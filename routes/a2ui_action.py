"""A2UI action handler route."""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.a2ui import _rule_detail_to_a2ui, _rules_to_a2ui, _voucher_list_to_a2ui, _voucher_to_a2ui
from helpers.auth import _require_auth
from helpers.csv_export import _append_posted_csv_from_record
from database import (
    add_audit_log,
    count_voucher_records,
    get_rule,
    get_voucher_record,
    list_attachments,
    list_rules,
    list_voucher_records,
    mark_voucher_posted,
    update_voucher_record,
)
from helpers.voucher import _format_rules_for_frontend

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/a2ui-action")
async def handle_a2ui_action(request: Request):
    user = await _require_auth(request)
    body = await request.json()
    event_name = body.get("event", "")
    event_data = body.get("data", {})

    logger.info("A2UI action: event=%s data=%s user=%s", event_name, event_data, user["username"])

    if event_name == "confirm_voucher":
        voucher_id = event_data.get("voucherId", "")
        if not voucher_id:
            return JSONResponse({"status": "error", "message": "缺少凭证ID"})
        db_record = await get_voucher_record(voucher_id)
        if not db_record:
            return JSONResponse({"status": "error", "message": f"凭证 {voucher_id} 不存在"})
        if db_record.get("status") == "posted":
            return JSONResponse({"status": "already_posted", "message": f"凭证 {voucher_id} 已经过账", "a2ui": {"messages": []}})
        _append_posted_csv_from_record(db_record)
        await mark_voucher_posted(voucher_id, user["id"])
        await add_audit_log(
            action="voucher.post", user_id=user["id"], username=user["username"],
            target_type="voucher", target_id=voucher_id, details={"source": "a2ui_action"},
        )
        logger.info("Voucher %s posted via A2UI action by %s", voucher_id, user["username"])
        return JSONResponse({"status": "posted", "message": f"凭证 {voucher_id} 已成功过账", "a2ui": {"messages": []}})

    if event_name == "filter_vouchers":
        status_filter = event_data.get("active", "")
        user_id = None if user["role"] == "admin" else user["id"]
        records = await list_voucher_records(user_id=user_id, status=status_filter or None, limit=50, offset=0)
        total = await count_voucher_records(user_id=user_id, status=status_filter or None)
        return JSONResponse({
            "status": "ok",
            "a2ui": {"messages": _voucher_list_to_a2ui(records, total, status_filter or None)},
        })

    if event_name == "create_rule":
        rule_type = event_data.get("ruleType", "")
        return JSONResponse({
            "status": "ok", "message": f"创建规则: {rule_type}",
            "a2ui": {"messages": _rules_to_a2ui([], rule_type, {"action": "create", "rule_type": rule_type})},
        })

    if event_name == "view_rule_detail":
        rule_code = event_data.get("ruleCode", "")
        rule = await get_rule(rule_code)
        if not rule:
            return JSONResponse({"status": "error", "message": f"规则 {rule_code} 不存在"})
        return JSONResponse({"status": "ok", "a2ui": {"messages": _rule_detail_to_a2ui(rule)}})

    if event_name == "back_to_rules":
        rules_list = await list_rules()
        rules_list = _format_rules_for_frontend(rules_list)
        return JSONResponse({"status": "ok", "a2ui": {"messages": _rules_to_a2ui(rules_list, None)}})

    if event_name in ("view_voucher_detail", "edit_voucher"):
        voucher_id = event_data.get("voucherId", "")
        db_record = await get_voucher_record(voucher_id)
        if not db_record:
            return JSONResponse({"status": "error", "message": f"凭证 {voucher_id} 不存在"})
        voucher_front = json.loads(db_record.get("voucher_data") or "{}")
        voucher_front["status"] = db_record.get("status", "draft")
        show_actions = event_name == "edit_voucher"
        attachments = await list_attachments(voucher_id)
        resp = {
            "status": "ok",
            "a2ui": {"messages": _voucher_to_a2ui(voucher_front, voucher_id, show_actions=show_actions, attachments=attachments)},
        }
        if event_name == "edit_voucher":
            resp["voucher"] = voucher_front
        return JSONResponse(resp)

    if event_name == "upload_attachment":
        voucher_id = event_data.get("voucherId", "")
        return JSONResponse({
            "status": "open_file_picker", "voucherId": voucher_id,
            "accept": ".png,.jpg,.jpeg,.gif,.webp,.bmp,.pdf,.xlsx,.xls,.csv",
        })

    if event_name == "save_voucher_edit":
        voucher_id = event_data.get("voucherId", "")
        voucher_data = event_data.get("voucherData", {})
        if not voucher_id:
            return JSONResponse({"status": "error", "message": "缺少凭证ID"})
        record = await get_voucher_record(voucher_id)
        if not record:
            return JSONResponse({"status": "error", "message": f"凭证 {voucher_id} 不存在"})
        if record.get("status") == "posted":
            return JSONResponse({"status": "error", "message": "已过账凭证不可编辑"})
        rows = voucher_data.get("rows", [])
        total_debit = sum(r.get("debit", 0) for r in rows)
        total_credit = sum(r.get("credit", 0) for r in rows)
        if abs(total_debit - total_credit) > 0.01:
            return JSONResponse({"status": "error", "message": f"借贷不平衡：借方 {total_debit:.2f}，贷方 {total_credit:.2f}"})
        updated = await update_voucher_record(
            voucher_id, voucher_data=voucher_data,
            header_text=voucher_data.get("header_text", ""),
            document_date=voucher_data.get("document_date", ""),
            posting_date=voucher_data.get("posting_date", ""),
        )
        if not updated:
            return JSONResponse({"status": "error", "message": "更新失败"})
        await add_audit_log(
            action="voucher.edit", user_id=user["id"], username=user["username"],
            target_type="voucher", target_id=voucher_id,
        )
        voucher_front = json.loads(record.get("voucher_data") or "{}")
        voucher_front["status"] = record.get("status", "draft")
        attachments = await list_attachments(voucher_id)
        return JSONResponse({
            "status": "ok", "message": f"凭证 {voucher_id} 已更新",
            "a2ui": {"messages": _voucher_to_a2ui(voucher_front, voucher_id, show_actions=True, attachments=attachments)},
        })

    return JSONResponse({"status": "unknown_event", "message": f"未处理的事件: {event_name}"})
