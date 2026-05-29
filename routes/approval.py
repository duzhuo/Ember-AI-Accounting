"""Approval flow routes — submit, approve, reject, list pending."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.auth import _require_auth
from database import (
    add_audit_log,
    approve_voucher,
    get_approval_record,
    get_voucher_record,
    list_pending_approvals,
    list_users,
    reject_voucher,
    submit_voucher_for_approval,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/vouchers/{voucher_id}/submit")
async def submit_for_approval(voucher_id: str, payload: dict, request: Request):
    """Submit a draft voucher for approval, or post directly if no_approval=True."""
    user = await _require_auth(request)
    approver_id = payload.get("approver_id")
    no_approval = payload.get("no_approval", False)

    record = await get_voucher_record(voucher_id)
    if not record:
        return JSONResponse({"error": f"凭证 {voucher_id} 不存在"}, status_code=404)
    if record.get("status") not in ("draft",):
        return JSONResponse({"error": f"凭证状态为 {record['status']}，无法提交审批"}, status_code=400)
    if user["role"] != "admin" and record["user_id"] != user["id"]:
        return JSONResponse({"error": "无权操作此凭证"}, status_code=403)

    if no_approval:
        from helpers.csv_export import _append_posted_csv_from_record
        from database import mark_voucher_posted
        _append_posted_csv_from_record(record)
        await mark_voucher_posted(voucher_id, user["id"])
        await add_audit_log(
            action="voucher.post", user_id=user["id"], username=user["username"],
            target_type="voucher", target_id=voucher_id, details={"direct": True},
        )
        return JSONResponse({"status": "posted", "message": f"凭证 {voucher_id} 已直接过账"})

    if not approver_id:
        return JSONResponse({"error": "请指定审批人"}, status_code=400)

    ok = await submit_voucher_for_approval(voucher_id, user["id"], approver_id)
    if not ok:
        return JSONResponse({"error": "提交审批失败，请检查凭证状态"}, status_code=400)

    await add_audit_log(
        action="voucher.submit_approval", user_id=user["id"], username=user["username"],
        target_type="voucher", target_id=voucher_id, details={"approver_id": approver_id},
    )
    return JSONResponse({"status": "pending_approval", "message": f"凭证 {voucher_id} 已提交审批"})


@router.post("/api/vouchers/{voucher_id}/approve")
async def approve(voucher_id: str, payload: dict, request: Request):
    """Approve a pending voucher and post it."""
    user = await _require_auth(request)

    record = await get_voucher_record(voucher_id)
    if not record:
        return JSONResponse({"error": f"凭证 {voucher_id} 不存在"}, status_code=404)
    if record.get("status") != "pending_approval":
        return JSONResponse({"error": "凭证不在待审批状态"}, status_code=400)

    approval = await get_approval_record(voucher_id)
    if not approval:
        return JSONResponse({"error": "未找到审批记录"}, status_code=404)
    if approval.get("approver_id") != user["id"] and user["role"] != "admin":
        return JSONResponse({"error": "您不是此凭证的指定审批人"}, status_code=403)

    comment = payload.get("comment", "")
    ok = await approve_voucher(voucher_id, user["id"], comment)
    if not ok:
        return JSONResponse({"error": "审批操作失败"}, status_code=500)

    updated = await get_voucher_record(voucher_id)
    if updated:
        from helpers.csv_export import _append_posted_csv_from_record
        _append_posted_csv_from_record(updated)

    await add_audit_log(
        action="voucher.approve", user_id=user["id"], username=user["username"],
        target_type="voucher", target_id=voucher_id, details={"comment": comment},
    )
    return JSONResponse({"status": "posted", "message": f"凭证 {voucher_id} 已审批通过并过账"})


@router.post("/api/vouchers/{voucher_id}/reject")
async def reject(voucher_id: str, payload: dict, request: Request):
    """Reject a pending voucher, returning it to draft."""
    user = await _require_auth(request)

    record = await get_voucher_record(voucher_id)
    if not record:
        return JSONResponse({"error": f"凭证 {voucher_id} 不存在"}, status_code=404)
    if record.get("status") != "pending_approval":
        return JSONResponse({"error": "凭证不在待审批状态"}, status_code=400)

    approval = await get_approval_record(voucher_id)
    if not approval:
        return JSONResponse({"error": "未找到审批记录"}, status_code=404)
    if approval.get("approver_id") != user["id"] and user["role"] != "admin":
        return JSONResponse({"error": "您不是此凭证的指定审批人"}, status_code=403)

    comment = payload.get("comment", "")
    if not comment:
        return JSONResponse({"error": "驳回时必须填写原因"}, status_code=400)

    ok = await reject_voucher(voucher_id, user["id"], comment)
    if not ok:
        return JSONResponse({"error": "驳回操作失败"}, status_code=500)

    await add_audit_log(
        action="voucher.reject", user_id=user["id"], username=user["username"],
        target_type="voucher", target_id=voucher_id, details={"comment": comment},
    )
    return JSONResponse({"status": "draft", "message": f"凭证 {voucher_id} 已驳回，请修改后重新提交"})


@router.get("/api/approvals/pending")
async def list_my_pending(request: Request):
    """List vouchers pending this user's approval."""
    user = await _require_auth(request)
    records = await list_pending_approvals(user["id"])
    return JSONResponse({"approvals": records, "total": len(records)})


@router.get("/api/users/approvers")
async def list_approvers(request: Request):
    """List all users who can serve as approvers (excluding self)."""
    user = await _require_auth(request)
    all_users = await list_users()
    approvers = [
        {"id": u["id"], "username": u["username"], "display_name": u.get("display_name", u["username"])}
        for u in all_users
        if u["id"] != user["id"]
    ]
    return JSONResponse({"approvers": approvers})
