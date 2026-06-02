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
    batch_mark_voucher_posted,
    count_voucher_records,
    get_rule,
    get_voucher_record,
    list_attachments,
    list_rules,
    list_voucher_records,
    update_voucher_record,
)
from helpers.voucher import _format_rules_for_frontend, post_voucher, reverse_voucher

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
        result = await post_voucher(voucher_id, user)
        if result["status"] == "posted":
            _append_posted_csv_from_record(result["record"])
            logger.info("Voucher %s posted via A2UI action by %s", voucher_id, user["username"])
        return JSONResponse({**result, "a2ui": {"messages": []}})

    if event_name == "filter_vouchers":
        status_filter = event_data.get("active", "")
        limit = event_data.get("limit", 50)
        offset = event_data.get("offset", 0)
        user_id = None if user["role"] == "admin" else user["id"]
        records = await list_voucher_records(user_id=user_id, status=status_filter or None, limit=limit, offset=offset)
        total = await count_voucher_records(user_id=user_id, status=status_filter or None)
        return JSONResponse({
            "status": "ok",
            "a2ui": {"messages": _voucher_list_to_a2ui(records, total, status_filter or None, limit=limit, offset=offset)},
        })

    if event_name == "search_vouchers":
        keyword = event_data.get("keyword", "").strip()
        status_filter = event_data.get("status", "")
        limit = event_data.get("limit", 50)
        offset = event_data.get("offset", 0)
        user_id = None if user["role"] == "admin" else user["id"]
        records = await list_voucher_records(
            user_id=user_id, status=status_filter or None, keyword=keyword or None, limit=limit, offset=offset,
        )
        total = await count_voucher_records(
            user_id=user_id, status=status_filter or None, keyword=keyword or None,
        )
        return JSONResponse({
            "status": "ok",
            "a2ui": {"messages": _voucher_list_to_a2ui(records, total, status_filter or None, keyword=keyword, limit=limit, offset=offset)},
        })

    if event_name == "batch_post_vouchers":
        voucher_ids = event_data.get("voucherIds", [])
        if not voucher_ids:
            return JSONResponse({"status": "error", "message": "请先勾选要过账的凭证"})
        # Ownership check for non-admin users
        if user["role"] != "admin":
            for vid in voucher_ids:
                rec = await get_voucher_record(vid)
                if rec and rec["user_id"] != user["id"]:
                    return JSONResponse({"status": "error", "message": f"无权操作凭证 {vid}"}, status_code=403)
        result = await batch_mark_voucher_posted(voucher_ids, user["id"])
        await add_audit_log(
            action="voucher.batch_post", user_id=user["id"], username=user["username"],
            details={"voucher_ids": voucher_ids, "posted": result["posted"], "failed": result["failed"]},
        )
        # Refresh the list
        user_id = None if user["role"] == "admin" else user["id"]
        records = await list_voucher_records(user_id=user_id, limit=50, offset=0)
        total = await count_voucher_records(user_id=user_id)
        return JSONResponse({
            "status": "ok",
            "message": f"批量过账完成：成功 {result['posted']} 个，失败 {result['failed']} 个",
            "a2ui": {"messages": _voucher_list_to_a2ui(records, total, None, limit=50, offset=0)},
        })

    if event_name == "reverse_voucher":
        voucher_id = event_data.get("voucherId", "")
        reason = event_data.get("reason", "").strip()
        if not voucher_id:
            return JSONResponse({"status": "error", "message": "缺少凭证ID"})
        if not reason:
            return JSONResponse({"status": "error", "message": "请输入冲销原因"})
        result = await reverse_voucher(voucher_id, user, reason)
        if result["status"] != "ok":
            code = 403 if result["status"] == "forbidden" else 200
            return JSONResponse(result, status_code=code)
        new_voucher_id = result["new_voucher_id"]
        reversal_record = await get_voucher_record(new_voucher_id)
        if reversal_record:
            reversal_front = json.loads(reversal_record.get("voucher_data") or "{}")
            reversal_front["status"] = "posted"
            attachments = await list_attachments(new_voucher_id)
            return JSONResponse({
                "status": "ok",
                "message": result["message"],
                "a2ui": {"messages": _voucher_to_a2ui(reversal_front, new_voucher_id, show_actions=True, attachments=attachments)},
            })
        return JSONResponse({"status": "ok", "message": result["message"]})

    if event_name == "export_voucher_pdf":
        voucher_id = event_data.get("voucherId", "")
        if not voucher_id:
            return JSONResponse({"status": "error", "message": "缺少凭证ID"})
        return JSONResponse({
            "status": "ok",
            "message": f"正在下载凭证 {voucher_id} 的 PDF...",
            "downloadUrl": f"/api/vouchers/{voucher_id}/pdf",
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
        if user["role"] != "admin" and db_record["user_id"] != user["id"]:
            return JSONResponse({"status": "error", "message": "无权查看此凭证"}, status_code=403)
        voucher_front = json.loads(db_record.get("voucher_data") or "{}")
        voucher_front["status"] = db_record.get("status", "draft")
        attachments = await list_attachments(voucher_id)
        resp = {
            "status": "ok",
            "a2ui": {"messages": _voucher_to_a2ui(voucher_front, voucher_id, show_actions=True, attachments=attachments)},
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
        if user["role"] != "admin" and record["user_id"] != user["id"]:
            return JSONResponse({"status": "error", "message": "无权编辑此凭证"}, status_code=403)
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
        voucher_front = dict(voucher_data)
        voucher_front["status"] = record.get("status", "draft")
        attachments = await list_attachments(voucher_id)
        return JSONResponse({
            "status": "ok", "message": f"凭证 {voucher_id} 已更新",
            "a2ui": {"messages": _voucher_to_a2ui(voucher_front, voucher_id, show_actions=True, attachments=attachments)},
        })

    return JSONResponse({"status": "unknown_event", "message": f"未处理的事件: {event_name}"})
