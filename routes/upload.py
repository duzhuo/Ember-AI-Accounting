"""File upload route — Excel/image/PDF → parse → LLM → voucher draft."""

import json
import logging
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from agentscope.message import UserMsg

from helpers.a2ui import BIZ_TYPE_LABELS, _voucher_to_a2ui
from helpers.auth import _get_session, _require_auth, _save_session
from helpers.constants import SUPPORTED_BUSINESS_TYPES
from database import (
    add_audit_log,
    get_voucher_record,
    list_attachments,
    list_chat_messages,
    save_attachment,
    save_chat_message,
    save_voucher_record,
)
from excel_loader import load_sales_transactions
from sap_exporter import export_sap_csv
from helpers.sse import _sse
from helpers.voucher import _voucher_to_front

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PDF_EXTENSIONS = {".pdf"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB



@router.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str | None = None,
):
    return StreamingResponse(_upload_stream(request, file, session_id), media_type="text/event-stream")


async def _upload_stream(request: Request, file: UploadFile, session_id: str | None):
    try:
        async for event in _upload_file_impl(request, file, session_id):
            yield event
    except Exception as exc:
        logger.error("Upload error: %s", exc, exc_info=True)
        yield _sse({"type": "error", "reply": "文件处理出错，请重试"})


async def _upload_file_impl(request: Request, file: UploadFile, session_id: str | None):
    app = request.app

    user = await _require_auth(request)
    chat_session_id = session_id or str(uuid.uuid4())
    session_id, session = _get_session(session_id, user_id=user["id"])

    await save_chat_message(
        session_id=chat_session_id, user_id=user["id"],
        role="user", content=f"上传文件: {file.filename}",
        message_type="upload", metadata={"filename": file.filename},
    )

    yield _sse({"type": "progress", "text": f"正在保存文件 {file.filename}..."})

    # File size check
    if file.size and file.size > MAX_FILE_SIZE:
        yield _sse({"type": "error", "reply": f"文件大小超过限制（最大 20MB，当前 {file.size / 1024 / 1024:.1f}MB）"})
        return

    file_id = str(uuid.uuid4())[:8]
    suffix = Path(file.filename or "upload.xlsx").suffix.lower()
    saved_path = UPLOAD_DIR / f"{file_id}{suffix}"
    with saved_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    file_info = {
        "name": file.filename,
        "size": file.size or saved_path.stat().st_size,
        "path": str(saved_path),
    }
    session["uploaded_files"].append(file_info)

    # Image / PDF path
    if suffix in IMAGE_EXTENSIONS or suffix in PDF_EXTENSIONS:
        file_type = "pdf" if suffix in PDF_EXTENSIONS else "image"
        source_label = "PDF" if suffix in PDF_EXTENSIONS else "图片"

        yield _sse({"type": "progress", "text": f"正在识别{source_label}内容..."})

        ocr_msg = UserMsg(name="user", content="", metadata={"file_path": str(saved_path), "file_type": file_type})
        ocr_result_msg = await app.state.ocr_agent.reply(ocr_msg)
        result = ocr_result_msg.metadata.get("ocr_result") if ocr_result_msg.metadata else None

        if result is None:
            reply = f"{source_label}识别失败，无法提取有效信息。请确保内容清晰且包含完整的发票/单据信息。"
            await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="upload")
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id, "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)}}})
            return

        business_type = result.get("business_type", "other")
        if business_type not in SUPPORTED_BUSINESS_TYPES:
            supported_list = "\n".join(f"  - {desc}" for desc in SUPPORTED_BUSINESS_TYPES.values())
            type_display = {"expense": "费用报销", "asset_purchase": "资产采购", "salary": "工资薪酬", "loan": "借款/还款", "other": "其他"}.get(business_type, business_type)
            reply = f"已识别单据类型为「{type_display}」，但当前系统暂不支持该类型的凭证生成。\n\n目前支持的凭证类型：\n{supported_list}"
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id, "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)}}})
            return

        txn = result.get("transaction")
        if txn is None:
            reply = f"{source_label}识别成功，但未能提取到完整的交易金额信息。请上传更清晰的文件。"
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id, "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)}}})
            return

        yield _sse({"type": "progress", "text": "正在生成凭证..."})

        voucher_msg = UserMsg(name="user", content=json.dumps(asdict(txn), ensure_ascii=False, default=str), metadata={"transaction": txn, "business_type": business_type})
        voucher_result = await app.state.voucher_agent.reply(voucher_msg)
        voucher = voucher_result.metadata.get("voucher") if voucher_result.metadata else None
        if not voucher:
            reply = "凭证生成失败，请重试。"
            await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="upload")
            yield _sse({"type": "error", "reply": reply})
            return
        session["vouchers"].append(voucher)
        _save_session(session_id, session)

        voucher_front = _voucher_to_front(voucher)
        _, actual_vid = await save_voucher_record(
            voucher_id=voucher.voucher_id, user_id=user["id"],
            voucher_data=voucher_front, session_id=session_id,
            company_code=voucher.company_code, document_type=voucher.document_type,
            document_date=voucher.document_date, posting_date=voucher.posting_date,
            reference=voucher.reference, header_text=voucher.header_text,
            confidence=str(voucher.confidence), warnings=voucher.warnings,
        )
        voucher_front["voucher_id"] = actual_vid
        await save_attachment(
            voucher_id=actual_vid,
            filename=file.filename or f"{file_id}{suffix}",
            file_path=str(saved_path), file_size=file_info["size"],
            content_type=file.content_type or "", uploaded_by=user["id"],
        )
        await add_audit_log(
            action="voucher.generate", user_id=user["id"], username=user["username"],
            target_type="voucher", target_id=actual_vid,
            details={"source": source_label, "filename": file.filename},
        )

        output_path = PROJECT_ROOT / "data" / "output" / f"sap_{file_id}.csv"
        export_sap_csv([voucher], output_path)

        biz_label = BIZ_TYPE_LABELS.get(business_type, business_type)
        reply = f"已从{source_label}中识别出1笔{biz_label}交易，生成了1张凭证草稿。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="upload", metadata={"voucher_id": actual_vid})

        attachments = await list_attachments(actual_vid)
        yield _sse({"type": "result", **{
            "reply": reply, "session_id": session_id,
            "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            "vouchers": [voucher_front],
            "a2ui": {"messages": _voucher_to_a2ui(voucher_front, actual_vid, attachments=attachments)},
        }})
        return

    # Excel path
    yield _sse({"type": "progress", "text": "正在解析Excel文件..."})
    try:
        transactions = load_sales_transactions(saved_path)
    except Exception as exc:
        logger.warning("Failed to parse uploaded file: %s", exc)
        yield _sse({"type": "error", "reply": "文件解析失败，请检查文件格式"})
        return

    vouchers = []
    voucher_id_map = {}  # original_vid -> actual_vid
    skipped = []
    failed = []
    total = len(transactions)
    for idx, txn in enumerate(transactions, 1):
        expected_voucher_id = f"VR-{txn.transaction_id}"
        existing = await get_voucher_record(expected_voucher_id)
        if existing:
            skipped.append(txn)
            continue

        yield _sse({"type": "progress", "text": f"正在生成凭证 ({idx}/{total})：{txn.customer_name}..."})

        voucher_msg = UserMsg(name="user", content=json.dumps(asdict(txn), ensure_ascii=False, default=str), metadata={"transaction": txn, "business_type": "sales_revenue"})
        voucher_result = await app.state.voucher_agent.reply(voucher_msg)
        voucher = voucher_result.metadata.get("voucher") if voucher_result.metadata else None
        if not voucher:
            failed.append(txn)
            continue
        session["vouchers"].append(voucher)
        vouchers.append(voucher)

        voucher_front = _voucher_to_front(voucher)
        _, actual_vid = await save_voucher_record(
            voucher_id=voucher.voucher_id, user_id=user["id"],
            voucher_data=voucher_front, session_id=session_id,
            company_code=voucher.company_code, document_type=voucher.document_type,
            document_date=voucher.document_date, posting_date=voucher.posting_date,
            reference=voucher.reference, header_text=voucher.header_text,
            confidence=str(voucher.confidence), warnings=voucher.warnings,
        )
        voucher_id_map[voucher.voucher_id] = actual_vid
        voucher_front["voucher_id"] = actual_vid
        await add_audit_log(
            action="voucher.generate", user_id=user["id"], username=user["username"],
            target_type="voucher", target_id=actual_vid,
            details={"source": "excel", "filename": file.filename},
        )

    _save_session(session_id, session)

    reply = f"已解析 {len(transactions)} 笔交易，生成了 {len(vouchers)} 张凭证草稿。"
    if skipped:
        details = "、".join(f"{txn.transaction_id}({txn.customer_name})" for txn in skipped)
        reply += f"\n跳过 {len(skipped)} 笔已存在的凭证：{details}"
    if failed:
        details = "、".join(f"{txn.transaction_id}({txn.customer_name})" for txn in failed)
        reply += f"\n{len(failed)} 笔生成失败：{details}"

    output_path = PROJECT_ROOT / "data" / "output" / f"sap_{file_id}.csv"
    export_sap_csv(vouchers, output_path)

    await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="upload", metadata={"voucher_count": len(vouchers)})

    voucher_fronts = []
    for v in vouchers:
        vf = _voucher_to_front(v)
        actual = voucher_id_map.get(v.voucher_id, v.voucher_id)
        vf["voucher_id"] = actual
        voucher_fronts.append(vf)
    last_voucher_front = voucher_fronts[-1] if voucher_fronts else {}
    last_voucher_id = last_voucher_front.get("voucher_id", "") if voucher_fronts else ""
    yield _sse({"type": "result", **{
        "reply": reply, "session_id": session_id,
        "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
        "vouchers": voucher_fronts,
        "a2ui": {"messages": _voucher_to_a2ui(last_voucher_front, last_voucher_id)} if voucher_fronts else {},
    }})
