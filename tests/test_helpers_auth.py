"""Comprehensive tests for helpers/auth.py (authentication, authorization, session persistence)."""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from helpers.auth import (
    _dict_to_voucher,
    _get_current_user,
    _get_session,
    _is_session_expired,
    _load_session,
    _require_admin,
    _require_auth,
    _save_session,
    _session_path,
    _voucher_to_json,
)
from voucher_models import Voucher, VoucherLine


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_request(auth_token=None):
    """Create a mock FastAPI Request with optional Authorization header."""
    request = MagicMock()
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request.headers = headers
    return request


def _sample_user(user_id="u1", role="user"):
    return {"id": user_id, "username": "testuser", "display_name": "Test", "role": role}


def _sample_voucher_obj() -> Voucher:
    return Voucher(
        voucher_id="VR-TEST-001",
        company_code="1000",
        document_type="DR",
        document_date="2026-05-31",
        posting_date="2026-05-31",
        reference="INV-001",
        header_text="Test voucher",
        source_transaction_id="TXN-001",
        confidence=Decimal("0.95"),
        warnings=["test warning"],
        lines=[
            VoucherLine(
                line_no=1, debit_credit="S", account_code="112200",
                account_name="Receivables", amount=Decimal("11300"),
                currency="CNY", customer_code="C001", customer_name="Customer A",
                tax_code="X1", profit_center="PC01", cost_center="CC01",
                assignment="A01", text="Entry 1",
            ),
            VoucherLine(
                line_no=2, debit_credit="H", account_code="600101",
                account_name="Revenue", amount=Decimal("10000"),
                currency="CNY", text="Entry 2",
            ),
        ],
    )


# ── _get_current_user() ──────────────────────────────────────────────────────


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_valid_bearer_token(self):
        request = _mock_request("valid-token-123")
        user = _sample_user()
        with patch("helpers.auth.get_user_by_token", new_callable=AsyncMock, return_value=user):
            result = await _get_current_user(request)
        assert result is not None
        assert result["id"] == "u1"

    @pytest.mark.asyncio
    async def test_no_auth_header(self):
        request = _mock_request()
        result = await _get_current_user(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_auth_format(self):
        request = MagicMock()
        request.headers = {"Authorization": "Basic abc123"}
        result = await _get_current_user(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_token(self):
        request = _mock_request("expired-token")
        with patch("helpers.auth.get_user_by_token", new_callable=AsyncMock, return_value=None):
            result = await _get_current_user(request)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_bearer_token(self):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer "}
        with patch("helpers.auth.get_user_by_token", new_callable=AsyncMock, return_value=None):
            result = await _get_current_user(request)
        assert result is None


# ── _require_auth() ──────────────────────────────────────────────────────────


class TestRequireAuth:
    @pytest.mark.asyncio
    async def test_authenticated_user(self):
        request = _mock_request("valid-token")
        user = _sample_user()
        with patch("helpers.auth.get_user_by_token", new_callable=AsyncMock, return_value=user):
            result = await _require_auth(request)
        assert result["id"] == "u1"

    @pytest.mark.asyncio
    async def test_unauthenticated_raises(self):
        from fastapi import HTTPException
        request = _mock_request()
        with pytest.raises(HTTPException) as exc_info:
            await _require_auth(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_raises(self):
        from fastapi import HTTPException
        request = _mock_request("bad-token")
        with patch("helpers.auth.get_user_by_token", new_callable=AsyncMock, return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await _require_auth(request)
        assert exc_info.value.status_code == 401


# ── _require_admin() ─────────────────────────────────────────────────────────


class TestRequireAdmin:
    @pytest.mark.asyncio
    async def test_admin_user(self):
        request = _mock_request("admin-token")
        admin = _sample_user(role="admin")
        with patch("helpers.auth.get_user_by_token", new_callable=AsyncMock, return_value=admin):
            result = await _require_admin(request)
        assert result["role"] == "admin"

    @pytest.mark.asyncio
    async def test_non_admin_raises(self):
        from fastapi import HTTPException
        request = _mock_request("user-token")
        user = _sample_user(role="user")
        with patch("helpers.auth.get_user_by_token", new_callable=AsyncMock, return_value=user):
            with pytest.raises(HTTPException) as exc_info:
                await _require_admin(request)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_raises_401(self):
        from fastapi import HTTPException
        request = _mock_request()
        with pytest.raises(HTTPException) as exc_info:
            await _require_admin(request)
        assert exc_info.value.status_code == 401


# ── _voucher_to_json() ───────────────────────────────────────────────────────


class TestVoucherToJson:
    def test_basic_conversion(self):
        voucher = _sample_voucher_obj()
        result = _voucher_to_json(voucher)
        assert result["voucher_id"] == "VR-TEST-001"
        assert result["company_code"] == "1000"

    def test_decimal_serialized_as_string(self):
        voucher = _sample_voucher_obj()
        result = _voucher_to_json(voucher)
        assert isinstance(result["confidence"], str)
        assert result["confidence"] == "0.95"

    def test_lines_converted(self):
        voucher = _sample_voucher_obj()
        result = _voucher_to_json(voucher)
        assert len(result["lines"]) == 2
        assert isinstance(result["lines"][0]["amount"], str)

    def test_warnings_preserved(self):
        voucher = _sample_voucher_obj()
        result = _voucher_to_json(voucher)
        assert result["warnings"] == ["test warning"]

    def test_json_serializable(self):
        voucher = _sample_voucher_obj()
        result = _voucher_to_json(voucher)
        # Should not raise
        json.dumps(result)

    def test_empty_lines(self):
        voucher = Voucher(
            voucher_id="VR-EMPTY", company_code="1000", document_type="DR",
            document_date="2026-01-01", posting_date="2026-01-01",
            reference="", header_text="", source_transaction_id="",
            confidence=Decimal("1.0"), lines=[],
        )
        result = _voucher_to_json(voucher)
        assert result["lines"] == []


# ── _dict_to_voucher() ───────────────────────────────────────────────────────


class TestDictToVoucher:
    def test_basic_conversion(self):
        data = {
            "voucher_id": "VR-001",
            "company_code": "1000",
            "document_type": "DR",
            "document_date": "2026-05-31",
            "posting_date": "2026-05-31",
            "reference": "INV-001",
            "header_text": "Test",
            "source_transaction_id": "TXN-001",
            "confidence": "0.95",
            "warnings": ["w1"],
            "lines": [
                {
                    "line_no": 1, "debit_credit": "S", "account_code": "111",
                    "account_name": "Cash", "amount": "100.00", "currency": "CNY",
                },
            ],
        }
        voucher = _dict_to_voucher(data)
        assert isinstance(voucher, Voucher)
        assert voucher.voucher_id == "VR-001"
        assert voucher.confidence == Decimal("0.95")

    def test_lines_converted(self):
        data = {
            "voucher_id": "VR-002", "company_code": "2000", "document_type": "DR",
            "document_date": "2026-01-01", "posting_date": "2026-01-01",
            "reference": "", "header_text": "", "source_transaction_id": "",
            "confidence": "1.0",
            "lines": [
                {
                    "line_no": 1, "debit_credit": "S", "account_code": "111",
                    "account_name": "A", "amount": "50", "currency": "USD",
                    "customer_code": "C1", "customer_name": "Cust",
                },
            ],
        }
        voucher = _dict_to_voucher(data)
        assert len(voucher.lines) == 1
        assert isinstance(voucher.lines[0], VoucherLine)
        assert voucher.lines[0].customer_code == "C1"

    def test_missing_optional_fields(self):
        data = {
            "voucher_id": "VR-003", "company_code": "3000", "document_type": "DR",
            "document_date": "2026-01-01", "posting_date": "2026-01-01",
            "reference": "", "header_text": "", "source_transaction_id": "",
            "confidence": "1.0",
            "lines": [
                {
                    "line_no": 1, "debit_credit": "H", "account_code": "222",
                    "account_name": "B", "amount": "50", "currency": "CNY",
                },
            ],
        }
        voucher = _dict_to_voucher(data)
        assert voucher.lines[0].customer_code == ""
        assert voucher.lines[0].tax_code == ""
        assert voucher.warnings == []

    def test_empty_lines(self):
        data = {
            "voucher_id": "VR-004", "company_code": "4000", "document_type": "DR",
            "document_date": "2026-01-01", "posting_date": "2026-01-01",
            "reference": "", "header_text": "", "source_transaction_id": "",
            "confidence": "1.0", "lines": [],
        }
        voucher = _dict_to_voucher(data)
        assert voucher.lines == []


# ── Round-trip: _voucher_to_json -> _dict_to_voucher ──────────────────────────


class TestVoucherRoundTrip:
    def test_round_trip_preserves_data(self):
        original = _sample_voucher_obj()
        json_data = _voucher_to_json(original)
        restored = _dict_to_voucher(json_data)
        assert restored.voucher_id == original.voucher_id
        assert restored.company_code == original.company_code
        assert restored.confidence == original.confidence
        assert len(restored.lines) == len(original.lines)
        assert restored.lines[0].account_code == original.lines[0].account_code


# ── Session persistence ──────────────────────────────────────────────────────


class TestSessionPersistence:
    def test_save_and_load_session(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        with patch("helpers.auth.SESSION_DIR", session_dir):
            voucher = _sample_voucher_obj()
            session = {
                "user_id": "u1",
                "title": "Test Session",
                "vouchers": [voucher],
                "uploaded_files": ["file.xlsx"],
            }
            _save_session("sess-001", session)
            loaded = _load_session("sess-001", user_id="u1")
            assert loaded is not None
            assert loaded["user_id"] == "u1"
            assert loaded["title"] == "Test Session"
            assert loaded["uploaded_files"] == ["file.xlsx"]
            assert len(loaded["vouchers"]) == 1
            assert isinstance(loaded["vouchers"][0], Voucher)

    def test_load_nonexistent_session(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        with patch("helpers.auth.SESSION_DIR", session_dir):
            result = _load_session("nonexistent", user_id="u1")
            assert result is not None
            assert result["vouchers"] == []
            assert result["title"] is None

    def test_load_session_wrong_user(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        with patch("helpers.auth.SESSION_DIR", session_dir):
            session = {"user_id": "u1", "title": "T", "vouchers": [], "uploaded_files": []}
            _save_session("sess-002", session)
            result = _load_session("sess-002", user_id="u2")
            assert result is None

    def test_get_session_new(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        with patch("helpers.auth.SESSION_DIR", session_dir):
            sid, session = _get_session(None, user_id="u1")
            assert sid is not None
            assert session["vouchers"] == []

    def test_get_session_existing(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        with patch("helpers.auth.SESSION_DIR", session_dir):
            session = {"user_id": "u1", "title": "T", "vouchers": [], "uploaded_files": []}
            _save_session("sess-003", session)
            sid, loaded = _get_session("sess-003", user_id="u1")
            assert sid == "sess-003"
            assert loaded["user_id"] == "u1"

    def test_get_session_wrong_user_generates_new(self, tmp_path):
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        with patch("helpers.auth.SESSION_DIR", session_dir):
            session = {"user_id": "u1", "title": "T", "vouchers": [], "uploaded_files": []}
            _save_session("sess-004", session)
            sid, loaded = _get_session("sess-004", user_id="u2")
            assert sid != "sess-004"
            assert loaded["vouchers"] == []


# ── _session_path() ──────────────────────────────────────────────────────────


class TestSessionPath:
    def test_path_contains_session_id(self):
        path = _session_path("my-session-123")
        assert path.name == "my-session-123.json"

    def test_path_is_json(self):
        path = _session_path("test")
        assert path.suffix == ".json"


# ── _is_session_expired() ────────────────────────────────────────────────────


class TestIsSessionExpired:
    @pytest.mark.asyncio
    async def test_no_messages_not_expired(self):
        with patch("helpers.auth.list_chat_messages", new_callable=AsyncMock, return_value=[]):
            result = await _is_session_expired("sess-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_recent_message_not_expired(self):
        from datetime import datetime
        recent = [{"created_at": datetime.utcnow().isoformat()}]
        with patch("helpers.auth.list_chat_messages", new_callable=AsyncMock, return_value=recent):
            result = await _is_session_expired("sess-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_old_message_expired(self):
        from datetime import datetime, timedelta
        old_time = (datetime.utcnow() - timedelta(hours=10)).isoformat()
        old = [{"created_at": old_time}]
        with patch("helpers.auth.list_chat_messages", new_callable=AsyncMock, return_value=old):
            result = await _is_session_expired("sess-001")
        assert result is True
