"""Auth and user management routes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.auth import _get_current_user, _require_admin
from database import (
    add_audit_log,
    authenticate_user,
    create_session_token,
    create_user,
    delete_session,
    delete_user,
    list_users,
    update_user,
)

router = APIRouter()


@router.post("/api/auth/login")
async def login(payload: dict, request: Request):
    username = payload.get("username", "").strip()
    password = payload.get("password", "")

    if not username or not password:
        return JSONResponse({"error": "请输入用户名和密码"}, status_code=400)

    user = await authenticate_user(username, password)
    if not user:
        await add_audit_log(action="login.failed", username=username, ip_address=request.client.host if request.client else None)
        return JSONResponse({"error": "用户名或密码错误"}, status_code=401)

    token = await create_session_token(user["id"])
    await add_audit_log(
        action="login.success",
        user_id=user["id"],
        username=user["username"],
        ip_address=request.client.host if request.client else None,
    )
    return JSONResponse({"token": token, "user": user})


@router.post("/api/auth/logout")
async def logout(request: Request):
    user = await _get_current_user(request)
    if user:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            await delete_session(token)
            await add_audit_log(action="logout", user_id=user["id"], username=user["username"])
    return JSONResponse({"status": "ok"})


@router.get("/api/auth/me")
async def get_me(request: Request):
    user = await _get_current_user(request)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    return JSONResponse({"user": user})


@router.get("/api/users")
async def api_list_users(request: Request):
    await _require_admin(request)
    users = await list_users()
    for u in users:
        u.pop("password_hash", None)
        u.pop("password_salt", None)
    return JSONResponse({"users": users})


@router.post("/api/users")
async def api_create_user(payload: dict, request: Request):
    admin = await _require_admin(request)
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    display_name = payload.get("display_name", "").strip()
    role = payload.get("role", "user")

    if not username or not password or not display_name:
        return JSONResponse({"error": "用户名、密码和显示名称不能为空"}, status_code=400)
    if role not in ("user", "admin"):
        return JSONResponse({"error": "无效的角色类型"}, status_code=400)

    try:
        user = await create_user(username, password, display_name, role)
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            return JSONResponse({"error": f"用户名「{username}」已存在"}, status_code=400)
        raise

    await add_audit_log(
        action="user.create", user_id=admin["id"], username=admin["username"],
        target_type="user", target_id=user["id"],
        details={"new_username": username, "new_role": role},
    )
    return JSONResponse({"user": user}, status_code=201)


@router.put("/api/users/{user_id}")
async def api_update_user(user_id: str, payload: dict, request: Request):
    admin = await _require_admin(request)
    if user_id == admin["id"] and payload.get("is_active") == 0:
        return JSONResponse({"error": "不能停用自己的账号"}, status_code=400)

    updated = await update_user(user_id, **payload)
    if not updated:
        return JSONResponse({"error": "用户不存在"}, status_code=404)

    await add_audit_log(
        action="user.update", user_id=admin["id"], username=admin["username"],
        target_type="user", target_id=user_id, details=payload,
    )
    return JSONResponse({"status": "ok"})


@router.delete("/api/users/{user_id}")
async def api_delete_user(user_id: str, request: Request):
    admin = await _require_admin(request)
    if user_id == admin["id"]:
        return JSONResponse({"error": "不能删除自己的账号"}, status_code=400)

    deleted = await delete_user(user_id)
    if not deleted:
        return JSONResponse({"error": "用户不存在"}, status_code=404)

    await add_audit_log(
        action="user.delete", user_id=admin["id"], username=admin["username"],
        target_type="user", target_id=user_id,
    )
    return JSONResponse({"status": "ok"})
