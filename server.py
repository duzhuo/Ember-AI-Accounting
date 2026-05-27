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

import json
import logging
import shutil
import uuid
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agentscope.message import Msg, UserMsg, AssistantMsg
from agentscope.workspace import LocalWorkspace
from agents.agent_config import AGENT_NAME, AGENT_CAPABILITIES
from agents.intent_agent import IntentAgent
from agents.voucher_agent import VoucherAgent
from agents.ocr_agent import OcrAgent

from database import (
    add_audit_log,
    authenticate_user,
    create_rule,
    create_session_token,
    create_user,
    delete_attachment,
    delete_rule as db_delete_rule,
    delete_session,
    delete_user,
    get_db,
    get_rule,
    get_user_by_token,
    init_db,
    list_attachments,
    list_audit_logs,
    list_chat_messages,
    get_voucher_record,
    list_rules,
    list_users,
    list_voucher_records,
    mark_voucher_posted,
    migrate_rules_from_excel,
    save_attachment,
    seed_default_rules,
    save_chat_message,
    save_voucher_record,
    update_rule as db_update_rule,
    update_user,
    update_voucher_record,
    count_voucher_records,
)
from excel_loader import load_sales_transactions
from sap_exporter import export_sap_csv
from voucher_models import Voucher, VoucherLine

# ── Logging ──────────────────────────────────────────────────────────────────

_log_dir = Path(__file__).parent / "data" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_log_dir / "ember.log", encoding="utf-8"),
    ],
)
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

SUPPORTED_BUSINESS_TYPES = {
    "sales_revenue": "销售收入（销售商品或提供服务产生的收入）",
    "expense": "费用报销（餐饮、差旅、办公等费用报销）",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PDF_EXTENSIONS = {".pdf"}


# ── SSE helpers ──────────────────────────────────────────────────────────────


def _sse(data: dict) -> bytes:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


def _extract_reply_delta(accumulated: str, last_len: int) -> str:
    """Try to parse accumulated JSON text and extract the reply field's new portion."""
    try:
        # Find the reply value in the JSON
        idx = accumulated.find('"reply"')
        if idx < 0:
            return ""
        # Find the value after "reply":
        colon = accumulated.index(":", idx + 7)
        # Find the opening quote of the string value
        quote_start = accumulated.index('"', colon + 1)
        # Extract from current position to end of accumulated (the reply is still building)
        reply_so_far = accumulated[quote_start + 1:]
        # Unescape basic JSON escapes
        reply_so_far = reply_so_far.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
        if len(reply_so_far) > last_len:
            return reply_so_far[last_len:]
    except (ValueError, IndexError):
        pass
    return ""


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


def _load_session(session_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    """Load session from disk. Returns None if session belongs to a different user."""
    path = _session_path(session_id)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            # Reject session if it belongs to a different user
            if user_id and raw.get("user_id") and raw["user_id"] != user_id:
                return None
            raw["vouchers"] = [_dict_to_voucher(v) for v in raw.get("vouchers", [])]
            return raw
        except Exception:
            pass
    return {"vouchers": [], "uploaded_files": [], "user_id": user_id}


def _save_session(session_id: str, session: dict[str, Any]) -> None:
    path = _session_path(session_id)
    data = {
        "user_id": session.get("user_id"),
        "vouchers": [_voucher_to_json(v) for v in session.get("vouchers", [])],
        "uploaded_files": session.get("uploaded_files", []),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_session(session_id: str | None, user_id: str | None = None) -> tuple[str, dict[str, Any]]:
    sid = session_id or str(uuid.uuid4())
    session = _load_session(sid, user_id)
    # Session belongs to a different user — create a new one
    if session is None:
        sid = str(uuid.uuid4())
        session = {"vouchers": [], "uploaded_files": [], "user_id": user_id}
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


# ── A2UI Protocol Helpers ────────────────────────────────────────────────────


def _build_a2ui_messages(surface_id: str, components: list, data: dict | None = None) -> list:
    """Build A2UI v0.9 protocol messages: createSurface + updateComponents + optional updateDataModel."""
    msgs = [{"version": "v0.9", "createSurface": {"surfaceId": surface_id, "catalogId": "ember"}}]
    msgs.append({"version": "v0.9", "updateComponents": {"surfaceId": surface_id, "components": components}})
    if data:
        msgs.append({"version": "v0.9", "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": data}})
    return msgs


def _voucher_to_a2ui(voucher_front: dict, voucher_id: str, show_actions: bool = True, attachments: list | None = None) -> dict:
    """Convert voucher frontend dict to A2UI messages."""
    rows = voucher_front.get("rows", [])
    header_pairs = [
        {"label": "凭证号", "value": voucher_front.get("voucher_id", "—")},
        {"label": "公司代码", "value": voucher_front.get("company_code", "—")},
        {"label": "凭证类型", "value": voucher_front.get("document_type", "—")},
        {"label": "凭证日期", "value": voucher_front.get("document_date", "—")},
        {"label": "过账日期", "value": voucher_front.get("posting_date", "—")},
        {"label": "参考", "value": voucher_front.get("reference", "—")},
        {"label": "凭证头文本", "value": voucher_front.get("header_text", "—")},
        {"label": "置信度", "value": voucher_front.get("confidence", "—")},
    ]

    table_columns = [
        {"key": "line_no", "label": "行号"},
        {"key": "account_code", "label": "科目代码"},
        {"key": "account_name", "label": "科目名称"},
        {"key": "debit_credit", "label": "借/贷"},
        {"key": "debit", "label": "借方金额", "align": "right"},
        {"key": "credit", "label": "贷方金额", "align": "right"},
        {"key": "currency", "label": "币种"},
        {"key": "text", "label": "摘要"},
    ]
    table_rows = []
    for r in rows:
        table_rows.append({
            "line_no": str(r.get("line_no", "")),
            "account_code": r.get("account_code", ""),
            "account_name": r.get("account_name", ""),
            "debit_credit": "借" if r.get("debit_credit") == "S" else "贷",
            "debit": f"{r.get('debit', 0):,.2f}" if r.get("debit") else "",
            "credit": f"{r.get('credit', 0):,.2f}" if r.get("credit") else "",
            "currency": r.get("currency", "CNY"),
            "text": r.get("text", ""),
        })

    total_debit = sum(r.get("debit", 0) for r in rows)
    total_credit = sum(r.get("credit", 0) for r in rows)

    warnings = voucher_front.get("warnings", [])
    warning_components = []
    if warnings:
        warning_components.append({
            "id": "warnings", "component": "Text",
            "text": "⚠️ " + "；".join(warnings), "variant": "caption",
        })

    status = voucher_front.get("status", "draft")
    is_posted = status == "posted"

    components = [
        {"id": "back-btn", "component": "Button", "child": "back-text",
         "variant": "secondary", "action": {"event": {"name": "back_to_voucher_list"}}},
        {"id": "back-text", "component": "Text", "text": "← 返回列表"},
        {"id": "title", "component": "Text", "text": f"凭证 {voucher_id}", "variant": "h2"},
        {"id": "info-card", "component": "Card", "title": "凭证信息", "children": ["kv-info"]},
        {"id": "kv-info", "component": "KeyValue", "pairs": header_pairs},
        *warning_components,
        {"id": "rows-card", "component": "Card", "title": "凭证明细", "children": ["rows-table"]},
        {"id": "rows-table", "component": "DataTable",
         "columns": table_columns, "rows": table_rows,
         "footer": {"label": "合计", "values": ["", "", "", "",
                      f"{total_debit:,.2f}", f"{total_credit:,.2f}", "", ""]}},
    ]
    # Attachments section
    att_list = attachments or []
    att_table_rows = []
    for att in att_list:
        size_kb = round(att.get("file_size", 0) / 1024, 1)
        att_table_rows.append({
            "id": att.get("id", ""),
            "filename": att.get("filename", ""),
            "size": f"{size_kb} KB",
            "uploaded_by_name": att.get("uploaded_by_name", ""),
            "created_at": att.get("created_at", ""),
        })

    att_columns = [
        {"key": "filename", "label": "文件名"},
        {"key": "size", "label": "大小"},
        {"key": "uploaded_by_name", "label": "上传人"},
        {"key": "created_at", "label": "上传时间"},
    ]

    components.append(
        {"id": "attach-card", "component": "Card", "title": f"附件（{len(att_list)} 份）", "children": ["attach-table", "attach-btn-row"]},
    )
    components.append(
        {"id": "attach-table", "component": "DataTable",
         "columns": att_columns, "rows": att_table_rows},
    )
    components.append(
        {"id": "attach-btn-row", "component": "Row", "children": ["upload-attach-btn"]},
    )
    components.append(
        {"id": "upload-attach-btn", "component": "Button", "child": "upload-attach-text",
         "variant": "secondary",
         "action": {"event": {"name": "upload_attachment", "data": {"voucherId": voucher_id}}}},
    )
    components.append(
        {"id": "upload-attach-text", "component": "Text", "text": "上传附件"},
    )

    if show_actions:
        components.extend([
            {"id": "actions-row", "component": "Row", "children": ["confirm-btn", "edit-btn"]},
            {"id": "confirm-btn", "component": "Button", "child": "confirm-text",
             "variant": "primary", "disabled": is_posted,
             "action": {"event": {"name": "confirm_voucher", "data": {"voucherId": voucher_id}}}},
            {"id": "confirm-text", "component": "Text", "text": "已过账" if is_posted else "确认并记账"},
            {"id": "edit-btn", "component": "Button", "child": "edit-text",
             "variant": "secondary", "disabled": is_posted,
             "action": {"event": {"name": "edit_voucher", "data": {"voucherId": voucher_id}}}},
            {"id": "edit-text", "component": "Text", "text": "编辑凭证"},
        ])
    return _build_a2ui_messages("voucher-detail", components)


def _voucher_list_to_a2ui(records: list, total: int, status_filter: str | None) -> dict:
    """Convert voucher records list to A2UI messages."""
    status_label = {"draft": "草稿", "posted": "已过账"}.get(status_filter, "全部")

    tabs = [
        {"key": "", "label": "全部"},
        {"key": "draft", "label": "草稿"},
        {"key": "posted", "label": "已过账"},
    ]

    table_columns = [
        {"key": "voucher_id", "label": "凭证号"},
        {"key": "document_type", "label": "类型"},
        {"key": "document_date", "label": "日期"},
        {"key": "header_text", "label": "摘要"},
        {"key": "status", "label": "状态"},
        {"key": "created_at", "label": "创建时间"},
    ]
    table_rows = []
    for rec in records:
        status_text = "已过账" if rec.get("status") == "posted" else "草稿"
        table_rows.append({
            "voucher_id": rec.get("voucher_id", ""),
            "document_type": rec.get("document_type", ""),
            "document_date": rec.get("document_date", ""),
            "header_text": rec.get("header_text", ""),
            "status": status_text,
            "created_at": rec.get("created_at", ""),
        })

    components = [
        {"id": "title", "component": "Text", "text": f"凭证列表 — {status_label}（共 {total} 条）", "variant": "h2"},
        {"id": "filter-tabs", "component": "FilterTabs",
         "tabs": tabs, "active": status_filter or "",
         "action": {"event": {"name": "filter_vouchers"}}},
        {"id": "voucher-table", "component": "DataTable",
         "columns": table_columns, "rows": table_rows,
         "rowAction": {"event": {"name": "view_voucher_detail", "data": {"voucherId": "{voucher_id}"}}}},
    ]
    return _build_a2ui_messages("voucher-list", components)


def _rules_to_a2ui(rules_list: list, rule_type: str, rule_mgmt: dict | None = None) -> dict:
    """Convert rules list to A2UI messages."""
    biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type)

    table_columns = [
        {"key": "rule_code", "label": "规则编码"},
        {"key": "business_type", "label": "业务类型"},
        {"key": "product_type", "label": "产品类型"},
        {"key": "tax_rate", "label": "税率"},
        {"key": "document_type", "label": "凭证类型"},
        {"key": "line_count", "label": "分录行数"},
    ]
    table_rows = []
    for rule in rules_list:
        table_rows.append({
            "rule_code": rule.get("rule_code", ""),
            "business_type": rule.get("business_type", ""),
            "product_type": rule.get("product_type", ""),
            "tax_rate": str(rule.get("tax_rate", "")),
            "document_type": rule.get("document_type", ""),
            "line_count": str(len(rule.get("lines", []))),
        })

    action_buttons = []
    if rule_mgmt and rule_mgmt.get("action") == "create":
        action_buttons.append({
            "id": "add-rule-btn", "component": "Button", "child": "add-rule-text",
            "variant": "primary",
            "action": {"event": {"name": "create_rule", "data": {"ruleType": rule_type}}}},
        )
        action_buttons.append({"id": "add-rule-text", "component": "Text", "text": "新增规则"})

    components = [
        {"id": "title", "component": "Text", "text": f"凭证规则 — {biz_label}（共 {len(rules_list)} 条）", "variant": "h2"},
        {"id": "rules-table", "component": "DataTable",
         "columns": table_columns, "rows": table_rows},
        *action_buttons,
    ]
    return _build_a2ui_messages("rules", components)


def _users_to_a2ui(users: list) -> dict:
    """Convert users list to A2UI messages."""
    table_columns = [
        {"key": "username", "label": "用户名"},
        {"key": "display_name", "label": "显示名称"},
        {"key": "role", "label": "角色"},
        {"key": "created_at", "label": "创建时间"},
    ]
    table_rows = []
    for u in users:
        table_rows.append({
            "username": u.get("username", ""),
            "display_name": u.get("display_name", ""),
            "role": "管理员" if u.get("role") == "admin" else "普通用户",
            "created_at": u.get("created_at", ""),
        })

    components = [
        {"id": "title", "component": "Text", "text": f"用户管理（共 {len(users)} 人）", "variant": "h2"},
        {"id": "users-table", "component": "DataTable",
         "columns": table_columns, "rows": table_rows},
    ]
    return _build_a2ui_messages("users", components)


# ── Startup event ────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    await init_db()
    logger.info("Database initialized")
    migrated = await migrate_rules_from_excel()
    if migrated:
        logger.info("Migrated %d rules from Excel to database", migrated)
    seeded = await seed_default_rules()
    if seeded:
        logger.info("Seeded %d default rules", seeded)

    # Shared workspace for context offloading
    workspace = LocalWorkspace(workdir=str(Path(__file__).parent / "data" / "workspace"))
    await workspace.initialize()
    app.state.workspace = workspace
    logger.info("Workspace initialized: %s", workspace.workdir)

    # Initialize agents
    app.state.intent_agent = IntentAgent("intent_agent", offloader=workspace)
    app.state.voucher_agent = VoucherAgent("voucher_agent", offloader=workspace)
    app.state.ocr_agent = OcrAgent("ocr_agent")
    logger.info("Agents initialized")


@app.on_event("shutdown")
async def shutdown():
    workspace = getattr(app.state, "workspace", None)
    if workspace:
        await workspace.close()
        logger.info("Workspace closed")


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
async def chat_endpoint(payload: dict, request: Request):
    return StreamingResponse(_chat_stream(payload, request), media_type="text/event-stream")


async def _chat_stream(payload: dict, request: Request):
    """Accept a natural language message and optional session_id, return AI response + voucher data."""
    try:
        user = await _require_auth(request)
    except Exception:
        yield _sse({"type": "error", "reply": "登录已过期，请重新登录。"})
        return
    message = payload.get("message", "").strip()
    session_id = payload.get("session_id")
    # Session timeout: if session inactive for > SESSION_TIMEOUT_HOURS, start new session
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
        for hist in history_for_llm[-200:]:
            role = hist.get("role", "user")
            content = hist.get("content", "")
            if role == "assistant":
                await app.state.intent_agent.observe(AssistantMsg(name="assistant", content=content))
            else:
                await app.state.intent_agent.observe(UserMsg(name="user", content=content))
        intent_msg = UserMsg(name="user", content=message)

        # Streaming intent classification
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
            parse_result = None  # Will trigger keyword fallback below

    logger.info("NL parse result for '%s': %s", message[:60], parse_result)
    if parse_result is None:
        # LLM failed — try keyword fallback before giving up
        parse_result = {"intent": "chat", "reply": f"你好！我是 {AGENT_NAME}，{AGENT_CAPABILITIES.replace('、', '、')}。有什么可以帮你的吗？", "business_type": None, "transaction": None}

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

        # Pending action continuation: if last assistant message was asking for info, continue that flow
        if pending_action_from_history == "user_mgmt_create":
            parse_result = {"intent": "user_mgmt", "action": "create", "new_username": message.strip(), "new_display_name": None, "new_role": "user", "new_password": None, "reply": "", "business_type": None, "transaction": None}
            logger.info("Pending action continuation: user_mgmt_create, username='%s'", message.strip())

        elif any(kw in msg_lower for kw in voucher_keywords):
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
            yield _sse({"type": "result", **{
                "reply": reply,
                "session_id": session_id,
            }})
            return

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
            yield _sse({"type": "result", **{
                "reply": reply,
                "session_id": session_id,
            }})
            return

        # User specified a type — load from database and return matching rules
        try:
            rules_list = await list_rules(business_type=rule_type)
            rules_list = _format_rules_for_frontend(rules_list)
        except Exception as exc:
            logger.error("Failed to load voucher rules: %s", exc)
            yield _sse({"type": "result", **{
                "reply": "加载凭证规则时出错，请稍后重试。",
                "session_id": session_id,
            }})
            return

        biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type)

        if not rules_list:
            if not reply:
                reply = f"暂无「{biz_label}」类型的凭证规则配置。"
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id, "view": "rules", "rules": [], "rule_type": rule_type, "a2ui": {"messages": _rules_to_a2ui([], rule_type)}}})
            return

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

        yield _sse({"type": "result", **{
            "reply": reply,
            "session_id": session_id,
            "rules": rules_list,
            "rule_type": rule_type,
            "view": "rules",
            "a2ui": {"messages": _rules_to_a2ui(rules_list, rule_type)},
        }})
        return

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
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
            return

        biz_label = BIZ_TYPE_LABELS.get(rule_type, rule_type)

        # Admin permission check for all write operations
        if user["role"] != "admin":
            reply = f"抱歉，只有管理员才能管理凭证规则。"
            await save_chat_message(
                session_id=chat_session_id, user_id=user["id"],
                role="assistant", content=reply, message_type="chat",
            )
            yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
            return

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

            yield _sse({"type": "result", **{
                "reply": reply,
                "session_id": session_id,
                "view": "rules",
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
                await save_chat_message(
                    session_id=chat_session_id, user_id=user["id"],
                    role="assistant", content=reply, message_type="chat",
                )
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

            await add_audit_log(
                action=f"rule.{action}_start",
                user_id=user["id"],
                username=user["username"],
                target_type="rule",
                details={"rule_type": rule_type},
            )

            yield _sse({"type": "result", **{
                "reply": reply,
                "session_id": session_id,
                "view": "rules",
                "rules": rules_list,
                "rule_mgmt": {"action": action, "rule_type": rule_type},
                "a2ui": {"messages": _rules_to_a2ui(rules_list, rule_type, {"action": action, "rule_type": rule_type})},
            }})
            return

    # Handle voucher_query intent — show user's voucher records
    if parse_result.get("intent") == "voucher_query":
        try:
            status_filter = parse_result.get("status")
            reply = parse_result.get("reply", "")

            # Regular users see only their own; admins see all
            user_id = None if user["role"] == "admin" else user["id"]
            records = await list_voucher_records(user_id=user_id, status=status_filter, limit=50, offset=0)
            total = await count_voucher_records(user_id=user_id, status=status_filter)
            logger.info("voucher_query: user=%s status=%s records=%d total=%d", user["username"], status_filter, len(records), total)

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
                yield _sse({"type": "result", **{
                    "reply": reply,
                    "session_id": session_id,
                    "view": "voucher_list",
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
                "reply": reply,
                "session_id": session_id,
                "view": "voucher_list",
                "view_data": {"vouchers": records, "total": total, "status_filter": status_filter},
                "a2ui": {"messages": a2ui_msgs},
            }})
            return
        except Exception as exc:
            logger.error("voucher_query error: %s", exc, exc_info=True)
            yield _sse({"type": "result", "reply": f"查询凭证时出错：{exc}", "session_id": session_id})
            return

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

            # Generate default password if not provided
            if not new_password:
                import secrets
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
                yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
                return

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

            yield _sse({"type": "result", **{
                "reply": reply,
                "session_id": session_id,
                "view": "user_list",
                "view_data": {"users": users},
                "a2ui": {"messages": _users_to_a2ui(users)},
            }})
            return

        # Default reply for unknown user_mgmt action
        if not reply:
            reply = "请告诉我您要如何管理用户，例如「添加用户zhangsan」。"
        await save_chat_message(
            session_id=chat_session_id, user_id=user["id"],
            role="assistant", content=reply, message_type="chat",
        )
        yield _sse({"type": "result", **{"reply": reply, "session_id": session_id}})
        return

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
        yield _sse({"type": "result", **{
            "reply": reply,
            "session_id": session_id,
        }})
        return

    voucher_msg = UserMsg(name="user", content=json.dumps(asdict(txn), ensure_ascii=False, default=str), metadata={"transaction": txn, "business_type": business_type})
    voucher_result = await app.state.voucher_agent.reply(voucher_msg)
    voucher = voucher_result.metadata.get("voucher") if voucher_result.metadata else None
    if not voucher:
        yield _sse({"type": "result", **{"reply": "凭证生成失败，请重试。", "session_id": session_id}})
        return
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

    yield _sse({"type": "result", **{
        "reply": reply,
        "session_id": session_id,
        "voucher": voucher_front,
        "view": "voucher",
        "a2ui": {"messages": _voucher_to_a2ui(voucher_front, voucher.voucher_id)},
    }})
    return


# ── API: File Upload ─────────────────────────────────────────────────────────


@app.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str | None = None,
):
    """Upload an Excel or image file, parse it, generate vouchers via LLM."""
    return StreamingResponse(_upload_stream(request, file, session_id), media_type="text/event-stream")


async def _upload_stream(request: Request, file: UploadFile, session_id: str | None):
    try:
        async for event in _upload_file_impl(request, file, session_id):
            yield event
    except Exception as exc:
        logger.error("Upload error: %s", exc, exc_info=True)
        yield _sse({"type": "error", "reply": f"文件处理出错：{exc}"})


async def _upload_file_impl(
    request: Request,
    file: UploadFile,
    session_id: str | None,
):
    user = await _require_auth(request)
    chat_session_id = session_id or str(uuid.uuid4())
    session_id, session = _get_session(session_id, user_id=user["id"])

    # Save user message
    await save_chat_message(
        session_id=chat_session_id,
        user_id=user["id"],
        role="user",
        content=f"上传文件: {file.filename}",
        message_type="upload",
        metadata={"filename": file.filename},
    )

    yield _sse({"type": "progress", "text": f"正在保存文件 {file.filename}..."})

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
        await save_attachment(
            voucher_id=voucher.voucher_id,
            filename=file.filename or f"{file_id}{suffix}",
            file_path=str(saved_path),
            file_size=file_info["size"],
            content_type=file.content_type or "",
            uploaded_by=user["id"],
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

        biz_label = BIZ_TYPE_LABELS.get(business_type, business_type)
        reply = f"已从{source_label}中识别出1笔{biz_label}交易，生成了1张凭证草稿。"
        await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="upload", metadata={"voucher_id": voucher.voucher_id})

        attachments = await list_attachments(voucher.voucher_id)
        yield _sse({"type": "result", **{
            "reply": reply,
            "session_id": session_id,
            "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
            "vouchers": [voucher_front],
            "a2ui": {"messages": _voucher_to_a2ui(voucher_front, voucher_front["voucher_id"], attachments=attachments)},
        }})
        return

    # ── Excel path ──
    yield _sse({"type": "progress", "text": "正在解析Excel文件..."})

    try:
        transactions = load_sales_transactions(saved_path)
    except Exception as exc:
        logger.warning("Failed to parse uploaded file: %s", exc)
        yield _sse({"type": "error", "reply": f"文件解析失败：{exc}。请确保Excel格式正确。"})
        return

    vouchers = []
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
    if skipped:
        details = "、".join(f"{txn.transaction_id}({txn.customer_name})" for txn in skipped)
        reply += f"\n跳过 {len(skipped)} 笔已存在的凭证：{details}"
    if failed:
        details = "、".join(f"{txn.transaction_id}({txn.customer_name})" for txn in failed)
        reply += f"\n{len(failed)} 笔生成失败：{details}"

    # Export to SAP CSV
    output_path = PROJECT_ROOT / "data" / "output" / f"sap_{file_id}.csv"
    export_sap_csv(vouchers, output_path)

    await save_chat_message(session_id=chat_session_id, user_id=user["id"], role="assistant", content=reply, message_type="upload", metadata={"voucher_count": len(vouchers)})

    voucher_fronts = [_voucher_to_front(v) for v in vouchers]
    last_voucher_front = voucher_fronts[-1] if voucher_fronts else {}
    last_voucher_id = vouchers[-1].voucher_id if vouchers else ""
    yield _sse({"type": "result", **{
        "reply": reply,
        "session_id": session_id,
        "file": {"name": file.filename, "size_kb": round(file_info["size"] / 1024, 1)},
        "vouchers": voucher_fronts,
        "a2ui": {"messages": _voucher_to_a2ui(last_voucher_front, last_voucher_id)} if voucher_fronts else {},
    }})


# ── API: A2UI Action ─────────────────────────────────────────────────────────


@app.post("/api/a2ui-action")
async def handle_a2ui_action(request: Request):
    """Handle A2UI component action events from the frontend."""
    user = await _require_auth(request)
    body = await request.json()
    event_name = body.get("event", "")
    event_data = body.get("data", {})

    logger.info("A2UI action: event=%s data=%s user=%s", event_name, event_data, user["username"])

    # Route by event name
    if event_name == "confirm_voucher":
        voucher_id = event_data.get("voucherId", "")
        if not voucher_id:
            return JSONResponse({"status": "error", "message": "缺少凭证ID"})
        # Check current status
        db_record = await get_voucher_record(voucher_id)
        if not db_record:
            return JSONResponse({"status": "error", "message": f"凭证 {voucher_id} 不存在"})
        if db_record.get("status") == "posted":
            return JSONResponse({"status": "already_posted", "message": f"凭证 {voucher_id} 已经过账", "a2ui": {"messages": []}})
        # Post the voucher
        _append_posted_csv_from_record(db_record)
        await mark_voucher_posted(voucher_id, user["id"])
        await add_audit_log(
            action="voucher.post", user_id=user["id"], username=user["username"],
            target_type="voucher", target_id=voucher_id, details={"source": "a2ui_action"},
        )
        logger.info("Voucher %s posted via A2UI action by %s", voucher_id, user["username"])
        return JSONResponse({
            "status": "posted",
            "message": f"凭证 {voucher_id} 已成功过账",
            "a2ui": {"messages": []},
        })

    if event_name == "filter_vouchers":
        # Return filtered voucher list as A2UI messages
        status_filter = event_data.get("active", "")
        user_id = None if user["role"] == "admin" else user["id"]
        records = await list_voucher_records(user_id=user_id, status=status_filter or None, limit=50, offset=0)
        total = await count_voucher_records(user_id=user_id, status=status_filter or None)
        return JSONResponse({
            "status": "ok",
            "a2ui": {"messages": _voucher_list_to_a2ui(records, total, status_filter or None)},
        })

    if event_name == "create_rule":
        rule_type = event_data.get("ruleType", "")
        return JSONResponse({
            "status": "ok",
            "message": f"创建规则: {rule_type}",
            "a2ui": {"messages": _rules_to_a2ui([], rule_type, {"action": "create", "rule_type": rule_type})},
        })

    if event_name in ("view_voucher_detail", "edit_voucher"):
        voucher_id = event_data.get("voucherId", "")
        db_record = await get_voucher_record(voucher_id)
        if not db_record:
            return JSONResponse({"status": "error", "message": f"凭证 {voucher_id} 不存在"})
        voucher_front = json.loads(db_record.get("voucher_data") or "{}")
        voucher_front["status"] = db_record.get("status", "draft")
        show_actions = event_name == "edit_voucher"
        attachments = await list_attachments(voucher_id)
        return JSONResponse({
            "status": "ok",
            "a2ui": {"messages": _voucher_to_a2ui(voucher_front, voucher_id, show_actions=show_actions, attachments=attachments)},
        })

    if event_name == "upload_attachment":
        voucher_id = event_data.get("voucherId", "")
        return JSONResponse({
            "status": "open_file_picker",
            "voucherId": voucher_id,
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
        if record.get("status") == "posted":
            return JSONResponse({"status": "error", "message": "已过账凭证不可编辑"})

        rows = voucher_data.get("rows", [])
        total_debit = sum(r.get("debit", 0) for r in rows)
        total_credit = sum(r.get("credit", 0) for r in rows)
        if abs(total_debit - total_credit) > 0.01:
            return JSONResponse({"status": "error", "message": f"借贷不平衡：借方 {total_debit:.2f}，贷方 {total_credit:.2f}"})

        updated = await update_voucher_record(
            voucher_id,
            voucher_data=voucher_data,
            header_text=voucher_data.get("header_text", ""),
            document_date=voucher_data.get("document_date", ""),
            posting_date=voucher_data.get("posting_date", ""),
        )
        if not updated:
            return JSONResponse({"status": "error", "message": "更新失败"})

        await add_audit_log(
            action="voucher.edit",
            user_id=user["id"],
            username=user["username"],
            target_type="voucher",
            target_id=voucher_id,
        )

        # Return refreshed voucher detail
        voucher_front = json.loads(record.get("voucher_data") or "{}")
        voucher_front["status"] = record.get("status", "draft")
        attachments = await list_attachments(voucher_id)
        return JSONResponse({
            "status": "ok",
            "message": f"凭证 {voucher_id} 已更新",
            "a2ui": {"messages": _voucher_to_a2ui(voucher_front, voucher_id, show_actions=True, attachments=attachments)},
        })

    return JSONResponse({"status": "unknown_event", "message": f"未处理的事件: {event_name}"})


# ── API: Confirm Voucher ─────────────────────────────────────────────────────


@app.post("/api/confirm")
async def confirm_voucher(payload: dict, request: Request):
    """Mark a voucher as posted: append to posted_vouchers.csv + update DB + audit."""
    user = await _require_auth(request)
    session_id = payload.get("session_id")
    voucher_id = payload.get("voucher_id")
    session_id, session = _get_session(session_id, user_id=user["id"])

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


@app.put("/api/vouchers/{voucher_id}")
async def api_update_voucher(voucher_id: str, payload: dict, request: Request):
    """Update a voucher record (only draft vouchers can be edited)."""
    user = await _require_auth(request)

    record = await get_voucher_record(voucher_id)
    if not record:
        return JSONResponse({"error": "凭证不存在"}, status_code=404)
    if record.get("status") == "posted":
        return JSONResponse({"error": "已过账的凭证不可编辑"}, status_code=400)

    voucher_data = payload.get("voucher_data", {})
    rows = voucher_data.get("rows", [])

    # Validate debit/credit balance
    total_debit = sum(r.get("debit", 0) for r in rows)
    total_credit = sum(r.get("credit", 0) for r in rows)
    if abs(total_debit - total_credit) > 0.01:
        return JSONResponse({"error": f"借贷不平衡：借方 {total_debit:.2f}，贷方 {total_credit:.2f}"}, status_code=400)

    updated = await update_voucher_record(
        voucher_id,
        voucher_data=voucher_data,
        header_text=voucher_data.get("header_text", record.get("header_text", "")),
        document_date=voucher_data.get("document_date", record.get("document_date", "")),
        posting_date=voucher_data.get("posting_date", record.get("posting_date", "")),
    )
    if not updated:
        return JSONResponse({"error": "更新失败"}, status_code=500)

    await add_audit_log(
        action="voucher.edit",
        user_id=user["id"],
        username=user["username"],
        target_type="voucher",
        target_id=voucher_id,
    )

    return JSONResponse({"status": "ok", "message": f"凭证 {voucher_id} 已更新"})


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


@app.get("/api/my-chat-history")
async def api_my_chat_history(request: Request, limit: int = 50):
    """Return the current user's most recent session messages for chat restoration."""
    user = await _require_auth(request)

    # Find the user's most recent session by latest message
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
    messages.reverse()  # chronological order (oldest first)

    return JSONResponse({
        "session_id": session_id,
        "messages": messages,
    })


# ── API: Attachments ─────────────────────────────────────────────────────────


@app.get("/api/vouchers/{voucher_id}/attachments")
async def api_list_attachments(voucher_id: str, request: Request):
    """List attachments for a voucher."""
    user = await _require_auth(request)
    attachments = await list_attachments(voucher_id)
    return JSONResponse({"attachments": attachments})


@app.post("/api/vouchers/{voucher_id}/attachments")
async def api_upload_attachment(voucher_id: str, request: Request, file: UploadFile = File(...)):
    """Upload a file as an attachment to a voucher."""
    user = await _require_auth(request)

    # Verify voucher exists
    db_record = await get_voucher_record(voucher_id)
    if not db_record:
        return JSONResponse({"error": "凭证不存在"}, status_code=404)

    # Save file to disk
    file_id = str(uuid.uuid4())[:8]
    suffix = Path(file.filename or "upload").suffix.lower()
    saved_path = UPLOAD_DIR / f"{file_id}{suffix}"
    with saved_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = saved_path.stat().st_size
    content_type = file.content_type or ""

    att_id = await save_attachment(
        voucher_id=voucher_id,
        filename=file.filename or f"{file_id}{suffix}",
        file_path=str(saved_path),
        file_size=file_size,
        content_type=content_type,
        uploaded_by=user["id"],
    )

    await add_audit_log(
        action="attachment.upload",
        user_id=user["id"],
        username=user["username"],
        target_type="attachment",
        target_id=att_id,
        details={"voucher_id": voucher_id, "filename": file.filename},
    )

    return JSONResponse({
        "status": "ok",
        "attachment_id": att_id,
        "filename": file.filename,
        "file_size": file_size,
    })


@app.delete("/api/attachments/{attachment_id}")
async def api_delete_attachment(attachment_id: str, request: Request):
    """Delete an attachment."""
    user = await _require_auth(request)
    deleted = await delete_attachment(attachment_id)
    if not deleted:
        return JSONResponse({"error": "附件不存在"}, status_code=404)
    await add_audit_log(
        action="attachment.delete",
        user_id=user["id"],
        username=user["username"],
        target_type="attachment",
        target_id=attachment_id,
    )
    return JSONResponse({"status": "ok"})


# ── Serve static frontend ────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(PROJECT_ROOT), html=True), name="static")


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
