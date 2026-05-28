"""Authentication and session management helpers."""

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from database import get_user_by_token, list_chat_messages
from voucher_models import Voucher, VoucherLine

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
SESSION_DIR = PROJECT_ROOT / "data" / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

# ── Constants ────────────────────────────────────────────────────────────────

SESSION_TIMEOUT_HOURS = 6


# ── Auth helpers ─────────────────────────────────────────────────────────────


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


def _voucher_to_json(voucher: Voucher) -> dict:
    from dataclasses import asdict
    from decimal import Decimal

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
            amount=ln["amount"],
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
    from decimal import Decimal
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


def _session_path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.json"


def _load_session(session_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    """Load session from disk. Returns None if session belongs to a different user."""
    path = _session_path(session_id)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
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
    if session is None:
        sid = str(uuid.uuid4())
        session = {"vouchers": [], "uploaded_files": [], "user_id": user_id}
    return sid, session


async def _is_session_expired(session_id: str) -> bool:
    """Check if a session has been inactive for too long."""
    from datetime import datetime, timedelta
    recent = await list_chat_messages(session_id=session_id, limit=1)
    if not recent:
        return False
    last_msg = recent[0]
    last_time = datetime.fromisoformat(last_msg["created_at"])
    if datetime.utcnow() - last_time > timedelta(hours=SESSION_TIMEOUT_HOURS):
        return True
    return False
