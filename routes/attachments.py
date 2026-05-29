"""Attachment management routes."""

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from helpers.auth import _require_auth
from database import add_audit_log, delete_attachment, get_attachment, get_voucher_record, list_attachments, save_attachment

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/api/vouchers/{voucher_id}/attachments")
async def api_list_attachments(voucher_id: str, request: Request):
    await _require_auth(request)
    attachments = await list_attachments(voucher_id)
    return JSONResponse({"attachments": attachments})


MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


@router.post("/api/vouchers/{voucher_id}/attachments")
async def api_upload_attachment(voucher_id: str, request: Request, file: UploadFile = File(...)):
    user = await _require_auth(request)
    db_record = await get_voucher_record(voucher_id)
    if not db_record:
        return JSONResponse({"error": "凭证不存在"}, status_code=404)

    if file.size and file.size > MAX_FILE_SIZE:
        return JSONResponse({"error": f"文件大小超过限制（最大 20MB，当前 {file.size / 1024 / 1024:.1f}MB）"}, status_code=400)

    file_id = str(uuid.uuid4())[:8]
    suffix = Path(file.filename or "upload").suffix.lower()
    saved_path = UPLOAD_DIR / f"{file_id}{suffix}"
    with saved_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = saved_path.stat().st_size
    content_type = file.content_type or ""

    att_id = await save_attachment(
        voucher_id=voucher_id,
        filename=file.filename or f"{file_id}{suffix}",
        file_path=str(saved_path), file_size=file_size,
        content_type=content_type, uploaded_by=user["id"],
    )

    await add_audit_log(
        action="attachment.upload", user_id=user["id"], username=user["username"],
        target_type="attachment", target_id=att_id,
        details={"voucher_id": voucher_id, "filename": file.filename},
    )

    return JSONResponse({
        "status": "ok", "attachment_id": att_id,
        "filename": file.filename, "file_size": file_size,
    })


@router.delete("/api/attachments/{attachment_id}")
async def api_delete_attachment(attachment_id: str, request: Request):
    user = await _require_auth(request)
    # Get attachment record before deleting to clean up physical file
    att = await get_attachment(attachment_id)
    if not att:
        return JSONResponse({"error": "附件不存在"}, status_code=404)
    # Delete physical file
    if att.get("file_path"):
        Path(att["file_path"]).unlink(missing_ok=True)
    deleted = await delete_attachment(attachment_id)
    if not deleted:
        return JSONResponse({"error": "附件删除失败"}, status_code=500)
    await add_audit_log(
        action="attachment.delete", user_id=user["id"], username=user["username"],
        target_type="attachment", target_id=attachment_id,
    )
    return JSONResponse({"status": "ok"})
