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
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import bcrypt

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "ember.db"

# ── Password hashing ──────────────────────────────────────────────────────────

SESSION_TIMEOUT_HOURS = 6


def _is_bcrypt_hash(hashed: str) -> bool:
    """Check if a hash string is in bcrypt format."""
    return hashed.startswith("$2")


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a password using bcrypt. Salt parameter is ignored (bcrypt generates its own)."""
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    return hashed, ""


def _hash_password_legacy(password: str, salt: str) -> tuple[str, str]:
    """Legacy SHA-256 hash for verifying old passwords."""
    hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return hashed, salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verify a password against its hash. Supports both bcrypt and legacy SHA-256."""
    if _is_bcrypt_hash(hashed):
        return bcrypt.checkpw(password.encode(), hashed.encode())
    computed, _ = _hash_password_legacy(password, salt)
    return secrets.compare_digest(computed, hashed)


# ── Database initialization ───────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',  -- 'user', 'admin', or 'reviewer'
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
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
    status TEXT NOT NULL DEFAULT 'draft',  -- 'draft', 'posted', or 'reversed'
    created_at TEXT NOT NULL,
    posted_at TEXT,
    posted_by TEXT,
    reversed_at TEXT,
    reversed_by TEXT,
    reversal_reason TEXT,
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

CREATE TABLE IF NOT EXISTS voucher_attachments (
    id TEXT PRIMARY KEY,
    voucher_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL DEFAULT '',
    uploaded_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (voucher_id) REFERENCES voucher_records(voucher_id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_voucher_attachments_voucher ON voucher_attachments(voucher_id);

CREATE TABLE IF NOT EXISTS login_attempts (
    ip TEXT NOT NULL,
    failed_count INTEGER NOT NULL DEFAULT 0,
    last_failed_at TEXT NOT NULL,
    PRIMARY KEY (ip)
);

CREATE TABLE IF NOT EXISTS approval_records (
    id TEXT PRIMARY KEY,
    voucher_id TEXT NOT NULL UNIQUE,
    requested_by TEXT NOT NULL,
    approver_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    action_at TEXT,
    action_by TEXT,
    comment TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (voucher_id) REFERENCES voucher_records(voucher_id) ON DELETE CASCADE,
    FOREIGN KEY (requested_by) REFERENCES users(id),
    FOREIGN KEY (approver_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_approval_records_approver ON approval_records(approver_id);
CREATE INDEX IF NOT EXISTS idx_approval_records_status ON approval_records(status);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(user_id, is_read);
"""


async def get_db() -> aiosqlite.Connection:
    """Get a database connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def _run_migrations(db: aiosqlite.Connection) -> None:
    """Run schema migrations for incremental upgrades."""
    # Migration: add must_change_password column if missing
    cursor = await db.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "must_change_password" not in columns:
        await db.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        await db.execute("UPDATE users SET must_change_password = 1 WHERE username = 'admin'")
        await db.commit()
        logger.info("Migration: added must_change_password column to users")

    # Migration: add reversal columns to voucher_records if missing
    cursor = await db.execute("PRAGMA table_info(voucher_records)")
    vr_columns = {row[1] for row in await cursor.fetchall()}
    if "reversed_at" not in vr_columns:
        await db.execute("ALTER TABLE voucher_records ADD COLUMN reversed_at TEXT")
        await db.execute("ALTER TABLE voucher_records ADD COLUMN reversed_by TEXT")
        await db.execute("ALTER TABLE voucher_records ADD COLUMN reversal_reason TEXT")
        await db.commit()
        logger.info("Migration: added reversal columns to voucher_records")

    # Migration: create notifications table if missing
    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notifications'")
    if not await cursor.fetchone():
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
            CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(user_id, is_read);
        """)
        await db.commit()
        logger.info("Migration: created notifications table")


async def _ensure_default_admin(db: aiosqlite.Connection) -> None:
    """Create default admin user if no users exist."""
    cursor = await db.execute("SELECT COUNT(*) FROM users")
    row = await cursor.fetchone()
    if row[0] == 0:
        admin_id = str(uuid.uuid4())
        hashed, salt = _hash_password("admin123")
        await db.execute(
            """INSERT INTO users (id, username, password_hash, password_salt, display_name, role, must_change_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (admin_id, "admin", hashed, salt, "系统管理员", "admin", 1,
             datetime.now().isoformat()),
        )
        await db.commit()
        logger.info("Created default admin user (username=admin, password=admin123)")


async def init_db() -> None:
    """Initialize the database schema and create default admin user."""
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        await db.commit()
        await _run_migrations(db)
        await _ensure_default_admin(db)
    finally:
        await db.close()


# ── User operations ────────────────────────────────────────────────────────────


async def authenticate_user(username: str, password: str) -> dict | None:
    """Authenticate a user by username and password. Returns user dict or None."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, password_hash, password_salt, display_name, role, is_active, must_change_password FROM users WHERE username = ?",
            (username,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if not row["is_active"]:
            return None
        if not verify_password(password, row["password_hash"], row["password_salt"]):
            return None

        # Auto-upgrade legacy SHA-256 hash to bcrypt
        if not _is_bcrypt_hash(row["password_hash"]):
            new_hash, _ = _hash_password(password)
            await db.execute(
                "UPDATE users SET password_hash = ?, password_salt = '' WHERE id = ?",
                (new_hash, row["id"]),
            )
            await db.commit()
            logger.info("Auto-upgraded password hash to bcrypt for user %s", row["username"])

        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "must_change_password": bool(row["must_change_password"]),
        }
    finally:
        await db.close()


async def create_session_token(user_id: str) -> str:
    """Create a session token for a user. Returns the token string."""
    token = secrets.token_urlsafe(32)
    session_id = str(uuid.uuid4())
    expires_at = (datetime.now() + timedelta(hours=SESSION_TIMEOUT_HOURS)).isoformat()
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO sessions (id, user_id, token, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, user_id, token, datetime.now().isoformat(), expires_at),
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
        now = datetime.now().isoformat()
        cursor = await db.execute(
            """SELECT u.id, u.username, u.display_name, u.role
               FROM users u JOIN sessions s ON u.id = s.user_id
               WHERE s.token = ? AND u.is_active = 1
               AND (s.expires_at IS NULL OR s.expires_at > ?)""",
            (token, now),
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


async def change_password(user_id: str, new_password: str) -> bool:
    """Change a user's password and clear must_change_password flag."""
    hashed, _ = _hash_password(new_password)
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE users SET password_hash = ?, password_salt = '', must_change_password = 0 WHERE id = ?",
            (hashed, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def clean_expired_sessions() -> int:
    """Delete all expired sessions. Returns number of deleted sessions."""
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "DELETE FROM sessions WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        await db.commit()
        count = cursor.rowcount
        if count:
            logger.info("Cleaned %d expired sessions", count)
        return count
    finally:
        await db.close()


async def list_users() -> list[dict]:
    """List all users (admin only)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, display_name, role, is_active, must_change_password, created_at FROM users ORDER BY created_at"
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

    # Handle password update separately
    if "password" in kwargs and kwargs["password"]:
        hashed, salt = _hash_password(kwargs["password"])
        fields["password_hash"] = hashed
        fields["password_salt"] = salt
        # Clear must_change_password flag when password is set
        fields["must_change_password"] = 0

    if not fields:
        return False

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


def _build_voucher_filter_conditions(
    user_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    table_alias: str = "",
) -> tuple[list[str], list[Any]]:
    """Build WHERE conditions and params for voucher record queries.

    Args:
        table_alias: Optional table alias prefix (e.g. "vr." for JOINs).

    Returns:
        (conditions, params) tuple.
    """
    prefix = f"{table_alias}." if table_alias else ""
    conditions: list[str] = []
    params: list[Any] = []

    if user_id:
        conditions.append(f"{prefix}user_id = ?")
        params.append(user_id)
    if status:
        conditions.append(f"{prefix}status = ?")
        params.append(status)
    if keyword:
        like = f"%{keyword}%"
        conditions.append(f"({prefix}header_text LIKE ? OR {prefix}reference LIKE ?)")
        params.extend([like, like])
    if date_from:
        conditions.append(f"{prefix}document_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append(f"{prefix}document_date <= ?")
        params.append(date_to)
    if amount_min is not None:
        conditions.append(f"CAST(json_extract({prefix}voucher_data, '$.total_amount') AS REAL) >= ?")
        params.append(amount_min)
    if amount_max is not None:
        conditions.append(f"CAST(json_extract({prefix}voucher_data, '$.total_amount') AS REAL) <= ?")
        params.append(amount_max)

    return conditions, params


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
) -> tuple[str, str]:
    """Save a voucher record to the database. Returns (record_id, voucher_id) tuple."""
    db = await get_db()
    try:
        # Check if voucher_id already exists
        cursor = await db.execute("SELECT id, user_id FROM voucher_records WHERE voucher_id = ?", (voucher_id,))
        existing = await cursor.fetchone()
        if existing:
            if existing["user_id"] == user_id:
                return existing["id"], voucher_id
            # Ownership conflict: generate a unique voucher_id for this user
            voucher_id = f"{voucher_id}-{uuid.uuid4().hex[:6]}"

        record_id = str(uuid.uuid4())
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
        return record_id, voucher_id
    finally:
        await db.close()


async def update_voucher_record(voucher_id: str, voucher_data: dict, **kwargs) -> bool:
    """Update a voucher record (only draft status allowed). Returns True if updated."""
    allowed = {"company_code", "document_type", "document_date", "posting_date", "reference", "header_text", "confidence", "warnings"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    fields["voucher_data"] = json.dumps(voucher_data, ensure_ascii=False)

    db = await get_db()
    try:
        # Only allow editing draft vouchers
        cursor = await db.execute("SELECT status FROM voucher_records WHERE voucher_id = ?", (voucher_id,))
        row = await cursor.fetchone()
        if row is None:
            return False
        if row["status"] != "draft":
            return False

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [voucher_id]
        cursor = await db.execute(
            f"UPDATE voucher_records SET {set_clause} WHERE voucher_id = ?",
            values,
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def mark_voucher_posted(voucher_id: str, posted_by: str) -> bool:
    """Mark a voucher as posted. Returns True if updated."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """UPDATE voucher_records
               SET status = 'posted', posted_at = ?, posted_by = ?
               WHERE voucher_id = ? AND status = 'draft'""",
            (datetime.now().isoformat(), posted_by, voucher_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def mark_voucher_reversed(voucher_id: str, reversed_by: str, reason: str) -> bool:
    """Mark a voucher as reversed. Returns True if updated."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """UPDATE voucher_records
               SET status = 'reversed', reversed_at = ?, reversed_by = ?, reversal_reason = ?
               WHERE voucher_id = ? AND status = 'posted'""",
            (datetime.now().isoformat(), reversed_by, reason, voucher_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def create_reversal_voucher(original_voucher_id: str, user_id: str, reason: str) -> str | None:
    """Create a reversal voucher and mark the original as reversed in one atomic transaction.
    Returns new voucher_id or None if original not found / already reversed."""
    db = await get_db()
    try:
        await db.execute("BEGIN")
        cursor = await db.execute(
            "SELECT * FROM voucher_records WHERE voucher_id = ? AND status = 'posted'",
            (original_voucher_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.execute("ROLLBACK")
            return None

        # Check no reversal already exists
        rev_check = await db.execute(
            "SELECT voucher_id FROM voucher_records WHERE voucher_id = ?",
            (f"REV-{original_voucher_id}",),
        )
        if await rev_check.fetchone():
            await db.execute("ROLLBACK")
            return None

        original = dict(row)
        voucher_data = json.loads(original.get("voucher_data") or "{}")

        # Swap debit/credit in line items
        rows = voucher_data.get("rows", [])
        reversal_rows = []
        for r in rows:
            new_r = dict(r)
            old_debit = new_r.get("debit", 0)
            old_credit = new_r.get("credit", 0)
            new_r["debit"] = old_credit
            new_r["credit"] = old_debit
            new_r["dc"] = "H" if old_debit > 0 else "S"
            orig_summary = new_r.get("text", "")
            new_r["text"] = f"冲销{original_voucher_id}: {orig_summary}" if orig_summary else f"冲销{original_voucher_id}"
            reversal_rows.append(new_r)

        reversal_data = dict(voucher_data)
        reversal_data["rows"] = reversal_rows
        reversal_data["header_text"] = f"冲销凭证 {original_voucher_id} - {reason}"

        new_voucher_id = f"REV-{original_voucher_id}"
        now = datetime.now().isoformat()
        record_id = str(uuid.uuid4())

        await db.execute(
            """INSERT INTO voucher_records
               (id, voucher_id, session_id, user_id, company_code, document_type,
                document_date, posting_date, reference, header_text, confidence,
                warnings, voucher_data, status, created_at, posted_at, posted_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'posted', ?, ?, ?)""",
            (
                record_id, new_voucher_id, original.get("session_id"), user_id,
                original.get("company_code", ""), original.get("document_type", ""),
                original.get("document_date", ""), original.get("posting_date", ""),
                original.get("reference", ""), reversal_data["header_text"],
                original.get("confidence", ""),
                json.dumps([], ensure_ascii=False),
                json.dumps(reversal_data, ensure_ascii=False),
                now, now, user_id,
            ),
        )

        await db.execute(
            """UPDATE voucher_records
               SET status = 'reversed', reversed_at = ?, reversed_by = ?, reversal_reason = ?
               WHERE voucher_id = ? AND status = 'posted'""",
            (now, user_id, reason, original_voucher_id),
        )

        await db.commit()
        return new_voucher_id
    except Exception:
        await db.execute("ROLLBACK")
        raise
    finally:
        await db.close()


async def batch_mark_voucher_posted(voucher_ids: list[str], posted_by: str) -> dict:
    """Batch mark vouchers as posted. Returns {posted: int, failed: int, errors: list}."""
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        posted = 0
        errors = []
        for vid in voucher_ids:
            try:
                cursor = await db.execute(
                    """UPDATE voucher_records
                       SET status = 'posted', posted_at = ?, posted_by = ?
                       WHERE voucher_id = ? AND status = 'draft'""",
                    (now, posted_by, vid),
                )
                if cursor.rowcount > 0:
                    posted += 1
                else:
                    errors.append(f"{vid}: 不存在或非草稿状态")
            except Exception as e:
                errors.append(f"{vid}: {str(e)}")
        await db.commit()
        return {"posted": posted, "failed": len(voucher_ids) - posted, "errors": errors}
    finally:
        await db.close()


async def list_voucher_records(
    user_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List voucher records with optional filters."""
    conditions, params = _build_voucher_filter_conditions(
        user_id=user_id, status=status, keyword=keyword,
        date_from=date_from, date_to=date_to,
        amount_min=amount_min, amount_max=amount_max,
        table_alias="vr",
    )
    where = " AND ".join(conditions) if conditions else "1=1"
    params.extend([limit, offset])

    db = await get_db()
    try:
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


async def count_voucher_records(
    user_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
) -> int:
    """Count voucher records with optional filters."""
    conditions, params = _build_voucher_filter_conditions(
        user_id=user_id, status=status, keyword=keyword,
        date_from=date_from, date_to=date_to,
        amount_min=amount_min, amount_max=amount_max,
    )
    where = " AND ".join(conditions) if conditions else "1=1"

    db = await get_db()
    try:
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


async def seed_default_rules() -> int:
    """Seed default rules (expense, etc.) into database if not present. Returns count seeded."""
    from voucher_rules import DEFAULT_EXPENSE_RULES

    db = await get_db()
    try:
        cursor = await db.execute("SELECT rule_code FROM voucher_rules")
        existing = {row["rule_code"] for row in await cursor.fetchall()}
    finally:
        await db.close()

    # Group DEFAULT_EXPENSE_RULES by rule_code
    grouped: dict[str, dict] = {}
    for row in DEFAULT_EXPENSE_RULES:
        rule_code = row[0]
        if rule_code in existing:
            continue
        if rule_code not in grouped:
            grouped[rule_code] = {
                "business_type": row[1],
                "product_type": row[2],
                "tax_rate": row[3],
                "document_type": row[4],
                "lines": [],
            }
        grouped[rule_code]["lines"].append({
            "line_no": row[5],
            "debit_credit": row[6],
            "account_code": row[7],
            "account_name": row[8],
            "amount_field": row[9],
            "customer_source": row[10],
            "tax_code_rule": row[11],
            "profit_center_source": row[12],
            "cost_center_source": row[13],
            "assignment_source": row[14],
            "text_template": row[15],
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
        logger.info("Seeded default rule: %s", rule_code)

    return len(grouped)


# ── Attachment operations ────────────────────────────────────────────────────


async def save_attachment(
    voucher_id: str,
    filename: str,
    file_path: str,
    file_size: int,
    content_type: str,
    uploaded_by: str,
) -> str:
    """Save an attachment record. Returns the attachment ID."""
    att_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO voucher_attachments (id, voucher_id, filename, file_path, file_size, content_type, uploaded_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (att_id, voucher_id, filename, file_path, file_size, content_type, uploaded_by, datetime.now().isoformat()),
        )
        await db.commit()
        return att_id
    finally:
        await db.close()


async def list_attachments(voucher_id: str) -> list[dict]:
    """List all attachments for a voucher."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT va.*, u.display_name as uploaded_by_name
               FROM voucher_attachments va
               LEFT JOIN users u ON va.uploaded_by = u.id
               WHERE va.voucher_id = ?
               ORDER BY va.created_at DESC""",
            (voucher_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def delete_attachment(attachment_id: str) -> bool:
    """Delete an attachment record. Returns True if deleted."""
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM voucher_attachments WHERE id = ?", (attachment_id,))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_attachment(attachment_id: str) -> dict | None:
    """Get a single attachment record by ID."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM voucher_attachments WHERE id = ?", (attachment_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ── Voucher ID generation ────────────────────────────────────────────────────


async def next_voucher_id(prefix: str, date_str: str) -> str:
    """Return the next unique voucher ID like VR-SO-20260529-003."""
    pattern = f"VR-{prefix}-{date_str}-%"
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM voucher_records WHERE voucher_id LIKE ?", (pattern,)
        )
        row = await cursor.fetchone()
        seq = (row[0] if row else 0) + 1
    finally:
        await db.close()
    return f"VR-{prefix}-{date_str}-{seq:03d}"


# ── Login rate limiting (SQLite-backed) ──────────────────────────────────────

_MAX_LOGIN_FAILS = 5
_LOCKOUT_SECONDS = 300


async def is_login_rate_limited(ip: str) -> bool:
    """Return True if the IP is currently locked out."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT failed_count, last_failed_at FROM login_attempts WHERE ip = ?", (ip,)
        )
        row = await cursor.fetchone()
        if not row:
            return False
        last = datetime.fromisoformat(row["last_failed_at"])
        if row["failed_count"] >= _MAX_LOGIN_FAILS and datetime.now() - last < timedelta(seconds=_LOCKOUT_SECONDS):
            return True
        if datetime.now() - last >= timedelta(seconds=_LOCKOUT_SECONDS):
            await db.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
            await db.commit()
        return False
    finally:
        await db.close()


async def record_login_failure(ip: str) -> None:
    """Increment failed login count for an IP."""
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        await db.execute(
            """INSERT INTO login_attempts (ip, failed_count, last_failed_at)
               VALUES (?, 1, ?)
               ON CONFLICT(ip) DO UPDATE SET
                 failed_count = failed_count + 1,
                 last_failed_at = excluded.last_failed_at""",
            (ip, now),
        )
        await db.commit()
    finally:
        await db.close()


async def clear_login_failures(ip: str) -> None:
    """Clear failed login count for an IP after successful login."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
        await db.commit()
    finally:
        await db.close()


# ── Approval flow ─────────────────────────────────────────────────────────────


async def submit_voucher_for_approval(voucher_id: str, requested_by: str, approver_id: str) -> bool:
    """Set voucher status to pending_approval and create an approval record."""
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "UPDATE voucher_records SET status='pending_approval' WHERE voucher_id=? AND status='draft'",
            (voucher_id,),
        )
        if cursor.rowcount == 0:
            return False
        await db.execute(
            """INSERT INTO approval_records (id, voucher_id, requested_by, approver_id, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)
               ON CONFLICT(voucher_id) DO UPDATE SET
                 requested_by=excluded.requested_by,
                 approver_id=excluded.approver_id,
                 status='pending',
                 action_at=NULL, action_by=NULL, comment=NULL,
                 created_at=excluded.created_at""",
            (str(uuid.uuid4()), voucher_id, requested_by, approver_id, now),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def approve_voucher(voucher_id: str, action_by: str, comment: str = "") -> bool:
    """Approve a pending voucher: mark as posted and update approval record."""
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "UPDATE voucher_records SET status='posted', posted_at=?, posted_by=? WHERE voucher_id=? AND status='pending_approval'",
            (now, action_by, voucher_id),
        )
        if cursor.rowcount == 0:
            return False
        await db.execute(
            "UPDATE approval_records SET status='approved', action_at=?, action_by=?, comment=? WHERE voucher_id=?",
            (now, action_by, comment, voucher_id),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def reject_voucher(voucher_id: str, action_by: str, comment: str = "") -> bool:
    """Reject a pending voucher: return to draft and update approval record."""
    db = await get_db()
    try:
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "UPDATE voucher_records SET status='draft' WHERE voucher_id=? AND status='pending_approval'",
            (voucher_id,),
        )
        if cursor.rowcount == 0:
            return False
        await db.execute(
            "UPDATE approval_records SET status='rejected', action_at=?, action_by=?, comment=? WHERE voucher_id=?",
            (now, action_by, comment, voucher_id),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def get_approval_record(voucher_id: str) -> dict | None:
    """Get the approval record for a voucher."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM approval_records WHERE voucher_id = ?", (voucher_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def list_pending_approvals(approver_id: str) -> list[dict]:
    """List vouchers pending approval for a specific approver."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT ar.*, vr.header_text, vr.document_date, vr.company_code,
                      u.display_name as requester_name
               FROM approval_records ar
               JOIN voucher_records vr ON ar.voucher_id = vr.voucher_id
               LEFT JOIN users u ON ar.requested_by = u.id
               WHERE ar.approver_id = ? AND ar.status = 'pending'
               ORDER BY ar.created_at DESC""",
            (approver_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


# ── Notification operations ──────────────────────────────────────────────────


async def create_notification(
    user_id: str, type: str, title: str, body: str,
    target_type: str | None = None, target_id: str | None = None,
) -> str:
    """Create a notification. Returns the notification ID."""
    notif_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO notifications (id, user_id, type, title, body, target_type, target_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (notif_id, user_id, type, title, body, target_type, target_id, datetime.now().isoformat()),
        )
        await db.commit()
        return notif_id
    finally:
        await db.close()


async def list_notifications(user_id: str, limit: int = 50, unread_only: bool = False) -> list[dict]:
    """List notifications for a user, newest first."""
    db = await get_db()
    try:
        where = "WHERE n.user_id = ?"
        params: list = [user_id]
        if unread_only:
            where += " AND n.is_read = 0"
        cursor = await db.execute(
            f"""SELECT n.*
                FROM notifications n
                {where}
                ORDER BY n.created_at DESC
                LIMIT ?""",
            params + [limit],
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def count_unread_notifications(user_id: str) -> int:
    """Count unread notifications for a user."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0
    finally:
        await db.close()


async def mark_notification_read(notification_id: str, user_id: str) -> bool:
    """Mark a single notification as read."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (notification_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def mark_all_notifications_read(user_id: str) -> int:
    """Mark all notifications as read for a user. Returns count of updated rows."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
            (user_id,),
        )
        await db.commit()
        return cursor.rowcount
    finally:
        await db.close()


async def delete_notification(notification_id: str, user_id: str) -> bool:
    """Delete a notification."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM notifications WHERE id = ? AND user_id = ?",
            (notification_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()
