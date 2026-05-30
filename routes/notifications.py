"""Notification routes — list, unread count, mark read, delete."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.auth import _require_auth
from database import (
    count_unread_notifications,
    delete_notification,
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/notifications")
async def api_list_notifications(request: Request, unread_only: bool = False, limit: int = 50):
    """List notifications for the current user."""
    user = await _require_auth(request)
    items = await list_notifications(user["id"], limit=limit, unread_only=unread_only)
    return JSONResponse({"notifications": items, "total": len(items)})


@router.get("/api/notifications/unread-count")
async def api_unread_count(request: Request):
    """Get the count of unread notifications."""
    user = await _require_auth(request)
    count = await count_unread_notifications(user["id"])
    return JSONResponse({"count": count})


@router.post("/api/notifications/{notification_id}/read")
async def api_mark_read(notification_id: str, request: Request):
    """Mark a single notification as read."""
    user = await _require_auth(request)
    ok = await mark_notification_read(notification_id, user["id"])
    if not ok:
        return JSONResponse({"error": "通知不存在或已读"}, status_code=404)
    return JSONResponse({"status": "ok"})


@router.post("/api/notifications/read-all")
async def api_mark_all_read(request: Request):
    """Mark all notifications as read for the current user."""
    user = await _require_auth(request)
    count = await mark_all_notifications_read(user["id"])
    return JSONResponse({"status": "ok", "marked": count})


@router.delete("/api/notifications/{notification_id}")
async def api_delete_notification(notification_id: str, request: Request):
    """Delete a notification."""
    user = await _require_auth(request)
    ok = await delete_notification(notification_id, user["id"])
    if not ok:
        return JSONResponse({"error": "通知不存在"}, status_code=404)
    return JSONResponse({"status": "ok"})
