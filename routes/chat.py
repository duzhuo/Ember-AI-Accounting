"""Chat API route — natural language → LLM → voucher draft."""

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncGenerator
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
from helpers.constants import SUPPORTED_BUSINESS_TYPES
from helpers.sse import _sse, _extract_reply_delta
from helpers.voucher import _format_rules_for_frontend, _voucher_to_front

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────

_BIZ_TYPE_MAP: dict[str, str] = {
    "销售收入": "sales_revenue", "销售": "sales_revenue",
    "费用报销": "expense", "费用": "expense", "报销": "expense",
    "资产采购": "asset_purchase", "资产": "asset_purchase", "采购": "asset_purchase",
    "工资薪酬": "salary", "工资": "salary", "薪酬": "salary",
    "借款": "loan", "还款": "loan", "贷款": "loan",
}

_KEYWORD_FALLBACK_MAP: dict[str, str] = {
    "查看凭证": "voucher_query", "凭证记录": "voucher_query",
    "我的凭证": "voucher_query", "凭证列表": "voucher_query",
    "已生成凭证": "voucher_query", "看看凭证": "voucher_query",
    "查凭证": "voucher_query",
    "添加用户": "user_mgmt_create", "新建用户": "user_mgmt_create",
    "创建用户": "user_mgmt_create", "增加用户": "user_mgmt_create",
    "查询用户": "user_mgmt_list", "用户列表": "user_mgmt_list",
    "查看用户": "user_mgmt_list", "用户管理": "user_mgmt_list",
    "所有用户": "user_mgmt_list",
}

_RULE_MGMT_KEYWORDS = [
    "新增规则", "添加规则", "创建规则", "建立规则", "修改规则", "更新规则",
    "删除规则", "去掉规则", "新增凭证规则", "添加凭证规则", "创建凭证规则",
]

_RULE_MGMT_ACTION_KEYWORDS = ["新增", "添加", "创建", "建立", "修改", "更新", "删除", "去掉"]

_PASSWORD_KEYWORDS = ["密码", "password", "默认密码", "初始密码", "密码是什么", "密码多少"]


# ── Shared helpers ────────────────────────────────────────────────────────────


def _detect_business_type(text: str) -> str | None:
    """Detect business type from Chinese keywords in text."""
    for keyword, biz_type in _BIZ_TYPE_MAP.items():
        if keyword in text:
            return biz_type
    return None


async def _generate_session_title(session_id: str, session: dict, first_message: str) -> None:
    """Generate a short title for the session using LLM (background task)."""
    from agents.model_factory import create_chat_model
    try:
        model = create_chat_model()
        prompt = f"为以下对话生成一个简短的标题（不超过15个字，不要引号和标点）：\n用户：{first_message}"
        msg = UserMsg(name="user", content=prompt)
        result = await model.reply(msg)
        title = result.text.strip().strip('"').strip("'").strip("《》")
        if len(title) > 20:
            title = title[:20]
        session["title"] = title
        _save_session(session_id, session)
        logger.info("Generated session title: %s", title)
    except Exception as exc:
        logger.warning("Failed to generate session title: %s", exc)
        title = first_message.split("\n")[0][:15]
        session["title"] = title
        _save_session(session_id, session)


# ── Route handler ────────────────────────────────────────────────────────────


@router.post("/api/chat")
async def chat_endpoint(request: Request) -> StreamingResponse:
    # Support both JSON and FormData
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
    else:
        form = await request.form()
        payload = dict(form)
    return StreamingResponse(_chat_stream(payload, request), media_type="text/event-stream")


async def _chat_stream(payload: dict, request: Request) -> AsyncGenerator[bytes, None]:
    # ── Auth ──────────────────────────────────────────────────────────────
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
        yield _sse({"type": "result", "reply": "请描述一笔业务，比如「请客户吃饭花了1200元」，或上传一张发票。", "session_id": session_id})
        return

    await save_chat_message(
        session_id=chat_session_id, user_id=user["id"],
        role="user", content=message, message_type="chat",
    )

    if not session.get("title"):
        asyncio.create_task(_generate_session_title(session_id, session, message))

    recent_history = await list_chat_messages(session_id=chat_session_id, limit=200)
    history_for_llm = [{"role": m["role"], "content": m["content"]} for m in recent_history]

    # ── Intent parsing ────────────────────────────────────────────────────
    parse_result = _check_pending_action(recent_history, message)
    if parse_result is None:
        result_container: dict = {}
        async for chunk in _parse_intent(app, history_for_llm, message, result_container):
            yield chunk
        parse_result = result_container.get("parse_result")

    logger.info("NL parse result for '%s': %s", message[:60], parse_result)

    if parse_result is None:
        parse_result = {
            "intent": "chat",
            "reply": f"你好！我是 {AGENT_NAME}，{AGENT_CAPABILITIES.replace('、', '、')}。有什么可以帮你的吗？",
            "business_type": None,
            "transaction": None,
        }

    # ── Intent dispatch ───────────────────────────────────────────────────
    intent = parse_result.get("intent")

    if intent == "chat":
        async for chunk in _handle_chat_intent(
            parse_result, message, user, chat_session_id, session_id, recent_history,
        ):
            yield chunk
        return

    if intent == "rule_query":
        async for chunk in _handle_rule_query(parse_result, user, chat_session_id, session_id):
            yield chunk
        return

    if intent == "rule_mgmt":
        async for chunk in _handle_rule_mgmt(parse_result, user, chat_session_id, session_id):
            yield chunk
        return

    if intent == "voucher_query":
        async for chunk in _handle_voucher_query(parse_result, user, chat_session_id, session_id):
            yield chunk
        return

    if intent == "user_mgmt":
        async for chunk in _handle_user_mgmt(parse_result, user, chat_session_id, session_id, recent_history):
            yield chunk
        return

    # ── Voucher generation ────────────────────────────────────────────────
    async for chunk in _handle_voucher_generation(
        parse_result, user, chat_session_id, session_id, session, app,
    ):
        yield chunk


# ── Intent parsing helpers ────────────────────────────────────────────────────


def _check_pending_action(recent_history: list[dict], message: str) -> dict | None:
    """Check if the message continues a pending action from recent history."""
    if not recent_history:
        return None

    msg_lower = message.lower()

    # Check for pending rule_mgmt / rule_query from last assistant message
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
            detected_type = _detect_business_type(msg_lower)
            all_keywords = ["都看", "全部", "所有", "都要", "全看", "都查", "全部查看"]
            is_all = any(kw in msg_lower for kw in all_keywords)
            if detected_type or (is_all and pending == "rule_query"):
                logger.info("Pending action: continuing %s (type=%s) for '%s'", pending, detected_type, message[:60])
                return {
                    "intent": pending,
                    "action": pending_type if pending == "rule_mgmt" else None,
                    "rule_type": detected_type,
                    "reply": "",
                    "business_type": None,
                    "transaction": None,
                }

    # Check for pending user_mgmt_create from first message
    first_msg = recent_history[0]
    if first_msg["role"] == "assistant":
        pending_action = (first_msg.get("metadata") or {}).get("pending_action")
        if pending_action == "user_mgmt_create":
            return {
                "intent": "user_mgmt", "action": "create",
                "new_username": message.strip(), "new_display_name": None,
                "new_role": "user", "new_password": None,
                "reply": "", "business_type": None, "transaction": None,
            }

    return None


async def _parse_intent(app, history_for_llm: list[dict], message: str, result_container: dict) -> AsyncGenerator[bytes, None]:
    """Run LLM intent parsing. Yields SSE delta bytes for streaming; stores result in result_container."""
    for hist in history_for_llm[-200:]:
        role = hist.get("role", "user")
        content = hist.get("content", "")
        if role == "assistant":
            await app.state.intent_agent.observe(AssistantMsg(name="assistant", content=content))
        else:
            await app.state.intent_agent.observe(UserMsg(name="user", content=content))

    intent_msg = UserMsg(name="user", content=message)
    result_container["parse_result"] = None
    accumulated_text = ""
    reply_len = 0
    try:
        async for event_or_msg in app.state.intent_agent._reply(inputs=intent_msg):
            if isinstance(event_or_msg, Msg):
                result_container["parse_result"] = event_or_msg.metadata.get("parse_result") if event_or_msg.metadata else None
            elif hasattr(event_or_msg, "delta"):
                accumulated_text += event_or_msg.delta
                delta = _extract_reply_delta(accumulated_text, reply_len)
                if delta:
                    yield _sse({"type": "delta", "text": delta})
                    reply_len += len(delta)
    except Exception as llm_exc:
        logger.error("LLM call failed: %s", llm_exc)
        result_container["parse_result"] = None


# ── Intent handlers ───────────────────────────────────────────────────────────


async def _handle_chat_intent(
    parse_result: dict,
    message: str,
    user: dict,
    chat_session_id: str,
    session_id: str,
    recent_history: list[dict],
) -> AsyncGenerator[bytes, None]:
    """Handle chat intent with keyword fallback to other intents."""
    reply = parse_result["reply"]
    msg_lower = message.lower()

    # Try keyword fallbacks
    fallback_result = _try_keyword_fallback(msg_lower, reply, recent_history, message)
    if fallback_result:
        parse_result = fallback_result
        # Re-dispatch to the appropriate handler
        intent = parse_result["intent"]
        if intent == "voucher_query":
            async for chunk in _handle_voucher_query(parse_result, user, chat_session_id, session_id):
                yield chunk
            return
        if intent == "user_mgmt":
            async for chunk in _handle_user_mgmt(parse_result, user, chat_session_id, session_id, recent_history):
                yield chunk
            return
        if intent == "rule_mgmt":
            async for chunk in _handle_rule_mgmt(parse_result, user, chat_session_id, session_id):
                yield chunk
            return

    # Pure chat response
    await save_chat_message(
        session_id=chat_session_id, user_id=user["id"],
        role="assistant", content=reply, message_type="chat",
    )
    yield _sse({"type": "result", "reply": reply, "session_id": session_id})


def _try_keyword_fallback(
    msg_lower: str, reply: str, recent_history: list[dict], message: str,
) -> dict | None:
    """Try to match keywords in the message and return a parse_result for a different intent."""
    # Check for pending user_mgmt_create
    if recent_history:
        first_msg = recent_history[0]
        if first_msg["role"] == "assistant":
            pending = (first_msg.get("metadata") or {}).get("pending_action")
            if pending == "user_mgmt_create":
                logger.info("Pending action continuation: user_mgmt_create, username='%s'", message.strip())
                return {
                    "intent": "user_mgmt", "action": "create",
                    "new_username": message.strip(), "new_display_name": None,
                    "new_role": "user", "new_password": None,
                    "reply": reply, "business_type": None, "transaction": None,
                }

    # Voucher keywords
    voucher_kws = ["查看凭证", "凭证记录", "我的凭证", "凭证列表", "已生成凭证", "看看凭证", "查凭证"]
    if any(kw in msg_lower for kw in voucher_kws):
        logger.info("Keyword fallback: chat -> voucher_query for '%s'", message[:60])
        return {
            "intent": "voucher_query", "status": None,
            "reply": reply, "business_type": None, "transaction": None,
        }

    # User management keywords
    user_create_kws = ["添加用户", "新建用户", "创建用户", "增加用户"]
    if any(kw in msg_lower for kw in user_create_kws):
        logger.info("Keyword fallback: chat -> user_mgmt for '%s'", message[:60])
        return {
            "intent": "user_mgmt", "action": "create",
            "new_username": None, "new_display_name": None,
            "new_role": "user", "new_password": None,
            "reply": reply, "business_type": None, "transaction": None,
        }

    user_list_kws = ["查询用户", "用户列表", "查看用户", "用户管理", "所有用户"]
    if any(kw in msg_lower for kw in user_list_kws):
        logger.info("Keyword fallback: chat -> user_mgmt (list) for '%s'", message[:60])
        return {
            "intent": "user_mgmt", "action": "list",
            "new_username": None, "new_display_name": None,
            "new_role": "user", "new_password": None,
            "reply": reply, "business_type": None, "transaction": None,
        }

    # Password query keywords
    password_kws = ["密码", "password", "默认密码", "初始密码", "密码是什么", "密码多少"]
    if any(kw in msg_lower for kw in password_kws):
        logger.info("Keyword fallback: chat -> user_mgmt (query_password) for '%s'", message[:60])
        return _build_password_query_result(msg_lower, reply, recent_history)

    # Rule management keywords
    rule_kws = ["新增规则", "添加规则", "创建规则", "建立规则", "修改规则", "更新规则",
                "删除规则", "去掉规则", "新增凭证规则", "添加凭证规则", "创建凭证规则"]
    if any(kw in msg_lower for kw in rule_kws):
        detected_type = _detect_business_type(msg_lower)
        logger.info("Keyword fallback: chat -> rule_mgmt (type=%s) for '%s'", detected_type, message[:60])
        return {
            "intent": "rule_mgmt", "action": "create",
            "rule_type": detected_type, "reply": "",
            "business_type": None, "transaction": None,
        }

    # Generic rule action keywords + "规则"
    rule_action_kws = ["新增", "添加", "创建", "建立", "修改", "更新", "删除", "去掉"]
    if any(kw in msg_lower for kw in rule_action_kws) and "规则" in msg_lower:
        detected_type = _detect_business_type(msg_lower)
        logger.info("Keyword fallback: chat -> rule_mgmt (type=%s) for '%s'", detected_type, message[:60])
        return {
            "intent": "rule_mgmt", "action": "create",
            "rule_type": detected_type, "reply": "",
            "business_type": None, "transaction": None,
        }

    # Context fallback: pending rule_mgmt from history
    if recent_history:
        last_msg = recent_history[0]
        if last_msg["role"] == "assistant":
            pending = (last_msg.get("metadata") or {}).get("pending_action")
            if pending == "rule_mgmt":
                pending_type = (last_msg.get("metadata") or {}).get("pending_action_type", "create")
                matched_type = _detect_business_type(msg_lower)
                if matched_type:
                    logger.info("Context fallback: chat -> rule_mgmt (action=%s, type=%s) for '%s'", pending_type, matched_type, message[:60])
                    return {
                        "intent": "rule_mgmt", "action": pending_type,
                        "rule_type": matched_type, "reply": "",
                        "business_type": None, "transaction": None,
                    }

    return None


def _build_password_query_result(msg_lower: str, reply: str, recent_history: list[dict]) -> dict:
    """Build a parse_result for password query intent."""
    # Try to extract username from message or recent context
    # Note: list_users() is async so we can't call it here; return None username
    # and let the handler resolve it.
    return {
        "intent": "user_mgmt", "action": "query_password",
        "new_username": None, "new_display_name": None,
        "new_role": "user", "new_password": None,
        "reply": reply, "business_type": None, "transaction": None,
    }


async def _handle_rule_query(
    parse_result: dict,
    user: dict,
    chat_session_id: str,
    session_id: str,
) -> AsyncGenerator[bytes, None]:
    """Handle rule_query intent."""
    if user["role"] == "admin":
        reply = "抱歉，管理员没有查看凭证规则的权限。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
        return

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
        yield _sse({"type": "result", "reply": "加载凭证规则时出错，请稍后重试。", "session_id": session_id})
        return

    biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type) if rule_type else "全部"
    if not rules_list:
        if not reply:
            reply = f"暂无「{biz_label}」类型的凭证规则配置。"
        yield _sse({"type": "result", "reply": reply, "session_id": session_id, "view": "rules", "rules": [], "rule_type": rule_type, "a2ui": {"messages": _rules_to_a2ui([], rule_type)}})
        return

    if not reply or rule_type is None:
        reply = f"以下是「{biz_label}」类型的凭证规则，共 {len(rules_list)} 条："
    await save_chat_message(
        session_id=chat_session_id, user_id=user["id"],
        role="assistant", content=reply, message_type="chat",
        metadata={"rule_type": rule_type},
    )
    yield _sse({"type": "result", "reply": reply, "session_id": session_id, "rules": rules_list, "rule_type": rule_type, "view": "rules", "a2ui": {"messages": _rules_to_a2ui(rules_list, rule_type)}})


async def _handle_rule_mgmt(
    parse_result: dict,
    user: dict,
    chat_session_id: str,
    session_id: str,
) -> AsyncGenerator[bytes, None]:
    """Handle rule_mgmt intent."""
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
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
        return

    biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type)
    if user["role"] != "admin":
        reply = "抱歉，只有管理员才能管理凭证规则。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
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
        yield _sse({"type": "result", "reply": reply, "session_id": session_id, "view": "rules", "rule_mgmt": {"action": "create", "rule_type": rule_type}, "a2ui": {"messages": _rules_to_a2ui([], rule_type, {"action": "create", "rule_type": rule_type})}})
        return

    if action in ("update", "delete"):
        try:
            rules_list = await list_rules(business_type=rule_type)
            rules_list = _format_rules_for_frontend(rules_list)
        except Exception as exc:
            logger.error("Failed to load rules: %s", exc)
            yield _sse({"type": "result", "reply": "加载规则失败，请重试。", "session_id": session_id})
            return
        if not rules_list:
            reply = f"暂无「{biz_label}」类型的规则可供{action}。"
            await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
            yield _sse({"type": "result", "reply": reply, "session_id": session_id})
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
        yield _sse({"type": "result", "reply": reply, "session_id": session_id, "view": "rules", "rules": rules_list, "rule_mgmt": {"action": action, "rule_type": rule_type}, "a2ui": {"messages": _rules_to_a2ui(rules_list, rule_type, {"action": action, "rule_type": rule_type})}})


async def _handle_voucher_query(
    parse_result: dict,
    user: dict,
    chat_session_id: str,
    session_id: str,
) -> AsyncGenerator[bytes, None]:
    """Handle voucher_query intent."""
    if user["role"] == "admin":
        reply = "抱歉，管理员没有查看凭证的权限。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
        return

    try:
        status_filter = parse_result.get("status")
        reply = parse_result.get("reply", "")
        user_id = None if user["role"] == "reviewer" else user["id"]
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
            yield _sse({"type": "result", "reply": reply, "session_id": session_id, "view": "voucher_list", "view_data": {"vouchers": [], "total": 0, "status_filter": status_filter}, "a2ui": {"messages": _voucher_list_to_a2ui([], 0, status_filter)}})
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
        yield _sse({"type": "result", "reply": reply, "session_id": session_id, "view": "voucher_list", "view_data": {"vouchers": records, "total": total, "status_filter": status_filter}, "a2ui": {"messages": a2ui_msgs}})
    except Exception as exc:
        logger.error("voucher_query error: %s", exc, exc_info=True)
        yield _sse({"type": "result", "reply": "查询凭证时出错，请重试", "session_id": session_id})


async def _handle_user_mgmt(
    parse_result: dict,
    user: dict,
    chat_session_id: str,
    session_id: str,
    recent_history: list[dict],
) -> AsyncGenerator[bytes, None]:
    """Handle user_mgmt intent."""
    reply = parse_result.get("reply", "")
    if user["role"] != "admin":
        reply = "抱歉，只有管理员才能管理用户。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
        return

    action = parse_result.get("action")
    new_username = parse_result.get("new_username", "").strip() if parse_result.get("new_username") else ""

    if not action and not new_username:
        action = "list"

    if action == "list":
        async for chunk in _handle_user_list(reply, user, chat_session_id, session_id):
            yield chunk
        return

    if action == "query_password":
        async for chunk in _handle_query_password(parse_result, reply, user, chat_session_id, session_id, recent_history):
            yield chunk
        return

    if action == "create":
        async for chunk in _handle_user_create(parse_result, reply, user, chat_session_id, session_id):
            yield chunk
        return

    # Default: show help
    if not reply:
        reply = "请告诉我您要如何管理用户，例如「添加用户zhangsan」。"
    await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
    yield _sse({"type": "result", "reply": reply, "session_id": session_id})


async def _handle_user_list(
    reply: str,
    user: dict,
    chat_session_id: str,
    session_id: str,
) -> AsyncGenerator[bytes, None]:
    """List all users."""
    users = await list_users()
    for u in users:
        u.pop("password_hash", None)
        u.pop("password_salt", None)
    if not reply:
        reply = f"当前共有 {len(users)} 个用户。"
    await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
    yield _sse({"type": "result", "reply": reply, "session_id": session_id, "view": "user_list", "view_data": {"users": users}, "a2ui": {"messages": _users_to_a2ui(users)}})


async def _handle_query_password(
    parse_result: dict,
    reply: str,
    user: dict,
    chat_session_id: str,
    session_id: str,
    recent_history: list[dict],
) -> AsyncGenerator[bytes, None]:
    """Handle password query for a user."""
    target_username = parse_result.get("new_username", "").strip()
    if not target_username:
        if recent_history:
            for hist in recent_history[:5]:
                content = hist.get("content", "")
                all_users = await list_users()
                for u in all_users:
                    if u["username"] in content or u.get("display_name", "") in content:
                        target_username = u["username"]
                        break
                if target_username:
                    break

    if not target_username:
        reply = "请指定要查询密码的用户名。例如：「zhangsan的密码是什么」"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
        return

    all_users = await list_users()
    target_user = next((u for u in all_users if u["username"] == target_username), None)
    if not target_user:
        reply = f"未找到用户「{target_username}」。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
        return

    if target_user.get("must_change_password"):
        reply = f"用户「{target_username}」的密码是系统生成的默认密码，尚未修改。\n\n默认密码格式为：`User@` + 随机字符\n\n建议用户登录后立即修改密码。"
    else:
        reply = f"用户「{target_username}」已经修改过密码，无法查看原始密码。\n\n如果需要重置密码，请联系管理员。"

    await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
    yield _sse({"type": "result", "reply": reply, "session_id": session_id})


async def _handle_user_create(
    parse_result: dict,
    reply: str,
    user: dict,
    chat_session_id: str,
    session_id: str,
) -> AsyncGenerator[bytes, None]:
    """Handle user creation."""
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
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
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
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
        return

    await add_audit_log(
        action="user.create", user_id=user["id"], username=user["username"],
        target_type="user", target_id=created["id"],
        details={"new_username": new_username, "new_role": new_role},
    )
    role_label = {"admin": "管理员", "reviewer": "复核人", "user": "普通用户"}.get(new_role, "普通用户")
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
    yield _sse({"type": "result", "reply": reply, "session_id": session_id, "view": "user_list", "view_data": {"users": users}, "a2ui": {"messages": _users_to_a2ui(users)}})


async def _handle_voucher_generation(
    parse_result: dict,
    user: dict,
    chat_session_id: str,
    session_id: str,
    session: dict,
    app,
) -> AsyncGenerator[bytes, None]:
    """Handle voucher generation intent."""
    if user["role"] == "admin":
        reply = "抱歉，管理员没有生成凭证的权限。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
        return

    business_type = parse_result["business_type"]
    txn = parse_result["transaction"]

    if business_type not in SUPPORTED_BUSINESS_TYPES or txn is None:
        supported_list = "\n".join(f"  - {desc}" for desc in SUPPORTED_BUSINESS_TYPES.values())
        type_display = {"expense": "费用报销", "asset_purchase": "资产采购", "salary": "工资薪酬", "loan": "借款/还款", "other": "其他"}.get(business_type, business_type)
        reply = f"抱歉，当前系统暂不支持「{type_display}」类型的凭证生成。\n\n目前支持的凭证类型：\n{supported_list}\n\n请描述一笔支持的业务，或上传Excel附件。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="chat")
        yield _sse({"type": "result", "reply": reply, "session_id": session_id})
        return

    voucher_msg = UserMsg(name="user", content=json.dumps(asdict(txn), ensure_ascii=False, default=str), metadata={"transaction": txn, "business_type": business_type})
    voucher_result = await app.state.voucher_agent.reply(voucher_msg)
    voucher = voucher_result.metadata.get("voucher") if voucher_result.metadata else None
    if not voucher:
        yield _sse({"type": "result", "reply": "凭证生成失败，请重试。", "session_id": session_id})
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

    await add_audit_log(
        action="voucher.generate", user_id=user["id"], username=user["username"],
        target_type="voucher", target_id=actual_vid,
        details={"business_type": business_type},
    )

    reply = f"已为您生成凭证草稿（置信度 {voucher.confidence}）。"
    if voucher.warnings:
        reply += f" ⚠️ 注意：{'；'.join(voucher.warnings)}"
    await save_chat_message(
        session_id=chat_session_id, user_id=user["id"],
        role="assistant", content=reply, message_type="chat",
        metadata={"voucher_id": actual_vid},
    )
    yield _sse({"type": "result", "reply": reply, "session_id": session_id, "voucher": voucher_front, "view": "voucher", "a2ui": {"messages": _voucher_to_a2ui(voucher_front, actual_vid)}})
