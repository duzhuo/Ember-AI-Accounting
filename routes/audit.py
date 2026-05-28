"""Audit logs and chat history routes."""

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
async def api_my_chat_history(request: Request, limit: int = 50):
    user = await _require_auth(request)
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
