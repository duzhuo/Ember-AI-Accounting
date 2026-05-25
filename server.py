"""FastAPI backend for the AI Accounting Voucher web app.

Provides REST APIs for:
  - Auth: login, logout, user management
  - Chat: natural language → LLM → voucher draft
  - File upload: Excel/image/PDF → parse → LLM → voucher draft
  - Confirm: mark voucher as posted
  - Vouchers: list/query voucher history
  - Audit: operation and conversation logging

Run:
    source .venv/bin/activate
    python server.py
"""

import asyncio
import json
import logging
import shutil
import uuid
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from prompts import NL_PARSE_SYSTEM_PROMPT, IMAGE_PARSE_SYSTEM_PROMPT

from database import (
    add_audit_log,
    authenticate_user,
    create_rule,
    create_session_token,
    create_user,
    delete_rule as db_delete_rule,
    delete_session,
    delete_user,
    get_rule,
    get_user_by_token,
    init_db,
    list_audit_logs,
    list_chat_messages,
    get_voucher_record,
    list_rules,
    list_users,
    list_voucher_records,
    mark_voucher_posted,
    migrate_rules_from_excel,
    save_chat_message,
    save_voucher_record,
    update_rule as db_update_rule,
    update_user,
    count_voucher_records,
)
from excel_loader import load_sales_transactions
from llm_voucher_generator import LLMVoucherGenerator
from sap_exporter import export_sap_csv
from voucher_models import Voucher, VoucherLine
from voucher_rules import build_sales_revenue_voucher, load_voucher_rule_lines, VoucherRuleLine

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(title="Ember AI Accounting", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
SESSION_DIR = PROJECT_ROOT / "data" / "sessions"
POSTED_CSV = PROJECT_ROOT / "data" / "output" / "posted_vouchers.csv"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants ───────────────────────────────────────────────────────────────

BIZ_TYPE_LABELS = {
    "sales_revenue": "销售收入",
    "expense": "费用报销",
    "asset_purchase": "资产采购",
    "salary": "工资薪酬",
    "loan": "借款/还款",
}
POSTED_CSV.parent.mkdir(parents=True, exist_ok=True)

# ── Globals ──────────────────────────────────────────────────────────────────

generator = LLMVoucherGenerator()

SUPPORTED_BUSINESS_TYPES = {
    "sales_revenue": "销售收入（销售商品或提供服务产生的收入）",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PDF_EXTENSIONS = {".pdf"}


# ── Auth helpers ──────────────────────────────────────────────────────────────


async def _get_current_user(request: Request) -> dict | None:
    """Extract and validate current user from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        return await get_user_by_token(token)
    return None


async def _require_auth(request: Request) -> dict:
    """Require authenticated user. Raises HTTPException if not authenticated."""
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


async def _require_admin(request: Request) -> dict:
    """Require admin user. Raises HTTPException if not admin."""
    user = await _require_auth(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ── Session persistence (for chat context) ───────────────────────────────────


def _session_path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.json"


def _load_session(session_id: str) -> dict[str, Any]:
    path = _session_path(session_id)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["vouchers"] = [_dict_to_voucher(v) for v in raw.get("vouchers", [])]
            return raw
        except Exception:
            pass
    return {"vouchers": [], "uploaded_files": []}


def _save_session(session_id: str, session: dict[str, Any]) -> None:
    path = _session_path(session_id)
    data = {
        "vouchers": [_voucher_to_json(v) for v in session.get("vouchers", [])],
        "uploaded_files": session.get("uploaded_files", []),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_session(session_id: str | None) -> tuple[str, dict[str, Any]]:
    sid = session_id or str(uuid.uuid4())
    session = _load_session(sid)
    return sid, session


# Session timeout: 6 hours
SESSION_TIMEOUT_HOURS = 6


async def _is_session_expired(session_id: str) -> bool:
    """Check if a session has been inactive for too long."""
    from datetime import datetime, timedelta
    recent = await list_chat_messages(session_id=session_id, limit=1)
    if not recent:
        return False  # No messages yet, not expired
    last_msg = recent[0]
    last_time = datetime.fromisoformat(last_msg["created_at"])
    if datetime.utcnow() - last_time > timedelta(hours=SESSION_TIMEOUT_HOURS):
        return True
    return False


# ── Helper: voucher ↔ JSON serialisable dict ─────────────────────────────────


def _voucher_to_json(voucher: Voucher) -> dict:
    from dataclasses import asdict

    def _convert(value: object) -> object:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, list):
            return [_convert(item) for item in value]
        if hasattr(value, "__dataclass_fields__"):
            return _convert(asdict(value))
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        return value

    return _convert(voucher)


def _dict_to_voucher(data: dict) -> Voucher:
    lines = [
        VoucherLine(
            line_no=ln["line_no"],
            debit_credit=ln["debit_credit"],
            account_code=ln["account_code"],
            account_name=ln["account_name"],
            amount=Decimal(str(ln["amount"])),
            currency=ln["currency"],
            customer_code=ln.get("customer_code", ""),
            customer_name=ln.get("customer_name", ""),
            tax_code=ln.get("tax_code", ""),
            profit_center=ln.get("profit_center", ""),
            cost_center=ln.get("cost_center", ""),
            assignment=ln.get("assignment", ""),
            text=ln.get("text", ""),
        )
        for ln in data.get("lines", [])
    ]
    return Voucher(
        voucher_id=data["voucher_id"],
        company_code=data["company_code"],
        document_type=data["document_type"],
        document_date=data["document_date"],
        posting_date=data["posting_date"],
        reference=data["reference"],
        header_text=data["header_text"],
        source_transaction_id=data["source_transaction_id"],
        confidence=Decimal(str(data["confidence"])),
        warnings=data.get("warnings", []),
        lines=lines,
    )


def _voucher_to_front(voucher: Voucher) -> dict:
    rows = []
    for line in voucher.lines:
        debit = float(line.amount) if line.debit_credit == "S" else 0
        credit = float(line.amount) if line.debit_credit == "H" else 0
        rows.append({
            "line_no": line.line_no,
            "account_code": line.account_code,
            "account_name": line.account_name,
            "debit_credit": line.debit_credit,
            "debit": debit,
            "credit": credit,
            "currency": line.currency,
            "customer_code": line.customer_code,
            "customer_name": line.customer_name,
            "tax_code": line.tax_code,
            "profit_center": line.profit_center,
            "cost_center": line.cost_center,
            "assignment": line.assignment,
            "text": line.text,
        })
    return {
        "voucher_id": voucher.voucher_id,
        "company_code": voucher.company_code,
        "document_type": voucher.document_type,
        "document_date": voucher.document_date,
        "posting_date": voucher.posting_date,
        "reference": voucher.reference,
        "header_text": voucher.header_text,
        "confidence": str(voucher.confidence),
        "warnings": voucher.warnings,
        "rows": rows,
    }


# ── Startup event ────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    await init_db()
    logger.info("Database initialized")
    migrated = await migrate_rules_from_excel()
    if migrated:
        logger.info("Migrated %d rules from Excel to database", migrated)


# ── API: Auth ─────────────────────────────────────────────────────────────────


@app.post("/api/auth/login")
async def login(payload: dict, request: Request):
    """Authenticate user and return a session token."""
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

    return JSONResponse({
        "token": token,
        "user": user,
    })


@app.post("/api/auth/logout")
async def logout(request: Request):
    """Logout the current user."""
    user = await _get_current_user(request)
    if user:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            await delete_session(token)
            await add_audit_log(action="logout", user_id=user["id"], username=user["username"])
    return JSONResponse({"status": "ok"})


@app.get("/api/auth/me")
async def get_me(request: Request):
    """Get current user info."""
    user = await _get_current_user(request)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    return JSONResponse({"user": user})


# ── API: User Management (admin only) ────────────────────────────────────────


@app.get("/api/users")
async def api_list_users(request: Request):
    """List all users (admin only)."""
    await _require_admin(request)
    users = await list_users()
    # Remove password data
    for u in users:
        u.pop("password_hash", None)
        u.pop("password_salt", None)
    return JSONResponse({"users": users})


@app.post("/api/users")
async def api_create_user(payload: dict, request: Request):
    """Create a new user (admin only)."""
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
        action="user.create",
        user_id=admin["id"],
        username=admin["username"],
        target_type="user",
        target_id=user["id"],
        details={"new_username": username, "new_role": role},
    )

    return JSONResponse({"user": user}, status_code=201)


@app.put("/api/users/{user_id}")
async def api_update_user(user_id: str, payload: dict, request: Request):
    """Update a user (admin only)."""
    admin = await _require_admin(request)

    # Don't allow admin to deactivate themselves
    if user_id == admin["id"] and payload.get("is_active") == 0:
        return JSONResponse({"error": "不能停用自己的账号"}, status_code=400)

    updated = await update_user(user_id, **payload)
    if not updated:
        return JSONResponse({"error": "用户不存在"}, status_code=404)

    await add_audit_log(
        action="user.update",
        user_id=admin["id"],
        username=admin["username"],
        target_type="user",
        target_id=user_id,
        details=payload,
    )

    return JSONResponse({"status": "ok"})


@app.delete("/api/users/{user_id}")
async def api_delete_user(user_id: str, request: Request):
    """Delete a user (admin only)."""
    admin = await _require_admin(request)

    if user_id == admin["id"]:
        return JSONResponse({"error": "不能删除自己的账号"}, status_code=400)

    deleted = await delete_user(user_id)
    if not deleted:
        return JSONResponse({"error": "用户不存在"}, status_code=404)

    await add_audit_log(
        action="user.delete",
        user_id=admin["id"],
        username=admin["username"],
        target_type="user",
        target_id=user_id,
    )

    return JSONResponse({"status": "ok"})


# ── Helpers ──────────────────────────────────────────────────────────────────


def _format_rules_for_frontend(rules: list[dict]) -> list[dict]:
    """Convert database rule dicts to the frontend display format."""
    result = []
    for rule in rules:
        formatted = {
            "rule_code": rule["rule_code"],
            "business_type": rule["business_type"],
            "product_type": rule["product_type"],
            "tax_rate": rule["tax_rate"],
            "document_type": rule["document_type"],
            "lines": [],
        }
        for line in rule.get("lines", []):
            formatted["lines"].append({
                "line_no": line["line_no"],
                "debit_credit": line["debit_credit"],
                "debit_credit_display": "借" if line["debit_credit"] == "S" else "贷",
                "account_code": line["account_code"],
                "account_name": line["account_name"],
                "amount_field": line["amount_field"],
                "amount_field_display": {
                    "total_amount": "价税合计",
                    "tax_excluded_amount": "不含税金额",
                    "tax_amount": "税额",
                }.get(line["amount_field"], line["amount_field"]),
                "customer_source": line.get("customer_source", ""),
                "tax_code_rule": line.get("tax_code_rule", ""),
                "profit_center_source": line.get("profit_center_source", ""),
                "cost_center_source": line.get("cost_center_source", ""),
                "assignment_source": line.get("assignment_source", ""),
                "text_template": line.get("text_template", ""),
            })
        result.append(formatted)
    return result


# ── API: Chat ────────────────────────────────────────────────────────────────


@app.post("/api/chat")
async def chat(payload: dict, request: Request):
    """Accept a natural language message and optional session_id, return AI response + voucher data."""
    user = await _require_auth(request)
    message = payload.get("message", "").strip()
    session_id = payload.get("session_id")

    # Session timeout: if session inactive for > SESSION_TIMEOUT_HOURS, start new session
    if session_id and await _is_session_expired(session_id):
        logger.info("Session %s expired (inactive > %dh), creating new session", session_id, SESSION_TIMEOUT_HOURS)
        session_id = None

    session_id, session = _get_session(session_id)
    chat_session_id = session_id

    if not message:
        return JSONResponse({
            "reply": "请描述一笔业务，比如「请客户吃饭花了1200元」，或上传一张发票。",
            "session_id": session_id,
        })

    # Save user message to audit
    await save_chat_message(
        session_id=chat_session_id,
        user_id=user["id"],
        role="user",
        content=message,
        message_type="chat",
    )

    # Build a synthetic SalesTransaction from the natural language via LLM
    # Pass recent conversation history so LLM can understand context
    # DeepSeek V4 Pro supports 1M context; use 200 messages (100 turns)
    recent_history = await list_chat_messages(session_id=chat_session_id, limit=200)
    history_for_llm = [{"role": m["role"], "content": m["content"]} for m in recent_history]

    # ── Pending action check: if the last assistant message had a pending action,
    #    try to continue that flow instead of re-classifying via LLM. ──
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
        last_msg = recent_history[0]  # most recent message (desc order)
        if last_msg["role"] == "assistant":
            last_meta = last_msg.get("metadata") or {}
            pending = last_meta.get("pending_action")
            if pending in ("rule_mgmt", "rule_query"):
                pending_type = last_meta.get("pending_action_type", "create")
                # Try to extract business type from user's reply
                detected_type = None
                for kw, biz_type in biz_type_map.items():
                    if kw in msg_lower_for_pending:
                        detected_type = biz_type
                        break
                if detected_type:
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
        parse_result = await _parse_transaction_from_nl(message, history=history_for_llm)
    logger.info("NL parse result for '%s': %s", message[:60], parse_result)
    if parse_result is None:
        return JSONResponse({
            "reply": "抱歉，我暂时无法理解。请尝试更具体的描述，例如「销售软件产品给XX公司，不含税金额100000元，税率13%」，或上传Excel附件。",
            "session_id": session_id,
        })

    # Handle chat intent — direct reply, no voucher generation
    if parse_result.get("intent") == "chat":
        reply = parse_result["reply"]

        # ── Keyword fallback: if LLM misclassifies as chat, detect intent from context ──
        msg_lower = message.lower()
        voucher_keywords = ["查看凭证", "凭证记录", "我的凭证", "凭证列表", "已生成凭证", "看看凭证", "查凭证"]
        user_mgmt_keywords = ["添加用户", "新建用户", "创建用户", "增加用户"]
        rule_mgmt_keywords = ["新增规则", "添加规则", "创建规则", "建立规则", "修改规则", "更新规则", "删除规则", "去掉规则",
                              "新增凭证规则", "添加凭证规则", "创建凭证规则"]
        rule_mgmt_action_keywords = ["新增", "添加", "创建", "建立", "修改", "更新", "删除", "去掉"]

        # Check pending action from last assistant message metadata
        pending_action_from_history = None
        if recent_history:
            last_msg = recent_history[0]
            if last_msg["role"] == "assistant":
                last_meta = last_msg.get("metadata") or {}
                pending_action_from_history = last_meta.get("pending_action")

        if any(kw in msg_lower for kw in voucher_keywords):
            # Override intent to voucher_query
            parse_result = {"intent": "voucher_query", "status": None, "reply": reply, "business_type": None, "transaction": None}
            logger.info("Keyword fallback: chat → voucher_query for '%s'", message[:60])

        elif any(kw in msg_lower for kw in user_mgmt_keywords):
            # Override intent to user_mgmt
            parse_result = {"intent": "user_mgmt", "action": "create", "new_username": None, "new_display_name": None, "new_role": "user", "new_password": None, "reply": reply, "business_type": None, "transaction": None}
            logger.info("Keyword fallback: chat → user_mgmt for '%s'", message[:60])

        elif any(kw in msg_lower for kw in rule_mgmt_keywords):
            # Override intent to rule_mgmt
            detected_type = None
            for kw, biz_type in biz_type_map.items():
                if kw in msg_lower:
                    detected_type = biz_type
                    break
            parse_result = {"intent": "rule_mgmt", "action": "create", "rule_type": detected_type, "reply": "", "business_type": None, "transaction": None}
            logger.info("Keyword fallback: chat → rule_mgmt (type=%s) for '%s'", detected_type, message[:60])

        elif any(kw in msg_lower for kw in rule_mgmt_action_keywords) and "规则" in msg_lower:
            # "新增费用报销的规则" — action keyword + 规则
            detected_type = None
            for kw, biz_type in biz_type_map.items():
                if kw in msg_lower:
                    detected_type = biz_type
                    break
            parse_result = {"intent": "rule_mgmt", "action": "create", "rule_type": detected_type, "reply": "", "business_type": None, "transaction": None}
            logger.info("Keyword fallback: chat → rule_mgmt (type=%s) for '%s'", detected_type, message[:60])

        elif pending_action_from_history == "rule_mgmt":
            # Context: previous message was a rule_mgmt prompt → this is a type selection
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
            # Still chat after all fallbacks
            await save_chat_message(
                session_id=chat_session_id,
                user_id=user["id"],
                role="assistant",
                content=reply,
                message_type="chat",
            )
            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
            })

    # Handle rule_query intent — show voucher rules
    if parse_result.get("intent") == "rule_query":
        rule_type = parse_result.get("rule_type")
        reply = parse_result.get("reply", "")

        await add_audit_log(
            action="rule.view",
            user_id=user["id"],
            username=user["username"],
            target_type="rule",
            details={"rule_type": rule_type},
        )

        if rule_type is None:
            available_types = {
                "sales_revenue": "销售收入",
            }
            type_list = "\n".join(f"  {i+1}. {desc}" for i, desc in enumerate(available_types.values()))
            if not reply:
                reply = f"目前系统支持以下凭证类型的规则查看：\n{type_list}\n\n请告诉我您想查看哪种类型的凭证规则？"

            await save_chat_message(
                session_id=chat_session_id,
                user_id=user["id"],
                role="assistant",
                content=reply,
                message_type="chat",
                metadata={"pending_action": "rule_query"},
            )
            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
            })

        # User specified a type — load from database and return matching rules
        try:
            rules_list = await list_rules(business_type=rule_type)
            rules_list = _format_rules_for_frontend(rules_list)
        except Exception as exc:
            logger.error("Failed to load voucher rules: %s", exc)
            return JSONResponse({
                "reply": "加载凭证规则时出错，请稍后重试。",
                "session_id": session_id,
            })

        biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type)

        if not rules_list:
            if not reply:
                reply = f"暂无「{biz_label}」类型的凭证规则配置。"
            return JSONResponse({"reply": reply, "session_id": session_id, "view": "rules", "rules": [], "rule_type": rule_type})

        if not reply:
            reply = f"以下是「{biz_label}」类型的凭证规则，共 {len(rules_list)} 条："

        await save_chat_message(
            session_id=chat_session_id,
            user_id=user["id"],
            role="assistant",
            content=reply,
            message_type="chat",
            metadata={"rule_type": rule_type},
        )

        return JSONResponse({
            "reply": reply,
            "session_id": session_id,
            "rules": rules_list,
            "rule_type": rule_type,
            "view": "rules",
        })

    # Handle rule_mgmt intent — create/update/delete voucher rules
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
            return JSONResponse({"reply": reply, "session_id": session_id})

        biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type)

        # Admin permission check for all write operations
        if user["role"] != "admin":
            reply = f"抱歉，只有管理员才能管理凭证规则。"
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
            )
            return JSONResponse({"reply": reply, "session_id": session_id})

        if action == "create":
            if not reply:
                reply = (
                    f"好的，我来帮你创建「{biz_label}」类型的凭证规则。\n\n"
                    "请在右侧弹出的表单中填写规则信息，或直接告诉我规则详情。"
                )

            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
                metadata={"action": "rule_mgmt_create", "rule_type": rule_type},
            )

            await add_audit_log(
                action="rule.create_start",
                user_id=user["id"],
                username=user["username"],
                target_type="rule",
                details={"rule_type": rule_type},
            )

            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
                "view": "rules",
                "rule_mgmt": {"action": "create", "rule_type": rule_type},
            })

        if action in ("update", "delete"):
            try:
                rules_list = await list_rules(business_type=rule_type)
                rules_list = _format_rules_for_frontend(rules_list)
            except Exception as exc:
                logger.error("Failed to load rules: %s", exc)
                return JSONResponse({"reply": "加载规则失败，请重试。", "session_id": session_id})

            if not rules_list:
                reply = f"暂无「{biz_label}」类型的规则可供{action}。"
                await save_chat_message(
                    session_id=chat_session_id, user_id=user["id"],
                    role="assistant", content=reply, message_type="chat",
                )
                return JSONResponse({"reply": reply, "session_id": session_id})

            if not reply:
                verb = "修改" if action == "update" else "删除"
                reply = f"请选择要{verb}的「{biz_label}」规则："

            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
                metadata={"action": f"rule_mgmt_{action}", "rule_type": rule_type},
            )

            await add_audit_log(
                action=f"rule.{action}_start",
                user_id=user["id"],
                username=user["username"],
                target_type="rule",
                details={"rule_type": rule_type},
            )

            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
                "view": "rules",
                "rules": rules_list,
                "rule_mgmt": {"action": action, "rule_type": rule_type},
            })

    # Handle voucher_query intent — show user's voucher records
    if parse_result.get("intent") == "voucher_query":
        status_filter = parse_result.get("status")
        reply = parse_result.get("reply", "")

        # Regular users see only their own; admins see all
        user_id = None if user["role"] == "admin" else user["id"]
        records = await list_voucher_records(user_id=user_id, status=status_filter, limit=50, offset=0)
        total = await count_voucher_records(user_id=user_id, status=status_filter)

        await add_audit_log(
            action="voucher.query",
            user_id=user["id"],
            username=user["username"],
            target_type="voucher",
            details={"status_filter": status_filter, "result_count": len(records)},
        )

        status_label = {"draft": "草稿", "posted": "已过账"}.get(status_filter, "全部")
        if not records:
            if not reply:
                reply = f"暂无{status_label}状态的凭证记录。"
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
            )
            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
                "view": "voucher_list",
                "view_data": {"vouchers": [], "total": 0, "status_filter": status_filter},
            })

        if not reply:
            reply = f"共找到 {total} 条{status_label}凭证记录："

        await save_chat_message(
            session_id=chat_session_id, user_id=user["id"],
            role="assistant", content=reply, message_type="chat",
            metadata={"voucher_count": len(records)},
        )

        return JSONResponse({
            "reply": reply,
            "session_id": session_id,
            "view": "voucher_list",
            "view_data": {"vouchers": records, "total": total, "status_filter": status_filter},
        })

    # Handle user_mgmt intent — admin creates user via conversation
    if parse_result.get("intent") == "user_mgmt":
        reply = parse_result.get("reply", "")

        # Check admin permission
        if user["role"] != "admin":
            reply = "抱歉，只有管理员才能添加用户。"
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
            )
            return JSONResponse({"reply": reply, "session_id": session_id})

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
                )
                return JSONResponse({"reply": reply, "session_id": session_id})

            # Generate default password if not provided
            if not new_password:
                import string
                new_password = "User@" + secrets.token_hex(4)

            try:
                created = await create_user(new_username, new_password, new_display_name, new_role)
            except Exception as exc:
                if "UNIQUE constraint" in str(exc):
                    reply = f"用户名「{new_username}」已存在，请使用其他用户名。"
                else:
                    reply = f"创建用户失败：{exc}"
                await save_chat_message(
                    session_id=chat_session_id, user_id=user["id"],
                    role="assistant", content=reply, message_type="chat",
                )
                return JSONResponse({"reply": reply, "session_id": session_id})

            await add_audit_log(
                action="user.create",
                user_id=user["id"],
                username=user["username"],
                target_type="user",
                target_id=created["id"],
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

            # Also return updated user list
            users = await list_users()
            for u in users:
                u.pop("password_hash", None)
                u.pop("password_salt", None)

            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
                "view": "user_list",
                "view_data": {"users": users},
            })

        # Default reply for unknown user_mgmt action
        if not reply:
            reply = "请告诉我您要如何管理用户，例如「添加用户zhangsan」。"
        await save_chat_message(
            session_id=chat_session_id, user_id=user["id"],
            role="assistant", content=reply, message_type="chat",
        )
        return JSONResponse({"reply": reply, "session_id": session_id})

    business_type = parse_result["business_type"]
    txn = parse_result["transaction"]

    # Check if the business type is supported
    if business_type not in SUPPORTED_BUSINESS_TYPES or txn is None:
        supported_list = "\n".join(
            f"  - {desc}" for desc in SUPPORTED_BUSINESS_TYPES.values()
        )
        type_display = {
            "expense": "费用报销",
            "asset_purchase": "资产采购",
            "salary": "工资薪酬",
            "loan": "借款/还款",
            "other": "其他",
        }.get(business_type, business_type)

        reply = (
            f"抱歉，当前系统暂不支持「{type_display}」类型的凭证生成。\n\n"
            f"目前支持的凭证类型：\n{supported_list}\n\n"
            "请描述一笔支持的业务，或上传Excel附件。"
        )
        await save_chat_message(
            session_id=chat_session_id,
            user_id=user["id"],
            role="assistant",
            content=reply,
            message_type="chat",
        )
        return JSONResponse({
            "reply": reply,
            "session_id": session_id,
        })

    voucher = await generator.generate(txn)
    session["vouchers"].append(voucher)
    _save_session(session_id, session)

    # Save voucher record to database
    voucher_front = _voucher_to_front(voucher)
    await save_voucher_record(
        voucher_id=voucher.voucher_id,
        user_id=user["id"],
        voucher_data=voucher_front,
        session_id=session_id,
        company_code=voucher.company_code,
        document_type=voucher.document_type,
        document_date=voucher.document_date,
        posting_date=voucher.posting_date,
        reference=voucher.reference,
        header_text=voucher.header_text,
        confidence=str(voucher.confidence),
        warnings=voucher.warnings,
    )

    # Audit log
    await add_audit_log(
        action="voucher.generate",
        user_id=user["id"],
        username=user["username"],
        target_type="voucher",
        target_id=voucher.voucher_id,
        details={"business_type": business_type},
    )

    reply = f"已为您生成凭证草稿（置信度 {voucher.confidence}）。"
    if voucher.warnings:
        reply += f" ⚠️ 注意：{'；'.join(voucher.warnings)}"

    await save_chat_message(
        session_id=chat_session_id,
        user_id=user["id"],
        role="assistant",
        content=reply,
        message_type="chat",
        metadata={"voucher_id": voucher.voucher_id},
    )

    return JSONResponse({
        "reply": reply,
        "session_id": session_id,
        "voucher": voucher_front,
        "view": "voucher",
    })


# ── API: File Upload ─────────────────────────────────────────────────────────


@app.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str | None = None,
):
    """Upload an Excel or image file, parse it, generate vouchers via LLM."""
    user = await _require_auth(request)
    chat_session_id = session_id or str(uuid.uuid4())
    session_id, session = _get_session(session_id)

    # Save user message
    await save_chat_message(
        session_id=chat_session_id,
        user_id=user["id"],
        role="user",
        content=f"上传文件: {file.filename}",
        message_type="upload",
        metadata={"filename": file.filename},
    )

    # Save uploaded file
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

    # ── Image / PDF path: use multimodal LLM for OCR ──
    if suffix in IMAGE_EXTENSIONS or suffix in PDF_EXTENSIONS:
        if suffix in PDF_EXTENSIONS:
            result = await _parse_pdf_to_transaction(saved_path)
        else:
            result = await _parse_image_to_transaction(saved_path)

        source_label = "PDF" if suffix in PDF_EXTENSIONS else "图片"

        if result is None:
            reply = f"{source_label}识别失败，无法提取有效信息。请确保内容清晰且包含完整的发票/单据信息。"
            await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="upload")
            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
                "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            })

        business_type = result.get("business_type", "other")
        if business_type not in SUPPORTED_BUSINESS_TYPES:
            supported_list = "\n".join(f"  - {desc}" for desc in SUPPORTED_BUSINESS_TYPES.values())
            type_display = {"expense": "费用报销", "asset_purchase": "资产采购", "salary": "工资薪酬", "loan": "借款/还款", "other": "其他"}.get(business_type, business_type)
            reply = f"已识别单据类型为「{type_display}」，但当前系统暂不支持该类型的凭证生成。\n\n目前支持的凭证类型：\n{supported_list}"
            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
                "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            })

        txn = result.get("transaction")
        if txn is None:
            reply = f"{source_label}识别成功，但未能提取到完整的交易金额信息。请上传更清晰的文件。"
            return JSONResponse({
                "reply": reply,
                "session_id": session_id,
                "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            })

        voucher = await generator.generate(txn)
        session["vouchers"].append(voucher)
        _save_session(session_id, session)

        voucher_front = _voucher_to_front(voucher)
        await save_voucher_record(
            voucher_id=voucher.voucher_id,
            user_id=user["id"],
            voucher_data=voucher_front,
            session_id=session_id,
            company_code=voucher.company_code,
            document_type=voucher.document_type,
            document_date=voucher.document_date,
            posting_date=voucher.posting_date,
            reference=voucher.reference,
            header_text=voucher.header_text,
            confidence=str(voucher.confidence),
            warnings=voucher.warnings,
        )
        await add_audit_log(
            action="voucher.generate",
            user_id=user["id"],
            username=user["username"],
            target_type="voucher",
            target_id=voucher.voucher_id,
            details={"source": source_label, "filename": file.filename},
        )

        # Export to SAP CSV
        output_path = PROJECT_ROOT / "data" / "output" / f"sap_{file_id}.csv"
        export_sap_csv([voucher], output_path)

        reply = f"已从{source_label}中识别出1笔销售收入交易，生成了1张凭证草稿。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="upload", metadata={"voucher_id": voucher.voucher_id})

        return JSONResponse({
            "reply": reply,
            "session_id": session_id,
            "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            "vouchers": [voucher_front],
        })

    # ── Excel path: original logic ──
    try:
        transactions = load_sales_transactions(saved_path)
    except Exception as exc:
        logger.warning("Failed to parse uploaded file: %s", exc)
        return JSONResponse({
            "reply": f"文件解析失败：{exc}。请确保Excel格式正确。",
            "session_id": session_id,
        })

    vouchers = []
    for txn in transactions:
        voucher = await generator.generate(txn)
        session["vouchers"].append(voucher)
        vouchers.append(voucher)

        voucher_front = _voucher_to_front(voucher)
        await save_voucher_record(
            voucher_id=voucher.voucher_id,
            user_id=user["id"],
            voucher_data=voucher_front,
            session_id=session_id,
            company_code=voucher.company_code,
            document_type=voucher.document_type,
            document_date=voucher.document_date,
            posting_date=voucher.posting_date,
            reference=voucher.reference,
            header_text=voucher.header_text,
            confidence=str(voucher.confidence),
            warnings=voucher.warnings,
        )
        await add_audit_log(
            action="voucher.generate",
            user_id=user["id"],
            username=user["username"],
            target_type="voucher",
            target_id=voucher.voucher_id,
            details={"source": "excel", "filename": file.filename},
        )

    _save_session(session_id, session)

    reply = f"已解析 {len(transactions)} 笔交易，生成了 {len(vouchers)} 张凭证草稿。"

    # Export to SAP CSV
    output_path = PROJECT_ROOT / "data" / "output" / f"sap_{file_id}.csv"
    export_sap_csv(vouchers, output_path)

    await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="upload", metadata={"voucher_count": len(vouchers)})

    return JSONResponse({
        "reply": reply,
        "session_id": session_id,
        "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
        "vouchers": [_voucher_to_front(v) for v in vouchers],
    })


# ── API: Confirm Voucher ─────────────────────────────────────────────────────


@app.post("/api/confirm")
async def confirm_voucher(payload: dict, request: Request):
    """Mark a voucher as posted: append to posted_vouchers.csv + update DB + audit."""
    user = await _require_auth(request)
    session_id = payload.get("session_id")
    voucher_id = payload.get("voucher_id")
    session_id, session = _get_session(session_id)

    # Try session first, then fall back to DB
    voucher = None
    for v in session.get("vouchers", []):
        if v.voucher_id == voucher_id:
            voucher = v
            break

    db_record = None
    if not voucher:
        db_record = await get_voucher_record(voucher_id)
        if not db_record:
            return JSONResponse({"status": "not_found", "message": f"凭证 {voucher_id} 不存在"})
        if db_record.get("status") == "posted":
            return JSONResponse({"status": "already_posted", "message": f"凭证 {voucher_id} 已经过账"})

    # Append to posted_vouchers.csv
    if voucher:
        _append_posted_csv(voucher)
    else:
        _append_posted_csv_from_record(db_record)

    # Update session if voucher was session-loaded
    if voucher:
        session["posted_voucher_ids"] = session.get("posted_voucher_ids", [])
        if voucher_id not in session["posted_voucher_ids"]:
            session["posted_voucher_ids"].append(voucher_id)
        _save_session(session_id, session)

    # Update database record
    await mark_voucher_posted(voucher_id, user["id"])

    # Audit log
    await add_audit_log(
        action="voucher.post",
        user_id=user["id"],
        username=user["username"],
        target_type="voucher",
        target_id=voucher_id,
        details={"session_id": session_id},
    )

    # Save chat message for audit
    await save_chat_message(
        session_id=session_id or "unknown",
        user_id=user["id"],
        role="assistant",
        content=f"凭证 {voucher_id} 已确认记账",
        message_type="confirm",
        metadata={"voucher_id": voucher_id},
    )

    logger.info("Voucher %s posted by %s and saved to %s", voucher_id, user["username"], POSTED_CSV)

    return JSONResponse({
        "status": "posted",
        "message": f"凭证 {voucher_id} 已成功过账，保存至 {POSTED_CSV.name}",
    })


def _append_posted_csv(voucher: Voucher) -> None:
    """Append one voucher's lines to the persistent posted_vouchers.csv."""
    import csv
    from sap_exporter import SAP_COLUMNS

    write_header = not POSTED_CSV.exists() or POSTED_CSV.stat().st_size == 0

    with POSTED_CSV.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SAP_COLUMNS)
        if write_header:
            writer.writeheader()
        for line in voucher.lines:
            writer.writerow({
                "BUKRS": voucher.company_code,
                "BLART": voucher.document_type,
                "BLDAT": voucher.document_date,
                "BUDAT": voucher.posting_date,
                "XBLNR": voucher.reference,
                "BKTXT": voucher.header_text,
                "BUZEI": line.line_no,
                "SHKZG": line.debit_credit,
                "HKONT": line.account_code,
                "ACCOUNT_NAME": line.account_name,
                "WRBTR": line.amount,
                "WAERS": line.currency,
                "KUNNR": line.customer_code,
                "CUSTOMER_NAME": line.customer_name,
                "MWSKZ": line.tax_code,
                "PRCTR": line.profit_center,
                "KOSTL": line.cost_center,
                "ZUONR": line.assignment,
                "SGTXT": line.text,
            })


def _append_posted_csv_from_record(record: dict) -> None:
    """Append a DB voucher record's lines to posted_vouchers.csv."""
    import csv
    from sap_exporter import SAP_COLUMNS

    voucher_data = json.loads(record.get("voucher_data") or "{}")
    rows = voucher_data.get("rows", [])

    write_header = not POSTED_CSV.exists() or POSTED_CSV.stat().st_size == 0

    with POSTED_CSV.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SAP_COLUMNS)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({
                "BUKRS": record.get("company_code", ""),
                "BLART": record.get("document_type", ""),
                "BLDAT": record.get("document_date", ""),
                "BUDAT": record.get("posting_date", ""),
                "XBLNR": record.get("reference", ""),
                "BKTXT": record.get("header_text", ""),
                "BUZEI": row.get("line_no", ""),
                "SHKZG": row.get("debit_credit", ""),
                "HKONT": row.get("account_code", ""),
                "ACCOUNT_NAME": row.get("account_name", ""),
                "WRBTR": row.get("debit", 0) or row.get("credit", 0),
                "WAERS": row.get("currency", "CNY"),
                "KUNNR": row.get("customer_code", ""),
                "CUSTOMER_NAME": row.get("customer_name", ""),
                "MWSKZ": row.get("tax_code", ""),
                "PRCTR": row.get("profit_center", ""),
                "KOSTL": row.get("cost_center", ""),
                "ZUONR": row.get("assignment", ""),
                "SGTXT": row.get("text", ""),
            })


# ── API: Voucher Rules ───────────────────────────────────────────────────────


@app.get("/api/rules")
async def get_voucher_rules(request: Request, business_type: str | None = None):
    """Return the current voucher rule configuration as JSON."""
    user = await _require_auth(request)

    try:
        rules = await list_rules(business_type=business_type)
        rules = _format_rules_for_frontend(rules)
    except Exception as exc:
        logger.error("Failed to load voucher rules: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    total_lines = sum(len(r.get("lines", [])) for r in rules)
    return JSONResponse({
        "rules": rules,
        "total_rules": len(rules),
        "total_lines": total_lines,
    })


@app.post("/api/rules")
async def api_create_rule(payload: dict, request: Request):
    """Create a new voucher rule (admin only)."""
    admin = await _require_admin(request)

    rule_code = (payload.get("rule_code") or "").strip()
    if not rule_code:
        return JSONResponse({"error": "规则代码不能为空"}, status_code=400)

    # Check for duplicate
    existing = await get_rule(rule_code)
    if existing:
        return JSONResponse({"error": f"规则代码「{rule_code}」已存在"}, status_code=400)

    lines = payload.get("lines", [])
    if not lines:
        return JSONResponse({"error": "至少需要一条分录行"}, status_code=400)

    try:
        rule = await create_rule(
            rule_code=rule_code,
            business_type=payload.get("business_type", "sales_revenue"),
            product_type=payload.get("product_type", "*"),
            tax_rate=payload.get("tax_rate", "*"),
            document_type=payload.get("document_type", "DR"),
            lines=lines,
        )
    except Exception as exc:
        logger.error("Failed to create rule: %s", exc)
        return JSONResponse({"error": "创建规则失败"}, status_code=500)

    await add_audit_log(
        action="rule.create",
        user_id=admin["id"],
        username=admin["username"],
        target_type="rule",
        target_id=rule_code,
    )

    formatted = _format_rules_for_frontend([rule])
    return JSONResponse({"rule": formatted[0] if formatted else {}}, status_code=201)


@app.put("/api/rules/{rule_code}")
async def api_update_rule(rule_code: str, payload: dict, request: Request):
    """Update an existing voucher rule (admin only)."""
    admin = await _require_admin(request)

    existing = await get_rule(rule_code)
    if not existing:
        return JSONResponse({"error": "规则不存在"}, status_code=404)

    lines = payload.get("lines")
    try:
        updated = await db_update_rule(
            rule_code,
            lines=lines,
            business_type=payload.get("business_type"),
            product_type=payload.get("product_type"),
            tax_rate=payload.get("tax_rate"),
            document_type=payload.get("document_type"),
        )
    except Exception as exc:
        logger.error("Failed to update rule: %s", exc)
        return JSONResponse({"error": "更新规则失败"}, status_code=500)

    if not updated:
        return JSONResponse({"error": "更新失败"}, status_code=500)

    await add_audit_log(
        action="rule.update",
        user_id=admin["id"],
        username=admin["username"],
        target_type="rule",
        target_id=rule_code,
    )

    rule = await get_rule(rule_code)
    formatted = _format_rules_for_frontend([rule])
    return JSONResponse({"rule": formatted[0] if formatted else {}})


@app.delete("/api/rules/{rule_code}")
async def api_delete_rule(rule_code: str, request: Request):
    """Delete a voucher rule (admin only)."""
    admin = await _require_admin(request)

    existing = await get_rule(rule_code)
    if not existing:
        return JSONResponse({"error": "规则不存在"}, status_code=404)

    deleted = await db_delete_rule(rule_code)
    if not deleted:
        return JSONResponse({"error": "删除失败"}, status_code=500)

    await add_audit_log(
        action="rule.delete",
        user_id=admin["id"],
        username=admin["username"],
        target_type="rule",
        target_id=rule_code,
    )

    return JSONResponse({"status": "ok"})


# ── API: Voucher History ─────────────────────────────────────────────────────


@app.get("/api/vouchers")
async def api_list_vouchers(request: Request, status: str | None = None, limit: int = 50, offset: int = 0):
    """List voucher records for the current user (or all for admin)."""
    user = await _require_auth(request)

    # Regular users see only their own vouchers; admins see all
    user_id = None if user["role"] == "admin" else user["id"]

    records = await list_voucher_records(user_id=user_id, status=status, limit=limit, offset=offset)
    total = await count_voucher_records(user_id=user_id, status=status)

    return JSONResponse({
        "vouchers": records,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@app.get("/api/vouchers/{voucher_id}")
async def api_get_voucher(voucher_id: str, request: Request):
    """Get a single voucher record with full data in frontend format."""
    user = await _require_auth(request)

    record = await get_voucher_record(voucher_id)
    if not record:
        return JSONResponse({"error": "凭证不存在"}, status_code=404)

    # Check access: regular users can only see their own vouchers
    if user["role"] != "admin" and record["user_id"] != user["id"]:
        return JSONResponse({"error": "无权查看此凭证"}, status_code=403)

    # Parse voucher_data JSON and return in frontend format
    voucher_data = json.loads(record.get("voucher_data") or "{}")
    voucher_data["status"] = record.get("status", "draft")
    voucher_data["created_at"] = record.get("created_at")
    voucher_data["posted_at"] = record.get("posted_at")
    voucher_data["posted_by_name"] = record.get("posted_by_name")
    voucher_data["user_display_name"] = record.get("user_display_name")

    return JSONResponse({"voucher": voucher_data})


# ── API: Audit Logs (admin only) ─────────────────────────────────────────────


@app.get("/api/audit-logs")
async def api_list_audit_logs(request: Request, action: str | None = None, limit: int = 100, offset: int = 0):
    """List audit logs (admin only)."""
    await _require_admin(request)

    logs = await list_audit_logs(action=action, limit=limit, offset=offset)

    return JSONResponse({
        "logs": logs,
        "limit": limit,
        "offset": offset,
    })


@app.get("/api/chat-history")
async def api_chat_history(request: Request, limit: int = 100, offset: int = 0):
    """List chat messages (admin only, for audit)."""
    await _require_admin(request)

    messages = await list_chat_messages(limit=limit, offset=offset)

    return JSONResponse({
        "messages": messages,
        "limit": limit,
        "offset": offset,
    })


# ── NL → Transaction via LLM ─────────────────────────────────────────────────


async def _parse_transaction_from_nl(message: str, history: list[dict] | None = None) -> dict | None:
    from openai import AsyncOpenAI
    import os

    base_url = os.environ.get(
        "PMDE_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"
    )
    api_key = os.environ.get(
        "PMDE_API_KEY", "4fea2171-9079-434e-bdf5-d98a00db9363"
    )
    model_name = os.environ.get("PMDE_MODEL_NAME", "deepseek-v4-pro")

    today = date.today().strftime("%Y-%m-%d")
    user_prompt = (
        f"当前日期：{today}\n\n用户输入：{message}\n\n"
        "请先判断用户意图（intent），再进行后续处理。"
    )

    # Build message list with conversation history for context
    messages = [{"role": "system", "content": NL_PARSE_SYSTEM_PROMPT}]
    if history:
        for msg in history[-200:]:  # Last 100 turns (user + assistant pairs)
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_prompt})

    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        completion = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.1,
        )
        raw = completion.choices[0].message.content

        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        data = json.loads(json_str)
        intent = data.get("intent", "unknown")

        if intent == "chat":
            return {
                "intent": "chat",
                "reply": data.get("reply", "你好！我是 Ember，有什么可以帮你的吗？"),
                "business_type": None,
                "transaction": None,
            }

        if intent == "rule_query":
            return {
                "intent": "rule_query",
                "rule_type": data.get("rule_type"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        if intent == "rule_mgmt":
            return {
                "intent": "rule_mgmt",
                "action": data.get("action", "create"),
                "rule_type": data.get("rule_type"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        if intent == "voucher_query":
            return {
                "intent": "voucher_query",
                "status": data.get("status"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        if intent == "user_mgmt":
            return {
                "intent": "user_mgmt",
                "action": data.get("action", "create"),
                "new_username": data.get("new_username"),
                "new_display_name": data.get("new_display_name"),
                "new_role": data.get("new_role", "user"),
                "new_password": data.get("new_password"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        business_type = data.get("business_type", "other")

        if business_type != "sales_revenue":
            return {"intent": "business", "business_type": business_type, "transaction": None}

        if data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
            return {"intent": "business", "business_type": business_type, "transaction": None}

        from voucher_models import SalesTransaction

        txn = SalesTransaction(
            transaction_id=data["transaction_id"],
            company_code=data.get("company_code", "1000"),
            document_date=data.get("document_date", today),
            posting_date=data.get("posting_date", today),
            customer_code=data.get("customer_code", "C99999"),
            customer_name=data.get("customer_name", "未知客户"),
            product_type=data.get("product_type", "service"),
            contract_no=data.get("contract_no", ""),
            invoice_no=data.get("invoice_no", ""),
            currency=data.get("currency", "CNY"),
            tax_rate=Decimal(str(data.get("tax_rate", "0.13"))),
            tax_excluded_amount=Decimal(str(data["tax_excluded_amount"])),
            tax_amount=Decimal(str(data.get("tax_amount", "0"))),
            total_amount=Decimal(str(data["total_amount"])),
            profit_center=data.get("profit_center", "PC-DEFAULT"),
            cost_center=data.get("cost_center", "CC-DEFAULT"),
        )
        return {"intent": "business", "business_type": business_type, "transaction": txn}

    except Exception as exc:
        logger.error("NL parse failed: %s", exc)
        return None


async def _parse_image_to_transaction(image_path: Path) -> dict | None:
    from openai import AsyncOpenAI
    import os
    import base64

    base_url = os.environ.get(
        "PMDE_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"
    )
    api_key = os.environ.get(
        "PMDE_API_KEY", "4fea2171-9079-434e-bdf5-d98a00db9363"
    )
    model_name = os.environ.get(
        "PMDE_VISION_MODEL_NAME",
        os.environ.get("PMDE_MODEL_NAME", "deepseek-v4-pro"),
    )

    today = date.today().strftime("%Y-%m-%d")

    try:
        image_bytes = image_path.read_bytes()
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        ext = image_path.suffix.lower()
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }
        mime_type = mime_map.get(ext, "image/jpeg")
    except Exception as exc:
        logger.error("Failed to read image: %s", exc)
        return None

    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        completion = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": IMAGE_PARSE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}},
                        {"type": "text", "text": f"当前日期：{today}\n\n请识别这张发票/单据图片，提取交易数据。"},
                    ],
                },
            ],
            temperature=0.1,
        )
        raw = completion.choices[0].message.content

        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        data = json.loads(json_str)
        business_type = data.get("business_type", "other")

        if business_type != "sales_revenue":
            return {"business_type": business_type, "transaction": None}

        if data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
            return {"business_type": business_type, "transaction": None}

        from voucher_models import SalesTransaction

        txn = SalesTransaction(
            transaction_id=data["transaction_id"],
            company_code=data.get("company_code", "1000"),
            document_date=data.get("document_date", today),
            posting_date=data.get("posting_date", today),
            customer_code=data.get("customer_code", "C99999"),
            customer_name=data.get("customer_name", "未知客户"),
            product_type=data.get("product_type", "service"),
            contract_no=data.get("contract_no", ""),
            invoice_no=data.get("invoice_no", ""),
            currency=data.get("currency", "CNY"),
            tax_rate=Decimal(str(data.get("tax_rate", "0.13"))),
            tax_excluded_amount=Decimal(str(data["tax_excluded_amount"])),
            tax_amount=Decimal(str(data.get("tax_amount", "0"))),
            total_amount=Decimal(str(data["total_amount"])),
            profit_center=data.get("profit_center", "PC-DEFAULT"),
            cost_center=data.get("cost_center", "CC-DEFAULT"),
        )
        return {"business_type": business_type, "transaction": txn}

    except Exception as exc:
        logger.error("Image parse failed: %s", exc)
        return None


def _pdf_to_images(pdf_path: Path) -> list[tuple[bytes, str]]:
    import fitz

    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        pages.append((pix.tobytes("png"), "image/png"))
    doc.close()
    return pages


async def _parse_pdf_to_transaction(pdf_path: Path) -> dict | None:
    from openai import AsyncOpenAI
    import os
    import base64

    try:
        pages = _pdf_to_images(pdf_path)
    except Exception as exc:
        logger.error("Failed to convert PDF to images: %s", exc)
        return None

    if not pages:
        return None

    base_url = os.environ.get(
        "PMDE_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"
    )
    api_key = os.environ.get(
        "PMDE_API_KEY", "4fea2171-9079-434e-bdf5-d98a00db9363"
    )
    model_name = os.environ.get(
        "PMDE_VISION_MODEL_NAME",
        os.environ.get("PMDE_MODEL_NAME", "deepseek-v4-pro"),
    )

    today = date.today().strftime("%Y-%m-%d")

    image_blocks = []
    for i, (img_bytes, mime_type) in enumerate(pages):
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        image_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
        })

    page_note = ""
    if len(pages) > 1:
        page_note = f"该PDF共{len(pages)}页，请识别其中包含发票/单据的页面并提取数据。"

    try:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        completion = await client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": IMAGE_PARSE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        *image_blocks,
                        {"type": "text", "text": f"当前日期：{today}\n\n请识别这张发票/单据，提取交易数据。{page_note}"},
                    ],
                },
            ],
            temperature=0.1,
        )
        raw = completion.choices[0].message.content

        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        data = json.loads(json_str)
        business_type = data.get("business_type", "other")

        if business_type != "sales_revenue":
            return {"business_type": business_type, "transaction": None}

        if data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
            return {"business_type": business_type, "transaction": None}

        from voucher_models import SalesTransaction

        txn = SalesTransaction(
            transaction_id=data["transaction_id"],
            company_code=data.get("company_code", "1000"),
            document_date=data.get("document_date", today),
            posting_date=data.get("posting_date", today),
            customer_code=data.get("customer_code", "C99999"),
            customer_name=data.get("customer_name", "未知客户"),
            product_type=data.get("product_type", "service"),
            contract_no=data.get("contract_no", ""),
            invoice_no=data.get("invoice_no", ""),
            currency=data.get("currency", "CNY"),
            tax_rate=Decimal(str(data.get("tax_rate", "0.13"))),
            tax_excluded_amount=Decimal(str(data["tax_excluded_amount"])),
            tax_amount=Decimal(str(data.get("tax_amount", "0"))),
            total_amount=Decimal(str(data["total_amount"])),
            profit_center=data.get("profit_center", "PC-DEFAULT"),
            cost_center=data.get("cost_center", "CC-DEFAULT"),
        )
        return {"business_type": business_type, "transaction": txn}

    except Exception as exc:
        logger.error("PDF parse failed: %s", exc)
        return None


# ── Serve static frontend ────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(PROJECT_ROOT), html=True), name="static")


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
