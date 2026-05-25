"""SQLite database layer for Ember AI Accounting.

Provides persistent storage for:
  - Users (with role-based access)
  - Voucher records (with audit trail)
  - Chat messages (conversation history)
  - Audit logs (operation tracking)
"""

import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "ember.db"

# ── Password hashing ──────────────────────────────────────────────────────────


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a password with a random salt using SHA-256."""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return hashed, salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verify a password against its hash and salt."""
    computed, _ = _hash_password(password, salt)
    return secrets.compare_digest(computed, hashed)


# ── Database initialization ───────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',  -- 'user' or 'admin'
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS voucher_records (
    id TEXT PRIMARY KEY,
    voucher_id TEXT UNIQUE NOT NULL,
    session_id TEXT,
    user_id TEXT NOT NULL,
    company_code TEXT,
    document_type TEXT,
    document_date TEXT,
    posting_date TEXT,
    reference TEXT,
    header_text TEXT,
    confidence TEXT,
    warnings TEXT,  -- JSON array
    voucher_data TEXT NOT NULL,  -- Full voucher JSON
    status TEXT NOT NULL DEFAULT 'draft',  -- 'draft' or 'posted'
    created_at TEXT NOT NULL,
    posted_at TEXT,
    posted_by TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,  -- 'user' or 'assistant'
    content TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'chat',  -- 'chat', 'upload', 'confirm'
    metadata TEXT,  -- JSON for extra data (file info, voucher id, etc.)
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    username TEXT,
    action TEXT NOT NULL,  -- 'login', 'logout', 'voucher.generate', 'voucher.post', 'rule.view', 'user.create', etc.
    target_type TEXT,  -- 'voucher', 'rule', 'user', 'session'
    target_id TEXT,
    details TEXT,  -- JSON
    ip_address TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_voucher_records_user ON voucher_records(user_id);
CREATE INDEX IF NOT EXISTS idx_voucher_records_status ON voucher_records(status);
CREATE INDEX IF NOT EXISTS idx_voucher_records_created ON voucher_records(created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_user ON chat_messages(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);

CREATE TABLE IF NOT EXISTS voucher_rules (
    id TEXT PRIMARY KEY,
    rule_code TEXT NOT NULL,
    business_type TEXT NOT NULL,
    product_type TEXT NOT NULL DEFAULT '*',
    tax_rate TEXT NOT NULL DEFAULT '*',
    document_type TEXT NOT NULL DEFAULT 'DR',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voucher_rule_lines (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    debit_credit TEXT NOT NULL,
    account_code TEXT NOT NULL,
    account_name TEXT NOT NULL,
    amount_field TEXT NOT NULL,
    customer_source TEXT NOT NULL DEFAULT '',
    tax_code_rule TEXT NOT NULL DEFAULT '',
    profit_center_source TEXT NOT NULL DEFAULT '',
    cost_center_source TEXT NOT NULL DEFAULT '',
    assignment_source TEXT NOT NULL DEFAULT '',
    text_template TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (rule_id) REFERENCES voucher_rules(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voucher_rules_code ON voucher_rules(rule_code);
CREATE INDEX IF NOT EXISTS idx_voucher_rules_business ON voucher_rules(business_type);
CREATE INDEX IF NOT EXISTS idx_voucher_rule_lines_rule ON voucher_rule_lines(rule_id);
"""


async def get_db() -> aiosqlite.Connection:
    """Get a database connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db() -> None:
    """Initialize the database schema and create default admin user."""
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        await db.commit()

        # Create default admin user if no users exist
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        row = await cursor.fetchone()
        if row[0] == 0:
            admin_id = str(uuid.uuid4())
            hashed, salt = _hash_password("admin123")
            await db.execute(
                """INSERT INTO users (id, username, password_hash, password_salt, display_name, role, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (admin_id, "admin", hashed, salt, "系统管理员", "admin",
                 datetime.now().isoformat()),
            )
            await db.commit()
            logger.info("Created default admin user (username=admin, password=admin123)")
    finally:
        await db.close()


# ── User operations ────────────────────────────────────────────────────────────


async def authenticate_user(username: str, password: str) -> dict | None:
    """Authenticate a user by username and password. Returns user dict or None."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, password_hash, password_salt, display_name, role, is_active FROM users WHERE username = ?",
            (username,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if not row["is_active"]:
            return None
        if not verify_password(password, row["password_hash"], row["password_salt"]):
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
        }
    finally:
        await db.close()


async def create_session_token(user_id: str) -> str:
    """Create a session token for a user. Returns the token string."""
    token = secrets.token_urlsafe(32)
    session_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO sessions (id, user_id, token, created_at)
               VALUES (?, ?, ?, ?)""",
            (session_id, user_id, token, datetime.now().isoformat()),
        )
        await db.commit()
        return token
    finally:
        await db.close()


async def get_user_by_token(token: str) -> dict | None:
    """Get user info from a session token."""
    if not token:
        return None
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT u.id, u.username, u.display_name, u.role
               FROM users u JOIN sessions s ON u.id = s.user_id
               WHERE s.token = ? AND u.is_active = 1""",
            (token,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
        }
    finally:
        await db.close()


async def delete_session(token: str) -> None:
    """Delete a session (logout)."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        await db.commit()
    finally:
        await db.close()


async def list_users() -> list[dict]:
    """List all users (admin only)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, display_name, role, is_active, created_at FROM users ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def create_user(username: str, password: str, display_name: str, role: str = "user") -> dict:
    """Create a new user. Returns user dict."""
    user_id = str(uuid.uuid4())
    hashed, salt = _hash_password(password)
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO users (id, username, password_hash, password_salt, display_name, role, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, hashed, salt, display_name, role,
             datetime.now().isoformat()),
        )
        await db.commit()
        return {"id": user_id, "username": username, "display_name": display_name, "role": role}
    finally:
        await db.close()


async def update_user(user_id: str, **kwargs) -> bool:
    """Update user fields. Returns True if updated."""
    allowed = {"display_name", "role", "is_active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    # Handle password update separately
    if "password" in kwargs and kwargs["password"]:
        hashed, salt = _hash_password(kwargs["password"])
        fields["password_hash"] = hashed
        fields["password_salt"] = salt

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]

    db = await get_db()
    try:
        cursor = await db.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?",
            values,
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def delete_user(user_id: str) -> bool:
    """Delete a user. Returns True if deleted."""
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


# ── Voucher record operations ─────────────────────────────────────────────────


async def save_voucher_record(
    voucher_id: str,
    user_id: str,
    voucher_data: dict,
    session_id: str | None = None,
    company_code: str = "",
    document_type: str = "",
    document_date: str = "",
    posting_date: str = "",
    reference: str = "",
    header_text: str = "",
    confidence: str = "",
    warnings: list | None = None,
) -> str:
    """Save a voucher record to the database. Returns the record ID."""
    record_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO voucher_records
               (id, voucher_id, session_id, user_id, company_code, document_type,
                document_date, posting_date, reference, header_text, confidence,
                warnings, voucher_data, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id, voucher_id, session_id, user_id, company_code,
                document_type, document_date, posting_date, reference,
                header_text, confidence,
                json.dumps(warnings or [], ensure_ascii=False),
                json.dumps(voucher_data, ensure_ascii=False),
                "draft",
                datetime.now().isoformat(),
            ),
        )
        await db.commit()
        return record_id
    finally:
        await db.close()


async def mark_voucher_posted(voucher_id: str, posted_by: str) -> bool:
    """Mark a voucher as posted. Returns True if updated."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """UPDATE voucher_records
               SET status = 'posted', posted_at = ?, posted_by = ?
               WHERE voucher_id = ?""",
            (datetime.now().isoformat(), posted_by, voucher_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def list_voucher_records(
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List voucher records with optional filters."""
    db = await get_db()
    try:
        conditions = []
        params: list[Any] = []

        if user_id:
            conditions.append("vr.user_id = ?")
            params.append(user_id)
        if status:
            conditions.append("vr.status = ?")
            params.append(status)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        cursor = await db.execute(
            f"""SELECT vr.*, u.display_name as user_display_name
                FROM voucher_records vr
                LEFT JOIN users u ON vr.user_id = u.id
                WHERE {where}
                ORDER BY vr.created_at DESC
                LIMIT ? OFFSET ?""",
            params,
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            record = dict(row)
            record["warnings"] = json.loads(record.get("warnings") or "[]")
            # Don't include full voucher_data in list view to save bandwidth
            record.pop("voucher_data", None)
            result.append(record)
        return result
    finally:
        await db.close()


async def get_voucher_record(voucher_id: str) -> dict | None:
    """Get a single voucher record by voucher_id."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT vr.*, u.display_name as user_display_name,
                      pu.display_name as posted_by_name
               FROM voucher_records vr
               LEFT JOIN users u ON vr.user_id = u.id
               LEFT JOIN users pu ON vr.posted_by = pu.id
               WHERE vr.voucher_id = ?""",
            (voucher_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        record = dict(row)
        record["warnings"] = json.loads(record.get("warnings") or "[]")
        return record
    finally:
        await db.close()


async def count_voucher_records(user_id: str | None = None, status: str | None = None) -> int:
    """Count voucher records with optional filters."""
    db = await get_db()
    try:
        conditions = []
        params: list[Any] = []
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions) if conditions else "1=1"
        cursor = await db.execute(f"SELECT COUNT(*) FROM voucher_records WHERE {where}", params)
        row = await cursor.fetchone()
        return row[0]
    finally:
        await db.close()


# ── Chat message operations ────────────────────────────────────────────────────


async def save_chat_message(
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    message_type: str = "chat",
    metadata: dict | None = None,
) -> str:
    """Save a chat message to the database. Returns the message ID."""
    msg_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO chat_messages (id, session_id, user_id, role, content, message_type, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id, session_id, user_id, role, content, message_type,
                json.dumps(metadata or {}, ensure_ascii=False),
                datetime.now().isoformat(),
            ),
        )
        await db.commit()
        return msg_id
    finally:
        await db.close()


async def list_chat_messages(
    session_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List chat messages with optional filters."""
    db = await get_db()
    try:
        conditions = []
        params: list[Any] = []
        if session_id:
            conditions.append("cm.session_id = ?")
            params.append(session_id)
        if user_id:
            conditions.append("cm.user_id = ?")
            params.append(user_id)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        cursor = await db.execute(
            f"""SELECT cm.*, u.display_name as user_display_name
                FROM chat_messages cm
                LEFT JOIN users u ON cm.user_id = u.id
                WHERE {where}
                ORDER BY cm.created_at DESC
                LIMIT ? OFFSET ?""",
            params,
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            record = dict(row)
            record["metadata"] = json.loads(record.get("metadata") or "{}")
            result.append(record)
        return result
    finally:
        await db.close()


# ── Audit log operations ───────────────────────────────────────────────────────


async def add_audit_log(
    action: str,
    user_id: str | None = None,
    username: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> str:
    """Add an audit log entry. Returns the log ID."""
    log_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO audit_logs (id, user_id, username, action, target_type, target_id, details, ip_address, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id, user_id, username, action, target_type, target_id,
                json.dumps(details or {}, ensure_ascii=False),
                ip_address,
                datetime.now().isoformat(),
            ),
        )
        await db.commit()
        return log_id
    finally:
        await db.close()


async def list_audit_logs(
    user_id: str | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List audit logs with optional filters."""
    db = await get_db()
    try:
        conditions = []
        params: list[Any] = []
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if action:
            conditions.append("action = ?")
            params.append(action)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        cursor = await db.execute(
            f"""SELECT * FROM audit_logs WHERE {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?""",
            params,
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            record = dict(row)
            record["details"] = json.loads(record.get("details") or "{}")
            result.append(record)
        return result
    finally:
        await db.close()


# ── Voucher rule operations ─────────────────────────────────────────────────


async def create_rule(
    rule_code: str,
    business_type: str,
    product_type: str = "*",
    tax_rate: str = "*",
    document_type: str = "DR",
    lines: list[dict] | None = None,
) -> dict:
    """Create a new voucher rule with its lines. Returns the rule dict."""
    rule_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO voucher_rules (id, rule_code, business_type, product_type, tax_rate, document_type, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (rule_id, rule_code, business_type, product_type, tax_rate, document_type, now, now),
        )
        for line in (lines or []):
            await db.execute(
                """INSERT INTO voucher_rule_lines
                   (id, rule_id, line_no, debit_credit, account_code, account_name, amount_field,
                    customer_source, tax_code_rule, profit_center_source, cost_center_source,
                    assignment_source, text_template)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), rule_id, line["line_no"], line["debit_credit"],
                    line["account_code"], line["account_name"], line["amount_field"],
                    line.get("customer_source", ""), line.get("tax_code_rule", ""),
                    line.get("profit_center_source", ""), line.get("cost_center_source", ""),
                    line.get("assignment_source", ""), line.get("text_template", ""),
                ),
            )
        await db.commit()
        return await get_rule(rule_code)
    finally:
        await db.close()


async def list_rules(business_type: str | None = None) -> list[dict]:
    """List all rules with their lines, optionally filtered by business_type."""
    db = await get_db()
    try:
        if business_type:
            cursor = await db.execute(
                "SELECT * FROM voucher_rules WHERE business_type = ? ORDER BY rule_code",
                (business_type,),
            )
        else:
            cursor = await db.execute("SELECT * FROM voucher_rules ORDER BY rule_code")
        rules = [dict(row) for row in await cursor.fetchall()]

        for rule in rules:
            cursor = await db.execute(
                "SELECT * FROM voucher_rule_lines WHERE rule_id = ? ORDER BY line_no",
                (rule["id"],),
            )
            rule["lines"] = [dict(row) for row in await cursor.fetchall()]
        return rules
    finally:
        await db.close()


async def get_rule(rule_code: str) -> dict | None:
    """Get a single rule by rule_code with its lines."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM voucher_rules WHERE rule_code = ?", (rule_code,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        rule = dict(row)
        cursor = await db.execute(
            "SELECT * FROM voucher_rule_lines WHERE rule_id = ? ORDER BY line_no",
            (rule["id"],),
        )
        rule["lines"] = [dict(row) for row in await cursor.fetchall()]
        return rule
    finally:
        await db.close()


async def update_rule(rule_code: str, lines: list[dict] | None = None, **kwargs) -> bool:
    """Update a rule's header fields and optionally replace all lines."""
    allowed = {"business_type", "product_type", "tax_rate", "document_type"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    now = datetime.now().isoformat()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM voucher_rules WHERE rule_code = ?", (rule_code,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        rule_id = row["id"]

        if fields:
            fields["updated_at"] = now
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            await db.execute(
                f"UPDATE voucher_rules SET {set_clause} WHERE id = ?",
                list(fields.values()) + [rule_id],
            )

        if lines is not None:
            await db.execute("DELETE FROM voucher_rule_lines WHERE rule_id = ?", (rule_id,))
            for line in lines:
                await db.execute(
                    """INSERT INTO voucher_rule_lines
                       (id, rule_id, line_no, debit_credit, account_code, account_name, amount_field,
                        customer_source, tax_code_rule, profit_center_source, cost_center_source,
                        assignment_source, text_template)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()), rule_id, line["line_no"], line["debit_credit"],
                        line["account_code"], line["account_name"], line["amount_field"],
                        line.get("customer_source", ""), line.get("tax_code_rule", ""),
                        line.get("profit_center_source", ""), line.get("cost_center_source", ""),
                        line.get("assignment_source", ""), line.get("text_template", ""),
                    ),
                )

        await db.commit()
        return True
    finally:
        await db.close()


async def delete_rule(rule_code: str) -> bool:
    """Delete a rule and all its lines. Returns True if deleted."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM voucher_rules WHERE rule_code = ?", (rule_code,)
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def migrate_rules_from_excel() -> int:
    """Migrate rules from Excel file to database. Returns number of rules migrated."""
    from voucher_rules import load_voucher_rule_lines

    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) FROM voucher_rules")
        count = (await cursor.fetchone())[0]
        if count > 0:
            return 0
    finally:
        await db.close()

    try:
        rule_lines = load_voucher_rule_lines()
    except Exception:
        return 0

    grouped: dict[str, list] = {}
    for rl in rule_lines:
        if rl.rule_code not in grouped:
            grouped[rl.rule_code] = {
                "business_type": rl.business_type,
                "product_type": rl.product_type,
                "tax_rate": rl.tax_rate,
                "document_type": rl.document_type,
                "lines": [],
            }
        grouped[rl.rule_code]["lines"].append({
            "line_no": rl.line_no,
            "debit_credit": rl.debit_credit,
            "account_code": rl.account_code,
            "account_name": rl.account_name,
            "amount_field": rl.amount_field,
            "customer_source": rl.customer_source,
            "tax_code_rule": rl.tax_code_rule,
            "profit_center_source": rl.profit_center_source,
            "cost_center_source": rl.cost_center_source,
            "assignment_source": rl.assignment_source,
            "text_template": rl.text_template,
        })

    for rule_code, data in grouped.items():
        await create_rule(
            rule_code=rule_code,
            business_type=data["business_type"],
            product_type=data["product_type"],
            tax_rate=data["tax_rate"],
            document_type=data["document_type"],
            lines=data["lines"],
        )
        logger.info("Migrated rule %s from Excel to database", rule_code)

    return len(grouped)
