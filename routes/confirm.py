"""Confirm voucher (post) route."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.auth import _get_session, _require_auth, _save_session
from helpers.csv_export import POSTED_CSV, _append_posted_csv, _append_posted_csv_from_record
from helpers.voucher import post_voucher
from database import add_audit_log, batch_mark_voucher_posted, get_voucher_record, save_chat_message

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

    if not voucher:
        result = await post_voucher(voucher_id, user)
        if result["status"] not in ("posted",):
            code = 403 if result["status"] == "forbidden" else (400 if "不平衡" in result.get("message", "") else 200)
            return JSONResponse(result, status_code=code)
        db_record = result["record"]
        _append_posted_csv_from_record(db_record)
    else:
        result = await post_voucher(voucher_id, user)
        if result["status"] not in ("posted",):
            code = 403 if result["status"] == "forbidden" else (400 if "不平衡" in result.get("message", "") else 200)
            return JSONResponse(result, status_code=code)
        _append_posted_csv(voucher)
        session["posted_voucher_ids"] = session.get("posted_voucher_ids", [])
        if voucher_id not in session["posted_voucher_ids"]:
            session["posted_voucher_ids"].append(voucher_id)
        _save_session(session_id, session)

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


@router.post("/api/confirm/batch")
async def confirm_voucher_batch(payload: dict, request: Request):
    """Batch post multiple draft vouchers."""
    user = await _require_auth(request)
    voucher_ids = payload.get("voucher_ids", [])
    if not voucher_ids:
        return JSONResponse({"error": "请选择至少一个凭证"}, status_code=400)
    if len(voucher_ids) > 100:
        return JSONResponse({"error": "单次最多批量过账100个凭证"}, status_code=400)

    # Ownership check for non-admin users
    if user["role"] != "admin":
        for vid in voucher_ids:
            rec = await get_voucher_record(vid)
            if rec and rec["user_id"] != user["id"]:
                return JSONResponse({"error": f"无权操作凭证 {vid}"}, status_code=403)

    result = await batch_mark_voucher_posted(voucher_ids, user["id"])

    await add_audit_log(
        action="voucher.batch_post", user_id=user["id"], username=user["username"],
        details={"voucher_ids": voucher_ids, "posted": result["posted"], "failed": result["failed"]},
    )

    return JSONResponse({
        "status": "ok",
        "posted": result["posted"],
        "failed": result["failed"],
        "errors": result["errors"],
        "message": f"批量过账完成：成功 {result['posted']} 个，失败 {result['failed']} 个",
    })
