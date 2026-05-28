"""Confirm voucher (post) route."""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.auth import _get_session, _require_auth, _save_session
from helpers.csv_export import POSTED_CSV, _append_posted_csv, _append_posted_csv_from_record
from database import add_audit_log, get_voucher_record, mark_voucher_posted, save_chat_message

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/confirm")
async def confirm_voucher(payload: dict, request: Request):
    user = await _require_auth(request)
    session_id = payload.get("session_id")
    voucher_id = payload.get("voucher_id")
    session_id, session = _get_session(session_id, user_id=user["id"])

    voucher = None
    for v in session.get("vouchers", []):
        if v.voucher_id == voucher_id:
            voucher = v
            break

    db_record = None
    if not voucher:
        db_record = await get_voucher_record(voucher_id)
        if not db_record:
            return JSONResponse({"status": "not_found", "message": f"凭证 {voucher_id} 不存在"})
        if db_record.get("status") == "posted":
            return JSONResponse({"status": "already_posted", "message": f"凭证 {voucher_id} 已经过账"})

    if voucher:
        _append_posted_csv(voucher)
    else:
        _append_posted_csv_from_record(db_record)

    if voucher:
        session["posted_voucher_ids"] = session.get("posted_voucher_ids", [])
        if voucher_id not in session["posted_voucher_ids"]:
            session["posted_voucher_ids"].append(voucher_id)
        _save_session(session_id, session)

    await mark_voucher_posted(voucher_id, user["id"])
    await add_audit_log(
        action="voucher.post", user_id=user["id"], username=user["username"],
        target_type="voucher", target_id=voucher_id, details={"session_id": session_id},
    )
    await save_chat_message(
        session_id=session_id or "unknown", user_id=user["id"],
        role="assistant", content=f"凭证 {voucher_id} 已确认记账",
        message_type="confirm", metadata={"voucher_id": voucher_id},
    )

    logger.info("Voucher %s posted by %s and saved to %s", voucher_id, user["username"], POSTED_CSV)

    return JSONResponse({
        "status": "posted",
        "message": f"凭证 {voucher_id} 已成功过账，保存至 {POSTED_CSV.name}",
    })
