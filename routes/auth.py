"""Auth and user management routes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.auth import _get_current_user, _require_auth, _require_admin
from database import (
    add_audit_log,
    authenticate_user,
    change_password,
    create_session_token,
    create_user,
    delete_session,
    delete_user,
    list_users,
    update_user,
    verify_password,
    get_db,
)

router = APIRouter()


# ── Auth routes ──────────────────────────────────────────────────────────────


@router.post("/api/auth/login")
async def login(payload: dict, request: Request):
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    ip = request.client.host if request.client else "unknown"

    if not username or not password:
        return JSONResponse({"error": "请输入用户名和密码"}, status_code=400)

    user = await authenticate_user(username, password)
    if not user:
        await add_audit_log(action="login.failed", username=username, ip_address=ip)
        return JSONResponse({"error": "用户名或密码错误"}, status_code=401)
    token = await create_session_token(user["id"])
    await add_audit_log(
        action="login.success", user_id=user["id"], username=user["username"], ip_address=ip,
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


@router.put("/api/auth/password")
async def api_change_password(payload: dict, request: Request):
    """Change current user's password."""
    user = await _require_auth(request)
    old_password = payload.get("old_password", "")
    new_password = payload.get("new_password", "")

    if not old_password or not new_password:
        return JSONResponse({"error": "请输入旧密码和新密码"}, status_code=400)
    if len(new_password) < 6:
        return JSONResponse({"error": "新密码至少6位"}, status_code=400)

    # Verify old password
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT password_hash, password_salt FROM users WHERE id = ?", (user["id"],)
        )
        row = await cursor.fetchone()
    finally:
        await db.close()

    if not row or not verify_password(old_password, row["password_hash"], row["password_salt"]):
        return JSONResponse({"error": "旧密码错误"}, status_code=400)

    ok = await change_password(user["id"], new_password)
    if not ok:
        return JSONResponse({"error": "修改失败"}, status_code=500)

    await add_audit_log(
        action="password.change", user_id=user["id"], username=user["username"],
    )
    return JSONResponse({"status": "ok", "message": "密码修改成功"})


# ── User management ──────────────────────────────────────────────────────────


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
    if len(password) < 6:
        return JSONResponse({"error": "密码至少6位"}, status_code=400)
    if role not in ("user", "admin", "reviewer"):
        return JSONResponse({"error": "无效的角色类型，可选：user、admin、reviewer"}, status_code=400)

    try:
        user = await create_user(username, password, display_name, role)
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            return JSONResponse({"error": f"用户名「{username}」已存在"}, status_code=400)
        logger.error("Failed to create user: %s", exc)
        return JSONResponse({"error": "创建用户失败"}, status_code=500)

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

    role = payload.get("role")
    if role and role not in ("user", "admin", "reviewer"):
        return JSONResponse({"error": "无效的角色类型，可选：user、admin、reviewer"}, status_code=400)

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


@router.post("/api/users/{user_id}/reset-password")
async def api_reset_password(user_id: str, request: Request):
    """Reset a user's password to a random default password."""
    import secrets
    admin = await _require_admin(request)

    # Get the target user
    users = await list_users()
    target_user = next((u for u in users if u["id"] == user_id), None)
    if not target_user:
        return JSONResponse({"error": "用户不存在"}, status_code=404)

    # Generate new password
    new_password = "User@" + secrets.token_hex(4)

    # Update password
    from database import change_password
    ok = await change_password(user_id, new_password)
    if not ok:
        return JSONResponse({"error": "重置密码失败"}, status_code=500)

    # Set must_change_password flag
    db = await get_db()
    try:
        await db.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()

    await add_audit_log(
        action="user.reset_password", user_id=admin["id"], username=admin["username"],
        target_type="user", target_id=user_id,
        details={"target_username": target_user["username"]},
    )

    return JSONResponse({
        "status": "ok",
        "message": f"已重置用户 {target_user['username']} 的密码",
        "new_password": new_password,
    })
