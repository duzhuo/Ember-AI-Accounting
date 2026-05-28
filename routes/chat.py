"""Chat API route — natural language → LLM → voucher draft."""

import json
import logging
import secrets
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agents.agent_config import AGENT_NAME, AGENT_CAPABILITIES
from agentscope.message import Msg, UserMsg, AssistantMsg

from helpers.a2ui import (
    BIZ_TYPE_LABELS,
    _rules_to_a2ui,
    _users_to_a2ui,
    _voucher_list_to_a2ui,
    _voucher_to_a2ui,
)
from helpers.auth import _is_session_expired, _get_session, _save_session, SESSION_TIMEOUT_HOURS
from database import (
    add_audit_log,
    create_user,
    get_voucher_record,
    list_chat_messages,
    list_rules,
    list_users,
    list_voucher_records,
    count_voucher_records,
    save_chat_message,
    save_voucher_record,
)
from helpers.sse import _sse, _extract_reply_delta
from helpers.voucher import _format_rules_for_frontend, _voucher_to_front

logger = logging.getLogger(__name__)

router = APIRouter()

SUPPORTED_BUSINESS_TYPES = {
    "sales_revenue": "销售收入（销售商品或提供服务产生的收入）",
    "expense": "费用报销（餐饮、差旅、办公等费用报销）",
}


@router.post("/api/chat")
async def chat_endpoint(payload: dict, request: Request):
    return StreamingResponse(_chat_stream(payload, request), media_type="text/event-stream")


async def _chat_stream(payload: dict, request: Request):
    try:
        from helpers.auth import _require_auth
        user = await _require_auth(request)
    except Exception:
        yield _sse({"type": "error", "reply": "登录已过期，请重新登录。"})
        return

    app = request.app

    message = payload.get("message", "").strip()
    session_id = payload.get("session_id")
    if session_id and await _is_session_expired(session_id):
        logger.info("Session %s expired (inactive > %dh), creating new session", session_id, SESSION_TIMEOUT_HOURS)
        session_id = None

    session_id, session = _get_session(session_id, user_id=user["id"])
    chat_session_id = session_id

    if not message:
        yield _sse({"type": "result", **{
            "reply": "请描述一笔业务，比如「请客户吃饭花了1200元」，或上传一张发票。",
            "session_id": session_id,
        }})
        return

    await save_chat_message(
        session_id=chat_session_id, user_id=user["id"],
        role="user", content=message, message_type="chat",
    )

    recent_history = await list_chat_messages(session_id=chat_session_id, limit=200)
    history_for_llm = [{"role": m["role"], "content": m["content"]} for m in recent_history]

    parse_result = None
    msg_lower_for_pending = message.lower()
    biz_type_map = {
        "销售收入": "sales_revenue", "销售": "sales_revenue",
        "费用报销": "expense", "费用": "expense", "报销": "expense",
        "资产采购": "asset_purchase", "资产": "asset_purchase", "采购": "asset_purchase",
        "工资薪酬": "salary", "工资": "salary", "薪酬": "salary",
        "借款": "loan", "还款": "loan", "贷款": "loan",
    }

    if recent_history:
        last_assistant_msg = None
        for m in recent_history:
            if m["role"] == "assistant":
                last_assistant_msg = m
                break
        if last_assistant_msg:
            last_meta = last_assistant_msg.get("metadata") or {}
            pending = last_meta.get("pending_action")
            if pending in ("rule_mgmt", "rule_query"):
                pending_type = last_meta.get("pending_action_type", "create")
                detected_type = None
                for kw, biz_type in biz_type_map.items():
                    if kw in msg_lower_for_pending:
                        detected_type = biz_type
                        break
                all_keywords = ["都看", "全部", "所有", "都要", "全看", "都查", "全部查看"]
                is_all = any(kw in msg_lower_for_pending for kw in all_keywords)
                if detected_type or (is_all and pending == "rule_query"):
                    parse_result = {
                        "intent": pending,
                        "action": pending_type if pending == "rule_mgmt" else None,
                        "rule_type": detected_type,
                        "reply": "",
                        "business_type": None,
                        "transaction": None,
                    }
                    logger.info("Pending action: continuing %s (type=%s) for '%s'", pending, detected_type, message[:60])

    if parse_result is None:
        for hist in history_for_llm[-200:]:
            role = hist.get("role", "user")
            content = hist.get("content", "")
            if role == "assistant":
                await app.state.intent_agent.observe(AssistantMsg(name="assistant", content=content))
            else:
                await app.state.intent_agent.observe(UserMsg(name="user", content=content))
        intent_msg = UserMsg(name="user", content=message)

        parse_result = None
        accumulated_text = ""
        reply_len = 0
        try:
            async for event_or_msg in app.state.intent_agent._reply(inputs=intent_msg):
                if isinstance(event_or_msg, Msg):
                    parse_result = event_or_msg.metadata.get("parse_result") if event_or_msg.metadata else None
                elif hasattr(event_or_msg, "delta"):
                    accumulated_text += event_or_msg.delta
                    delta = _extract_reply_delta(accumulated_text, reply_len)
                    if delta:
                        yield _sse({"type": "delta", "text": delta})
                        reply_len += len(delta)
        except Exception as llm_exc:
            logger.error("LLM call failed: %s", llm_exc)
            parse_result = None

    logger.info("NL parse result for '%s': %s", message[:60], parse_result)
    if parse_result is None:
        parse_result = {"intent": "chat", "reply": f"你好！我是 {AGENT_NAME}，{AGENT_CAPABILITIES.replace('、', '、')}。有什么可以帮你的吗？", "business_type": None, "transaction": None}

    # Handle chat intent
    if parse_result.get("intent") == "chat":
        reply = parse_result["reply"]
        msg_lower = message.lower()
        voucher_keywords = ["查看凭证", "凭证记录", "我的凭证", "凭证列表", "已生成凭证", "看看凭证", "查凭证"]
        user_mgmt_keywords = ["添加用户", "新建用户", "创建用户", "增加用户"]
        rule_mgmt_keywords = ["新增规则", "添加规则", "创建规则", "建立规则", "修改规则", "更新规则", "删除规则", "去掉规则",
                              "新增凭证规则", "添加凭证规则", "创建凭证规则"]
        rule_mgmt_action_keywords = ["新增", "添加", "创建", "建立", "修改", "更新", "删除", "去掉"]

        pending_action_from_history = None
        if recent_history:
            last_msg = recent_history[0]
            if last_msg["role"] == "assistant":
                last_meta = last_msg.get("metadata") or {}
                pending_action_from_history = last_meta.get("pending_action")

        if pending_action_from_history == "user_mgmt_create":
            parse_result = {"intent": "user_mgmt", "action": "create", "new_username": message.strip(), "new_display_name": None, "new_role": "user", "new_password": None, "reply": "", "business_type": None, "transaction": None}
            logger.info("Pending action continuation: user_mgmt_create, username='%s'", message.strip())
        elif any(kw in msg_lower for kw in voucher_keywords):
            parse_result = {"intent": "voucher_query", "status": None, "reply": reply, "business_type": None, "transaction": None}
            logger.info("Keyword fallback: chat → voucher_query for '%s'", message[:60])
        elif any(kw in msg_lower for kw in user_mgmt_keywords):
            parse_result = {"intent": "user_mgmt", "action": "create", "new_username": None, "new_display_name": None, "new_role": "user", "new_password": None, "reply": reply, "business_type": None, "transaction": None}
            logger.info("Keyword fallback: chat → user_mgmt for '%s'", message[:60])
        elif any(kw in msg_lower for kw in rule_mgmt_keywords):
            detected_type = None
            for kw, biz_type in biz_type_map.items():
                if kw in msg_lower:
                    detected_type = biz_type
                    break
            parse_result = {"intent": "rule_mgmt", "action": "create", "rule_type": detected_type, "reply": "", "business_type": None, "transaction": None}
            logger.info("Keyword fallback: chat → rule_mgmt (type=%s) for '%s'", detected_type, message[:60])
        elif any(kw in msg_lower for kw in rule_mgmt_action_keywords) and "规则" in msg_lower:
            detected_type = None
            for kw, biz_type in biz_type_map.items():
                if kw in msg_lower:
                    detected_type = biz_type
                    break
            parse_result = {"intent": "rule_mgmt", "action": "create", "rule_type": detected_type, "reply": "", "business_type": None, "transaction": None}
            logger.info("Keyword fallback: chat → rule_mgmt (type=%s) for '%s'", detected_type, message[:60])
        elif pending_action_from_history == "rule_mgmt":
            pending_type = (recent_history[0].get("metadata") or {}).get("pending_action_type", "create")
            matched_type = None
            for kw, biz_type in biz_type_map.items():
                if kw in msg_lower:
                    matched_type = biz_type
                    break
            if matched_type:
                parse_result = {"intent": "rule_mgmt", "action": pending_type, "rule_type": matched_type, "reply": "", "business_type": None, "transaction": None}
                logger.info("Context fallback: chat → rule_mgmt (action=%s, type=%s) for '%s'", pending_type, matched_type, message[:60])

        if parse_result.get("intent") == "chat":
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
            )
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
            return

    # Handle rule_query intent
    if parse_result.get("intent") == "rule_query":
        rule_type = parse_result.get("rule_type")
        reply = parse_result.get("reply", "")
        await add_audit_log(
            action="rule.view", user_id=user["id"], username=user["username"],
            target_type="rule", details={"rule_type": rule_type},
        )
        try:
            rules_list = await list_rules(business_type=rule_type)
            rules_list = _format_rules_for_frontend(rules_list)
        except Exception as exc:
            logger.error("Failed to load voucher rules: %s", exc)
            yield _sse({"type": "result", **{"reply": "加载凭证规则时出错，请稍后重试。", "session_id": session_id}})
            return

        biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type) if rule_type else "全部"
        if not rules_list:
            if not reply:
                reply = f"暂无「{biz_label}」类型的凭证规则配置。"
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id, "view": "rules", "rules": [], "rule_type": rule_type, "a2ui": {"messages": _rules_to_a2ui([], rule_type)}}})
            return
        if not reply or rule_type is None:
            reply = f"以下是「{biz_label}」类型的凭证规则，共 {len(rules_list)} 条："
        await save_chat_message(
            session_id=chat_session_id, user_id=user["id"],
            role="assistant", content=reply, message_type="chat",
            metadata={"rule_type": rule_type},
        )
        yield _sse({"type": "result", **{
            "reply": reply, "session_id": session_id,
            "rules": rules_list, "rule_type": rule_type, "view": "rules",
            "a2ui": {"messages": _rules_to_a2ui(rules_list, rule_type)},
        }})
        return

    # Handle rule_mgmt intent
    if parse_result.get("intent") == "rule_mgmt":
        action = parse_result.get("action", "create")
        rule_type = parse_result.get("rule_type")
        reply = parse_result.get("reply", "")

        if not rule_type:
            if not reply:
                reply = "请告诉我要管理哪种业务类型的规则？可选类型：\n• 销售收入\n• 费用报销\n• 资产采购\n• 工资薪酬\n• 借款/还款"
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
                metadata={"pending_action": "rule_mgmt", "pending_action_type": action},
            )
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
            return

        biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type)
        if user["role"] != "admin":
            reply = "抱歉，只有管理员才能管理凭证规则。"
            await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
            return

        if action == "create":
            if not reply:
                reply = f"好的，我来帮你创建「{biz_label}」类型的凭证规则。\n\n请在右侧弹出的表单中填写规则信息，或直接告诉我规则详情。"
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
                metadata={"action": "rule_mgmt_create", "rule_type": rule_type},
            )
            await add_audit_log(action="rule.create_start", user_id=user["id"], username=user["username"], target_type="rule", details={"rule_type": rule_type})
            yield _sse({"type": "result", **{
                "reply": reply, "session_id": session_id, "view": "rules",
                "rule_mgmt": {"action": "create", "rule_type": rule_type},
                "a2ui": {"messages": _rules_to_a2ui([], rule_type, {"action": "create", "rule_type": rule_type})},
            }})
            return

        if action in ("update", "delete"):
            try:
                rules_list = await list_rules(business_type=rule_type)
                rules_list = _format_rules_for_frontend(rules_list)
            except Exception as exc:
                logger.error("Failed to load rules: %s", exc)
                yield _sse({"type": "result", **{"reply": "加载规则失败，请重试。", "session_id": session_id}})
                return
            if not rules_list:
                reply = f"暂无「{biz_label}」类型的规则可供{action}。"
                await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
                yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
                return
            if not reply:
                verb = "修改" if action == "update" else "删除"
                reply = f"请选择要{verb}的「{biz_label}」规则："
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
                metadata={"action": f"rule_mgmt_{action}", "rule_type": rule_type},
            )
            await add_audit_log(action=f"rule.{action}_start", user_id=user["id"], username=user["username"], target_type="rule", details={"rule_type": rule_type})
            yield _sse({"type": "result", **{
                "reply": reply, "session_id": session_id, "view": "rules",
                "rules": rules_list,
                "rule_mgmt": {"action": action, "rule_type": rule_type},
                "a2ui": {"messages": _rules_to_a2ui(rules_list, rule_type, {"action": action, "rule_type": rule_type})},
            }})
            return

    # Handle voucher_query intent
    if parse_result.get("intent") == "voucher_query":
        try:
            status_filter = parse_result.get("status")
            reply = parse_result.get("reply", "")
            user_id = None if user["role"] == "admin" else user["id"]
            records = await list_voucher_records(user_id=user_id, status=status_filter, limit=50, offset=0)
            total = await count_voucher_records(user_id=user_id, status=status_filter)
            logger.info("voucher_query: user=%s status=%s records=%d total=%d", user["username"], status_filter, len(records), total)
            await add_audit_log(
                action="voucher.query", user_id=user["id"], username=user["username"],
                target_type="voucher", details={"status_filter": status_filter, "result_count": len(records)},
            )
            status_label = {"draft": "草稿", "posted": "已过账"}.get(status_filter, "全部")
            if not records:
                if not reply:
                    reply = f"暂无{status_label}状态的凭证记录。"
                await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
                yield _sse({"type": "result", **{
                    "reply": reply, "session_id": session_id, "view": "voucher_list",
                    "view_data": {"vouchers": [], "total": 0, "status_filter": status_filter},
                    "a2ui": {"messages": _voucher_list_to_a2ui([], 0, status_filter)},
                }})
                return
            if not reply:
                reply = f"共找到 {total} 条{status_label}凭证记录："
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
                metadata={"voucher_count": len(records)},
            )
            a2ui_msgs = _voucher_list_to_a2ui(records, total, status_filter)
            logger.info("voucher_query: a2ui messages generated, count=%d", len(a2ui_msgs))
            yield _sse({"type": "result", **{
                "reply": reply, "session_id": session_id, "view": "voucher_list",
                "view_data": {"vouchers": records, "total": total, "status_filter": status_filter},
                "a2ui": {"messages": a2ui_msgs},
            }})
            return
        except Exception as exc:
            logger.error("voucher_query error: %s", exc, exc_info=True)
            yield _sse({"type": "result", "reply": f"查询凭证时出错：{exc}", "session_id": session_id})
            return

    # Handle user_mgmt intent
    if parse_result.get("intent") == "user_mgmt":
        reply = parse_result.get("reply", "")
        if user["role"] != "admin":
            reply = "抱歉，只有管理员才能添加用户。"
            await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
            return
        action = parse_result.get("action", "create")
        if action == "create":
            new_username = parse_result.get("new_username", "").strip()
            new_display_name = parse_result.get("new_display_name") or new_username
            new_role = parse_result.get("new_role", "user")
            new_password = parse_result.get("new_password")
            if not new_username:
                if not reply:
                    reply = "请提供用户名。例如：「添加用户zhangsan，显示名称张三」"
                await save_chat_message(
                    session_id=chat_session_id, user_id=user["id"],
                    role="assistant", content=reply, message_type="chat",
                    metadata={"pending_action": "user_mgmt_create"},
                )
                yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
                return
            if not new_password:
                new_password = "User@" + secrets.token_hex(4)
            try:
                created = await create_user(new_username, new_password, new_display_name, new_role)
            except Exception as exc:
                if "UNIQUE constraint" in str(exc):
                    reply = f"用户名「{new_username}」已存在，请使用其他用户名。"
                else:
                    reply = f"创建用户失败：{exc}"
                await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
                yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
                return
            await add_audit_log(
                action="user.create", user_id=user["id"], username=user["username"],
                target_type="user", target_id=created["id"],
                details={"new_username": new_username, "new_role": new_role},
            )
            role_label = "管理员" if new_role == "admin" else "普通用户"
            if not reply:
                reply = f"已成功创建用户：\n• 用户名：{new_username}\n• 显示名称：{new_display_name}\n• 角色：{role_label}\n• 密码：{new_password}\n\n请通知用户尽快修改密码。"
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
                metadata={"created_user": new_username},
            )
            users = await list_users()
            for u in users:
                u.pop("password_hash", None)
                u.pop("password_salt", None)
            yield _sse({"type": "result", **{
                "reply": reply, "session_id": session_id, "view": "user_list",
                "view_data": {"users": users},
                "a2ui": {"messages": _users_to_a2ui(users)},
            }})
            return
        if not reply:
            reply = "请告诉我您要如何管理用户，例如「添加用户zhangsan」。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
        return

    # Voucher generation
    business_type = parse_result["business_type"]
    txn = parse_result["transaction"]

    if business_type not in SUPPORTED_BUSINESS_TYPES or txn is None:
        supported_list = "\n".join(f"  - {desc}" for desc in SUPPORTED_BUSINESS_TYPES.values())
        type_display = {"expense": "费用报销", "asset_purchase": "资产采购", "salary": "工资薪酬", "loan": "借款/还款", "other": "其他"}.get(business_type, business_type)
        reply = f"抱歉，当前系统暂不支持「{type_display}」类型的凭证生成。\n\n目前支持的凭证类型：\n{supported_list}\n\n请描述一笔支持的业务，或上传Excel附件。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
        return

    voucher_msg = UserMsg(name="user", content=json.dumps(asdict(txn), ensure_ascii=False, default=str), metadata={"transaction": txn, "business_type": business_type})
    voucher_result = await app.state.voucher_agent.reply(voucher_msg)
    voucher = voucher_result.metadata.get("voucher") if voucher_result.metadata else None
    if not voucher:
        yield _sse({"type": "result", **{"reply": "凭证生成失败，请重试。", "session_id": session_id}})
        return
    session["vouchers"].append(voucher)
    _save_session(session_id, session)

    voucher_front = _voucher_to_front(voucher)
    await save_voucher_record(
        voucher_id=voucher.voucher_id, user_id=user["id"],
        voucher_data=voucher_front, session_id=session_id,
        company_code=voucher.company_code, document_type=voucher.document_type,
        document_date=voucher.document_date, posting_date=voucher.posting_date,
        reference=voucher.reference, header_text=voucher.header_text,
        confidence=str(voucher.confidence), warnings=voucher.warnings,
    )
    await add_audit_log(
        action="voucher.generate", user_id=user["id"], username=user["username"],
        target_type="voucher", target_id=voucher.voucher_id,
        details={"business_type": business_type},
    )

    reply = f"已为您生成凭证草稿（置信度 {voucher.confidence}）。"
    if voucher.warnings:
        reply += f" ⚠️ 注意：{'；'.join(voucher.warnings)}"
    await save_chat_message(
        session_id=chat_session_id, user_id=user["id"],
        role="assistant", content=reply, message_type="chat",
        metadata={"voucher_id": voucher.voucher_id},
    )
    yield _sse({"type": "result", **{
        "reply": reply, "session_id": session_id,
        "voucher": voucher_front, "view": "voucher",
        "a2ui": {"messages": _voucher_to_a2ui(voucher_front, voucher.voucher_id)},
    }})
    return
