"""Voucher list, detail, and update routes."""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.auth import _require_auth
from database import (
    add_audit_log,
    count_voucher_records,
    get_voucher_record,
    list_voucher_records,
    update_voucher_record,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/vouchers")
async def api_list_vouchers(request: Request, status: str | None = None, limit: int = 50, offset: int = 0):
    user = await _require_auth(request)
    user_id = None if user["role"] == "admin" else user["id"]
    records = await list_voucher_records(user_id=user_id, status=status, limit=limit, offset=offset)
    total = await count_voucher_records(user_id=user_id, status=status)
    return JSONResponse({"vouchers": records, "total": total, "limit": limit, "offset": offset})


@router.get("/api/vouchers/{voucher_id}")
async def api_get_voucher(voucher_id: str, request: Request):
    user = await _require_auth(request)
    record = await get_voucher_record(voucher_id)
    if not record:
        return JSONResponse({"error": "凭证不存在"}, status_code=404)
    if user["role"] != "admin" and record["user_id"] != user["id"]:
        return JSONResponse({"error": "无权查看此凭证"}, status_code=403)

    voucher_data = json.loads(record.get("voucher_data") or "{}")
    voucher_data["status"] = record.get("status", "draft")
    voucher_data["created_at"] = record.get("created_at")
    voucher_data["posted_at"] = record.get("posted_at")
    voucher_data["posted_by_name"] = record.get("posted_by_name")
    voucher_data["user_display_name"] = record.get("user_display_name")
    return JSONResponse({"voucher": voucher_data})


@router.put("/api/vouchers/{voucher_id}")
async def api_update_voucher(voucher_id: str, payload: dict, request: Request):
    user = await _require_auth(request)
    record = await get_voucher_record(voucher_id)
    if not record:
        return JSONResponse({"error": "凭证不存在"}, status_code=404)
    if record.get("status") == "posted":
        return JSONResponse({"error": "已过账的凭证不可编辑"}, status_code=400)

    voucher_data = payload.get("voucher_data", {})
    rows = voucher_data.get("rows", [])
    total_debit = sum(r.get("debit", 0) for r in rows)
    total_credit = sum(r.get("credit", 0) for r in rows)
    if abs(total_debit - total_credit) > 0.01:
        return JSONResponse({"error": f"借贷不平衡：借方 {total_debit:.2f}，贷方 {total_credit:.2f}"}, status_code=400)

    updated = await update_voucher_record(
        voucher_id,
        voucher_data=voucher_data,
        header_text=voucher_data.get("header_text", record.get("header_text", "")),
        document_date=voucher_data.get("document_date", record.get("document_date", "")),
        posting_date=voucher_data.get("posting_date", record.get("posting_date", "")),
    )
    if not updated:
        return JSONResponse({"error": "更新失败"}, status_code=500)

    await add_audit_log(
        action="voucher.edit", user_id=user["id"], username=user["username"],
        target_type="voucher", target_id=voucher_id,
    )
    return JSONResponse({"status": "ok", "message": f"凭证 {voucher_id} 已更新"})
