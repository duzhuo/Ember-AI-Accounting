"""Voucher rules CRUD routes."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.auth import _require_admin, _require_auth
from database import (
    add_audit_log,
    create_rule,
    delete_rule as db_delete_rule,
    get_rule,
    list_rules,
    update_rule as db_update_rule,
)
from helpers.voucher import _format_rules_for_frontend

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/rules")
async def get_voucher_rules(request: Request, business_type: str | None = None):
    await _require_auth(request)
    try:
        rules = await list_rules(business_type=business_type)
        rules = _format_rules_for_frontend(rules)
    except Exception as exc:
        logger.error("Failed to load voucher rules: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    total_lines = sum(len(r.get("lines", [])) for r in rules)
    return JSONResponse({"rules": rules, "total_rules": len(rules), "total_lines": total_lines})


@router.post("/api/rules")
async def api_create_rule(payload: dict, request: Request):
    admin = await _require_admin(request)
    rule_code = (payload.get("rule_code") or "").strip()
    if not rule_code:
        return JSONResponse({"error": "规则代码不能为空"}, status_code=400)
    existing = await get_rule(rule_code)
    if existing:
        return JSONResponse({"error": f"规则代码「{rule_code}」已存在"}, status_code=400)
    lines = payload.get("lines", [])
    if not lines:
        return JSONResponse({"error": "至少需要一条分录行"}, status_code=400)
    try:
        rule = await create_rule(
            rule_code=rule_code,
            business_type=payload.get("business_type", "sales_revenue"),
            product_type=payload.get("product_type", "*"),
            tax_rate=payload.get("tax_rate", "*"),
            document_type=payload.get("document_type", "DR"),
            lines=lines,
        )
    except Exception as exc:
        logger.error("Failed to create rule: %s", exc)
        return JSONResponse({"error": "创建规则失败"}, status_code=500)
    await add_audit_log(
        action="rule.create", user_id=admin["id"], username=admin["username"],
        target_type="rule", target_id=rule_code,
    )
    formatted = _format_rules_for_frontend([rule])
    return JSONResponse({"rule": formatted[0] if formatted else {}}, status_code=201)


@router.put("/api/rules/{rule_code}")
async def api_update_rule(rule_code: str, payload: dict, request: Request):
    admin = await _require_admin(request)
    existing = await get_rule(rule_code)
    if not existing:
        return JSONResponse({"error": "规则不存在"}, status_code=404)
    lines = payload.get("lines")
    try:
        updated = await db_update_rule(
            rule_code,
            lines=lines,
            business_type=payload.get("business_type"),
            product_type=payload.get("product_type"),
            tax_rate=payload.get("tax_rate"),
            document_type=payload.get("document_type"),
        )
    except Exception as exc:
        logger.error("Failed to update rule: %s", exc)
        return JSONResponse({"error": "更新规则失败"}, status_code=500)
    if not updated:
        return JSONResponse({"error": "更新失败"}, status_code=500)
    await add_audit_log(
        action="rule.update", user_id=admin["id"], username=admin["username"],
        target_type="rule", target_id=rule_code,
    )
    rule = await get_rule(rule_code)
    formatted = _format_rules_for_frontend([rule])
    return JSONResponse({"rule": formatted[0] if formatted else {}})


@router.delete("/api/rules/{rule_code}")
async def api_delete_rule(rule_code: str, request: Request):
    admin = await _require_admin(request)
    existing = await get_rule(rule_code)
    if not existing:
        return JSONResponse({"error": "规则不存在"}, status_code=404)
    deleted = await db_delete_rule(rule_code)
    if not deleted:
        return JSONResponse({"error": "删除失败"}, status_code=500)
    await add_audit_log(
        action="rule.delete", user_id=admin["id"], username=admin["username"],
        target_type="rule", target_id=rule_code,
    )
    return JSONResponse({"status": "ok"})
