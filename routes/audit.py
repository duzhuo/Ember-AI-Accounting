"""Audit logs and chat history routes."""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.auth import _require_admin, _require_auth
from database import get_db, list_audit_logs, list_chat_messages

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/audit-logs")
async def api_list_audit_logs(request: Request, action: str | None = None, limit: int = 100, offset: int = 0):
    await _require_admin(request)
    logs = await list_audit_logs(action=action, limit=limit, offset=offset)
    return JSONResponse({"logs": logs, "limit": limit, "offset": offset})


@router.get("/api/chat-history")
async def api_chat_history(request: Request, limit: int = 100, offset: int = 0):
    await _require_admin(request)
    messages = await list_chat_messages(limit=limit, offset=offset)
    return JSONResponse({"messages": messages, "limit": limit, "offset": offset})


@router.get("/api/my-chat-history")
async def api_my_chat_history(request: Request, session_id: str | None = None, limit: int = 50):
    user = await _require_auth(request)
    if not session_id:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT session_id FROM chat_messages WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
                (user["id"],),
            )
            row = await cursor.fetchone()
        finally:
            await db.close()
        if not row:
            return JSONResponse({"session_id": None, "messages": []})
        session_id = row["session_id"]

    messages = await list_chat_messages(session_id=session_id, user_id=user["id"], limit=limit)
    messages.reverse()
    return JSONResponse({"session_id": session_id, "messages": messages})


@router.get("/api/my-sessions")
async def api_my_sessions(request: Request, limit: int = 20):
    """List the current user's chat sessions, newest first."""
    from helpers.auth import _load_session, _save_session
    user = await _require_auth(request)
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT session_id, COUNT(*) as message_count,
                      MAX(created_at) as last_message_at,
                      MIN(CASE WHEN role='user' THEN content END) as preview
               FROM chat_messages WHERE user_id = ?
               GROUP BY session_id ORDER BY last_message_at DESC LIMIT ?""",
            (user["id"], limit),
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()

    sessions = []
    sessions_to_title = []
    for r in rows:
        sid = r["session_id"]
        session_data = _load_session(sid, user["id"])
        title = (session_data or {}).get("title")
        if not title:
            fallback = r["preview"] or "(无内容)"
            fallback = fallback.split("\n")[0].strip()
            if fallback.startswith("{") or fallback.startswith("["):
                fallback = "(文件上传)"
            title = fallback[:20] + "..." if len(fallback) > 20 else fallback
            # Collect sessions that need LLM-generated titles
            if r["preview"] and not r["preview"].startswith("{") and not r["preview"].startswith("["):
                sessions_to_title.append((sid, r["preview"], session_data or {"user_id": user["id"]}))
        else:
            if len(title) > 20:
                title = title[:20] + "..."
        sessions.append({
            "session_id": sid,
            "message_count": r["message_count"],
            "last_message_at": r["last_message_at"],
            "title": title,
        })

    # Generate LLM titles in background for sessions that don't have one
    if sessions_to_title:
        asyncio.create_task(_batch_generate_titles(sessions_to_title))

    return JSONResponse({"sessions": sessions})


async def _batch_generate_titles(sessions: list):
    """Generate LLM titles for sessions that don't have one yet."""
    from agents.model_factory import create_chat_model
    from agentscope.message import UserMsg
    try:
        model = create_chat_model()
        for sid, first_msg, session_data in sessions:
            try:
                prompt = f"为以下对话生成一个简短的标题（不超过15个字，不要引号和标点）：\n用户：{first_msg}"
                msg = UserMsg(name="user", content=prompt)
                result = await model.reply(msg)
                title = result.text.strip().strip('"').strip("'").strip("《》")
                if len(title) > 20:
                    title = title[:20]
                session_data["title"] = title
                _save_session(sid, session_data)
                logger.info("Generated title for session %s: %s", sid[:8], title)
            except Exception as exc:
                logger.warning("Failed to generate title for session %s: %s", sid[:8], exc)
    except Exception as exc:
        logger.warning("Failed to initialize model for title generation: %s", exc)
