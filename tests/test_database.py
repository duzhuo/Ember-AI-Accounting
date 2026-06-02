"""Comprehensive tests for database.py CRUD operations, edge cases, and error handling."""

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import database as db


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_db_path(tmp_path):
    """Point DB_PATH to a temporary directory for every test."""
    with patch.object(db, "DB_PATH", tmp_path / "ember.db"):
        yield


@pytest_asyncio.fixture(autouse=True)
async def _init_database(tmp_path):
    """Initialize a fresh database for each test."""
    with patch.object(db, "DB_PATH", tmp_path / "ember.db"):
        await db.init_db()
        yield
        # cleanup handled by tmp_path


@pytest_asyncio.fixture
async def sample_user():
    """Create and return a sample user."""
    user = await db.create_user("testuser", "password123", "Test User", "user")
    return user


@pytest_asyncio.fixture
async def admin_user():
    """Create and return an admin user."""
    user = await db.create_user("testadmin", "adminpass", "Admin User", "admin")
    return user


@pytest_asyncio.fixture
async def sample_voucher(sample_user):
    """Create and return a sample voucher record."""
    voucher_data = {
        "rows": [
            {"line_no": 1, "debit_credit": "S", "debit": 100, "credit": 0},
            {"line_no": 2, "debit_credit": "H", "debit": 0, "credit": 100},
        ],
        "total_amount": 100,
    }
    record_id, voucher_id = await db.save_voucher_record(
        voucher_id="VR-TEST-20260531-001",
        user_id=sample_user["id"],
        voucher_data=voucher_data,
        company_code="1000",
        document_type="DR",
    )
    return {"record_id": record_id, "voucher_id": voucher_id, "user_id": sample_user["id"]}


# ── Password hashing ──────────────────────────────────────────────────────────


class TestPasswordHashing:
    def test_hash_password_returns_bcrypt(self):
        hashed, salt = db._hash_password("mypassword")
        assert hashed.startswith("$2")
        assert salt == ""

    def test_verify_password_bcrypt(self):
        hashed, _ = db._hash_password("secret")
        assert db.verify_password("secret", hashed, "") is True
        assert db.verify_password("wrong", hashed, "") is False

    def test_is_bcrypt_hash_true(self):
        assert db._is_bcrypt_hash("$2b$12$abc123") is True

    def test_is_bcrypt_hash_false(self):
        assert db._is_bcrypt_hash("abc123def") is False

    def test_verify_password_legacy(self):
        salt = "randomsalt"
        hashed, _ = db._hash_password_legacy("oldpass", salt)
        assert db.verify_password("oldpass", hashed, salt) is True
        assert db.verify_password("wrong", hashed, salt) is False


# ── Database initialization ───────────────────────────────────────────────────


class TestInitDb:
    @pytest.mark.asyncio
    async def test_init_creates_default_admin(self):
        """init_db should create a default admin user when DB is empty."""
        users = await db.list_users()
        admin = next((u for u in users if u["username"] == "admin"), None)
        assert admin is not None
        assert admin["role"] == "admin"

    @pytest.mark.asyncio
    async def test_init_idempotent(self):
        """Calling init_db twice should not fail."""
        await db.init_db()
        users = await db.list_users()
        assert len(users) >= 1


# ── User CRUD ─────────────────────────────────────────────────────────────────


class TestUserCRUD:
    @pytest.mark.asyncio
    async def test_create_user(self):
        user = await db.create_user("alice", "pass123", "Alice Wang", "user")
        assert user["username"] == "alice"
        assert user["display_name"] == "Alice Wang"
        assert user["role"] == "user"
        assert "id" in user

    @pytest.mark.asyncio
    async def test_create_user_duplicate_username(self):
        await db.create_user("bob", "pass1", "Bob", "user")
        with pytest.raises(Exception):
            await db.create_user("bob", "pass2", "Bob2", "user")

    @pytest.mark.asyncio
    async def test_list_users(self):
        await db.create_user("user1", "p1", "User 1", "user")
        await db.create_user("user2", "p2", "User 2", "reviewer")
        users = await db.list_users()
        usernames = {u["username"] for u in users}
        assert "user1" in usernames
        assert "user2" in usernames

    @pytest.mark.asyncio
    async def test_update_user_display_name(self, sample_user):
        result = await db.update_user(sample_user["id"], display_name="New Name")
        assert result is True
        users = await db.list_users()
        updated = next(u for u in users if u["id"] == sample_user["id"])
        assert updated["display_name"] == "New Name"

    @pytest.mark.asyncio
    async def test_update_user_role(self, sample_user):
        result = await db.update_user(sample_user["id"], role="reviewer")
        assert result is True

    @pytest.mark.asyncio
    async def test_update_user_password_only(self, sample_user):
        """Updating only the password should succeed (known trap in CLAUDE.md)."""
        result = await db.update_user(sample_user["id"], password="newsecret")
        assert result is True
        # Verify new password works
        auth_result = await db.authenticate_user("testuser", "newsecret")
        assert auth_result is not None

    @pytest.mark.asyncio
    async def test_update_user_no_valid_fields(self, sample_user):
        result = await db.update_user(sample_user["id"], nonexistent="value")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_nonexistent_user(self):
        result = await db.update_user("nonexistent-id", display_name="Ghost")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_user(self, sample_user):
        result = await db.delete_user(sample_user["id"])
        assert result is True
        users = await db.list_users()
        assert not any(u["id"] == sample_user["id"] for u in users)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_user(self):
        result = await db.delete_user("nonexistent-id")
        assert result is False


# ── Authentication ────────────────────────────────────────────────────────────


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_authenticate_user_success(self, sample_user):
        result = await db.authenticate_user("testuser", "password123")
        assert result is not None
        assert result["username"] == "testuser"
        assert result["role"] == "user"

    @pytest.mark.asyncio
    async def test_authenticate_user_wrong_password(self, sample_user):
        result = await db.authenticate_user("testuser", "wrongpass")
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_user_nonexistent(self):
        result = await db.authenticate_user("nobody", "pass")
        assert result is None

    @pytest.mark.asyncio
    async def test_authenticate_inactive_user(self, sample_user):
        await db.update_user(sample_user["id"], is_active=0)
        result = await db.authenticate_user("testuser", "password123")
        assert result is None


# ── Session management ────────────────────────────────────────────────────────


class TestSessionManagement:
    @pytest.mark.asyncio
    async def test_create_and_get_session_token(self, sample_user):
        token = await db.create_session_token(sample_user["id"])
        assert token is not None
        user = await db.get_user_by_token(token)
        assert user is not None
        assert user["id"] == sample_user["id"]

    @pytest.mark.asyncio
    async def test_get_user_by_empty_token(self):
        result = await db.get_user_by_token("")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_by_invalid_token(self):
        result = await db.get_user_by_token("invalid-token-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_session(self, sample_user):
        token = await db.create_session_token(sample_user["id"])
        await db.delete_session(token)
        result = await db.get_user_by_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_change_password(self, sample_user):
        result = await db.change_password(sample_user["id"], "newpassword")
        assert result is True
        auth = await db.authenticate_user("testuser", "newpassword")
        assert auth is not None

    @pytest.mark.asyncio
    async def test_change_password_nonexistent_user(self):
        result = await db.change_password("bad-id", "newpass")
        assert result is False

    @pytest.mark.asyncio
    async def test_clean_expired_sessions(self, sample_user):
        """Should clean up expired sessions."""
        token = await db.create_session_token(sample_user["id"])
        # Manually expire the session
        d = await db.get_db()
        past = (datetime.now() - timedelta(hours=10)).isoformat()
        await d.execute("UPDATE sessions SET expires_at = ? WHERE token = ?", (past, token))
        await d.commit()
        await d.close()
        count = await db.clean_expired_sessions()
        assert count >= 1
        assert await db.get_user_by_token(token) is None


# ── Voucher record CRUD ──────────────────────────────────────────────────────


class TestVoucherRecords:
    @pytest.mark.asyncio
    async def test_save_voucher_record(self, sample_user):
        data = {"rows": [{"debit": 50, "credit": 0}, {"debit": 0, "credit": 50}]}
        record_id, vid = await db.save_voucher_record(
            voucher_id="VR-001", user_id=sample_user["id"], voucher_data=data,
        )
        assert record_id is not None
        assert vid == "VR-001"

    @pytest.mark.asyncio
    async def test_save_duplicate_voucher_same_user(self, sample_user):
        """Same user saving same voucher_id returns existing record."""
        data = {"rows": []}
        rid1, vid1 = await db.save_voucher_record("VR-DUP", sample_user["id"], data)
        rid2, vid2 = await db.save_voucher_record("VR-DUP", sample_user["id"], data)
        assert rid1 == rid2
        assert vid1 == vid2

    @pytest.mark.asyncio
    async def test_save_duplicate_voucher_different_user(self, sample_user, admin_user):
        """Different user saving same voucher_id gets a modified voucher_id."""
        data = {"rows": []}
        _, vid1 = await db.save_voucher_record("VR-CLASH", sample_user["id"], data)
        _, vid2 = await db.save_voucher_record("VR-CLASH", admin_user["id"], data)
        assert vid1 != vid2
        assert vid2.startswith("VR-CLASH-")

    @pytest.mark.asyncio
    async def test_get_voucher_record(self, sample_voucher):
        record = await db.get_voucher_record(sample_voucher["voucher_id"])
        assert record is not None
        assert record["voucher_id"] == sample_voucher["voucher_id"]
        assert record["company_code"] == "1000"

    @pytest.mark.asyncio
    async def test_get_voucher_record_not_found(self):
        result = await db.get_voucher_record("NONEXISTENT")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_voucher_record_draft(self, sample_voucher):
        new_data = {"rows": [{"debit": 200, "credit": 0}, {"debit": 0, "credit": 200}]}
        result = await db.update_voucher_record(
            sample_voucher["voucher_id"], new_data, company_code="2000",
        )
        assert result is True
        record = await db.get_voucher_record(sample_voucher["voucher_id"])
        assert json.loads(record["voucher_data"]) == new_data

    @pytest.mark.asyncio
    async def test_update_voucher_record_nonexistent(self):
        result = await db.update_voucher_record("FAKE-ID", {"rows": []})
        assert result is False

    @pytest.mark.asyncio
    async def test_update_posted_voucher_fails(self, sample_voucher):
        await db.mark_voucher_posted(sample_voucher["voucher_id"], "user1")
        result = await db.update_voucher_record(sample_voucher["voucher_id"], {"rows": []})
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_voucher_posted(self, sample_voucher):
        result = await db.mark_voucher_posted(sample_voucher["voucher_id"], "user1")
        assert result is True
        record = await db.get_voucher_record(sample_voucher["voucher_id"])
        assert record["status"] == "posted"
        assert record["posted_by"] == "user1"

    @pytest.mark.asyncio
    async def test_mark_voucher_posted_nonexistent(self):
        result = await db.mark_voucher_posted("FAKE", "user1")
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_voucher_reversed(self, sample_voucher):
        await db.mark_voucher_posted(sample_voucher["voucher_id"], "user1")
        result = await db.mark_voucher_reversed(sample_voucher["voucher_id"], "user1", "mistake")
        assert result is True
        record = await db.get_voucher_record(sample_voucher["voucher_id"])
        assert record["status"] == "reversed"

    @pytest.mark.asyncio
    async def test_mark_voucher_reversed_not_posted(self, sample_voucher):
        """Cannot reverse a draft voucher."""
        result = await db.mark_voucher_reversed(sample_voucher["voucher_id"], "user1", "reason")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_voucher_records(self, sample_user):
        data = {"rows": []}
        await db.save_voucher_record("VR-L01", sample_user["id"], data)
        await db.save_voucher_record("VR-L02", sample_user["id"], data)
        records = await db.list_voucher_records(user_id=sample_user["id"])
        assert len(records) >= 2

    @pytest.mark.asyncio
    async def test_list_voucher_records_by_status(self, sample_user):
        data = {"rows": []}
        _, vid = await db.save_voucher_record("VR-STAT", sample_user["id"], data)
        await db.mark_voucher_posted(vid, sample_user["id"])
        drafts = await db.list_voucher_records(status="draft")
        assert all(r["status"] == "draft" for r in drafts)
        posted = await db.list_voucher_records(status="posted")
        assert any(r["voucher_id"] == vid for r in posted)

    @pytest.mark.asyncio
    async def test_list_voucher_records_by_keyword(self, sample_user):
        data = {"rows": []}
        await db.save_voucher_record(
            "VR-KW1", sample_user["id"], data, header_text="office supplies",
        )
        records = await db.list_voucher_records(keyword="office")
        assert any("office" in (r.get("header_text") or "") for r in records)

    @pytest.mark.asyncio
    async def test_count_voucher_records(self, sample_user):
        data = {"rows": []}
        await db.save_voucher_record("VR-CNT1", sample_user["id"], data)
        count = await db.count_voucher_records(user_id=sample_user["id"])
        assert count >= 1

    @pytest.mark.asyncio
    async def test_create_reversal_voucher(self, sample_user, sample_voucher):
        await db.mark_voucher_posted(sample_voucher["voucher_id"], sample_user["id"])
        new_vid = await db.create_reversal_voucher(sample_voucher["voucher_id"], sample_user["id"], "error")
        assert new_vid is not None
        assert new_vid.startswith("REV-")
        original = await db.get_voucher_record(sample_voucher["voucher_id"])
        assert original["status"] == "reversed"
        reversal = await db.get_voucher_record(new_vid)
        assert reversal["status"] == "posted"

    @pytest.mark.asyncio
    async def test_create_reversal_voucher_not_posted(self, sample_voucher):
        result = await db.create_reversal_voucher(sample_voucher["voucher_id"], "user1", "reason")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_reversal_voucher_nonexistent(self):
        result = await db.create_reversal_voucher("FAKE", "user1", "reason")
        assert result is None

    @pytest.mark.asyncio
    async def test_batch_mark_voucher_posted(self, sample_user):
        data = {"rows": []}
        await db.save_voucher_record("VR-B1", sample_user["id"], data)
        await db.save_voucher_record("VR-B2", sample_user["id"], data)
        result = await db.batch_mark_voucher_posted(["VR-B1", "VR-B2", "VR-FAKE"], "user1")
        assert result["posted"] == 2
        assert result["failed"] == 1
        assert len(result["errors"]) == 1


# ── Chat messages ─────────────────────────────────────────────────────────────


class TestChatMessages:
    @pytest.mark.asyncio
    async def test_save_chat_message(self, sample_user):
        msg_id = await db.save_chat_message(
            session_id="sess1", user_id=sample_user["id"],
            role="user", content="Hello",
        )
        assert msg_id is not None

    @pytest.mark.asyncio
    async def test_list_chat_messages(self, sample_user):
        await db.save_chat_message("sess2", sample_user["id"], "user", "msg1")
        await db.save_chat_message("sess2", sample_user["id"], "assistant", "msg2")
        messages = await db.list_chat_messages(session_id="sess2")
        assert len(messages) >= 2

    @pytest.mark.asyncio
    async def test_list_chat_messages_by_user(self, sample_user):
        await db.save_chat_message("sess3", sample_user["id"], "user", "hi")
        messages = await db.list_chat_messages(user_id=sample_user["id"])
        assert len(messages) >= 1

    @pytest.mark.asyncio
    async def test_chat_message_metadata(self, sample_user):
        await db.save_chat_message(
            "sess4", sample_user["id"], "user", "upload",
            message_type="upload", metadata={"filename": "test.xlsx"},
        )
        messages = await db.list_chat_messages(session_id="sess4")
        assert messages[0]["metadata"]["filename"] == "test.xlsx"


# ── Audit logs ────────────────────────────────────────────────────────────────


class TestAuditLogs:
    @pytest.mark.asyncio
    async def test_add_audit_log(self):
        log_id = await db.add_audit_log(
            action="login", user_id="u1", username="testuser",
            target_type="session", details={"ip": "127.0.0.1"},
        )
        assert log_id is not None

    @pytest.mark.asyncio
    async def test_list_audit_logs(self):
        await db.add_audit_log(action="test.action", user_id="u1", username="tester")
        logs = await db.list_audit_logs(action="test.action")
        assert len(logs) >= 1
        assert logs[0]["action"] == "test.action"

    @pytest.mark.asyncio
    async def test_list_audit_logs_with_details(self):
        await db.add_audit_log(
            action="detail.test", details={"key": "value"},
        )
        logs = await db.list_audit_logs(action="detail.test")
        assert logs[0]["details"]["key"] == "value"


# ── Voucher rules ─────────────────────────────────────────────────────────────


class TestVoucherRules:
    @pytest.mark.asyncio
    async def test_create_and_get_rule(self):
        lines = [
            {
                "line_no": 1, "debit_credit": "S", "account_code": "660201",
                "account_name": "Expense", "amount_field": "total_amount",
            },
        ]
        rule = await db.create_rule("TEST_RULE", "expense", lines=lines)
        assert rule is not None
        assert rule["rule_code"] == "TEST_RULE"
        assert len(rule["lines"]) == 1

    @pytest.mark.asyncio
    async def test_get_rule_not_found(self):
        result = await db.get_rule("NONEXISTENT")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_rules(self):
        await db.create_rule("LIST_A", "expense")
        await db.create_rule("LIST_B", "sales_revenue")
        rules = await db.list_rules()
        codes = {r["rule_code"] for r in rules}
        assert "LIST_A" in codes
        assert "LIST_B" in codes

    @pytest.mark.asyncio
    async def test_list_rules_by_business_type(self):
        await db.create_rule("FILTER_EXP", "expense")
        await db.create_rule("FILTER_SAL", "sales_revenue")
        expense_rules = await db.list_rules(business_type="expense")
        assert all(r["business_type"] == "expense" for r in expense_rules)

    @pytest.mark.asyncio
    async def test_update_rule_header(self):
        await db.create_rule("UPD_HDR", "expense")
        result = await db.update_rule("UPD_HDR", business_type="reimbursement")
        assert result is True
        rule = await db.get_rule("UPD_HDR")
        assert rule["business_type"] == "reimbursement"

    @pytest.mark.asyncio
    async def test_update_rule_lines(self):
        await db.create_rule("UPD_LINES", "expense", lines=[
            {"line_no": 1, "debit_credit": "S", "account_code": "111", "account_name": "A", "amount_field": "total"},
        ])
        new_lines = [
            {"line_no": 1, "debit_credit": "S", "account_code": "222", "account_name": "B", "amount_field": "total"},
            {"line_no": 2, "debit_credit": "H", "account_code": "333", "account_name": "C", "amount_field": "total"},
        ]
        result = await db.update_rule("UPD_LINES", lines=new_lines)
        assert result is True
        rule = await db.get_rule("UPD_LINES")
        assert len(rule["lines"]) == 2
        assert rule["lines"][0]["account_code"] == "222"

    @pytest.mark.asyncio
    async def test_update_nonexistent_rule(self):
        result = await db.update_rule("FAKE_RULE", business_type="x")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_rule(self):
        await db.create_rule("DEL_RULE", "expense")
        result = await db.delete_rule("DEL_RULE")
        assert result is True
        assert await db.get_rule("DEL_RULE") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_rule(self):
        result = await db.delete_rule("NO_RULE")
        assert result is False


# ── Attachments ───────────────────────────────────────────────────────────────


class TestAttachments:
    @pytest.mark.asyncio
    async def test_save_and_list_attachments(self, sample_user, sample_voucher):
        att_id = await db.save_attachment(
            voucher_id=sample_voucher["voucher_id"],
            filename="invoice.pdf", file_path="/tmp/invoice.pdf",
            file_size=1024, content_type="application/pdf",
            uploaded_by=sample_user["id"],
        )
        assert att_id is not None
        attachments = await db.list_attachments(sample_voucher["voucher_id"])
        assert len(attachments) >= 1
        assert attachments[0]["filename"] == "invoice.pdf"

    @pytest.mark.asyncio
    async def test_get_attachment(self, sample_user, sample_voucher):
        att_id = await db.save_attachment(
            voucher_id=sample_voucher["voucher_id"],
            filename="doc.pdf", file_path="/tmp/doc.pdf",
            file_size=512, content_type="application/pdf",
            uploaded_by=sample_user["id"],
        )
        att = await db.get_attachment(att_id)
        assert att is not None
        assert att["filename"] == "doc.pdf"

    @pytest.mark.asyncio
    async def test_get_attachment_not_found(self):
        result = await db.get_attachment("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_attachment(self, sample_user, sample_voucher):
        att_id = await db.save_attachment(
            voucher_id=sample_voucher["voucher_id"],
            filename="del.pdf", file_path="/tmp/del.pdf",
            file_size=100, content_type="application/pdf",
            uploaded_by=sample_user["id"],
        )
        result = await db.delete_attachment(att_id)
        assert result is True
        assert await db.get_attachment(att_id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_attachment(self):
        result = await db.delete_attachment("fake-id")
        assert result is False


# ── Voucher ID generation ─────────────────────────────────────────────────────


class TestVoucherIdGeneration:
    @pytest.mark.asyncio
    async def test_next_voucher_id_first(self):
        vid = await db.next_voucher_id("SO", "20260531")
        assert vid == "VR-SO-20260531-001"

    @pytest.mark.asyncio
    async def test_next_voucher_id_increments(self, sample_user):
        await db.save_voucher_record(
            "VR-INC-20260531-001", sample_user["id"], {"rows": []},
        )
        vid = await db.next_voucher_id("INC", "20260531")
        assert vid == "VR-INC-20260531-002"


# ── Login rate limiting ───────────────────────────────────────────────────────


class TestLoginRateLimiting:
    @pytest.mark.asyncio
    async def test_not_limited_initially(self):
        assert await db.is_login_rate_limited("10.0.0.1") is False

    @pytest.mark.asyncio
    async def test_limited_after_max_failures(self):
        ip = "10.0.0.2"
        for _ in range(5):
            await db.record_login_failure(ip)
        assert await db.is_login_rate_limited(ip) is True

    @pytest.mark.asyncio
    async def test_clear_failures(self):
        ip = "10.0.0.3"
        for _ in range(5):
            await db.record_login_failure(ip)
        await db.clear_login_failures(ip)
        assert await db.is_login_rate_limited(ip) is False


# ── Approval flow ─────────────────────────────────────────────────────────────


class TestApprovalFlow:
    @pytest.mark.asyncio
    async def test_submit_for_approval(self, sample_user, admin_user, sample_voucher):
        result = await db.submit_voucher_for_approval(
            sample_voucher["voucher_id"], sample_user["id"], admin_user["id"],
        )
        assert result is True
        record = await db.get_voucher_record(sample_voucher["voucher_id"])
        assert record["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_submit_non_draft_for_approval(self, sample_user, admin_user, sample_voucher):
        await db.mark_voucher_posted(sample_voucher["voucher_id"], sample_user["id"])
        result = await db.submit_voucher_for_approval(
            sample_voucher["voucher_id"], sample_user["id"], admin_user["id"],
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_approve_voucher(self, sample_user, admin_user, sample_voucher):
        await db.submit_voucher_for_approval(
            sample_voucher["voucher_id"], sample_user["id"], admin_user["id"],
        )
        result = await db.approve_voucher(sample_voucher["voucher_id"], admin_user["id"], "looks good")
        assert result is True
        record = await db.get_voucher_record(sample_voucher["voucher_id"])
        assert record["status"] == "posted"

    @pytest.mark.asyncio
    async def test_reject_voucher(self, sample_user, admin_user, sample_voucher):
        await db.submit_voucher_for_approval(
            sample_voucher["voucher_id"], sample_user["id"], admin_user["id"],
        )
        result = await db.reject_voucher(sample_voucher["voucher_id"], admin_user["id"], "needs fix")
        assert result is True
        record = await db.get_voucher_record(sample_voucher["voucher_id"])
        assert record["status"] == "draft"

    @pytest.mark.asyncio
    async def test_approve_non_pending_voucher(self, sample_user, admin_user, sample_voucher):
        result = await db.approve_voucher(sample_voucher["voucher_id"], admin_user["id"])
        assert result is False

    @pytest.mark.asyncio
    async def test_get_approval_record(self, sample_user, admin_user, sample_voucher):
        await db.submit_voucher_for_approval(
            sample_voucher["voucher_id"], sample_user["id"], admin_user["id"],
        )
        approval = await db.get_approval_record(sample_voucher["voucher_id"])
        assert approval is not None
        assert approval["status"] == "pending"

    @pytest.mark.asyncio
    async def test_list_pending_approvals(self, sample_user, admin_user, sample_voucher):
        await db.submit_voucher_for_approval(
            sample_voucher["voucher_id"], sample_user["id"], admin_user["id"],
        )
        pending = await db.list_pending_approvals(admin_user["id"])
        assert len(pending) >= 1


# ── Notifications ─────────────────────────────────────────────────────────────


class TestNotifications:
    @pytest.mark.asyncio
    async def test_create_and_list_notification(self, sample_user):
        notif_id = await db.create_notification(
            sample_user["id"], "info", "Test Title", "Test body",
        )
        assert notif_id is not None
        notifs = await db.list_notifications(sample_user["id"])
        assert len(notifs) >= 1
        assert notifs[0]["title"] == "Test Title"

    @pytest.mark.asyncio
    async def test_count_unread_notifications(self, sample_user):
        await db.create_notification(sample_user["id"], "info", "T1", "B1")
        await db.create_notification(sample_user["id"], "warning", "T2", "B2")
        count = await db.count_unread_notifications(sample_user["id"])
        assert count >= 2

    @pytest.mark.asyncio
    async def test_mark_notification_read(self, sample_user):
        notif_id = await db.create_notification(sample_user["id"], "info", "T", "B")
        result = await db.mark_notification_read(notif_id, sample_user["id"])
        assert result is True
        count = await db.count_unread_notifications(sample_user["id"])
        # Could be 0 if this is the only notification
        notifs = await db.list_notifications(sample_user["id"], unread_only=True)
        assert not any(n["id"] == notif_id for n in notifs)

    @pytest.mark.asyncio
    async def test_mark_all_notifications_read(self, sample_user):
        await db.create_notification(sample_user["id"], "info", "A", "a")
        await db.create_notification(sample_user["id"], "info", "B", "b")
        count = await db.mark_all_notifications_read(sample_user["id"])
        assert count >= 2
        unread = await db.count_unread_notifications(sample_user["id"])
        assert unread == 0

    @pytest.mark.asyncio
    async def test_delete_notification(self, sample_user):
        notif_id = await db.create_notification(sample_user["id"], "info", "D", "d")
        result = await db.delete_notification(notif_id, sample_user["id"])
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_notification_wrong_user(self, sample_user, admin_user):
        notif_id = await db.create_notification(sample_user["id"], "info", "X", "x")
        result = await db.delete_notification(notif_id, admin_user["id"])
        assert result is False

    @pytest.mark.asyncio
    async def test_list_notifications_unread_only(self, sample_user):
        n1 = await db.create_notification(sample_user["id"], "info", "U1", "u1")
        n2 = await db.create_notification(sample_user["id"], "info", "U2", "u2")
        await db.mark_notification_read(n1, sample_user["id"])
        unread = await db.list_notifications(sample_user["id"], unread_only=True)
        assert all(n["is_read"] == 0 for n in unread)
        assert not any(n["id"] == n1 for n in unread)
