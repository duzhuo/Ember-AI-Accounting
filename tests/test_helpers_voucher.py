"""Comprehensive tests for helpers/voucher.py (post/reverse voucher logic)."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

import helpers.voucher as hv
from helpers.voucher import (
    _format_rules_for_frontend,
    _voucher_to_front,
    post_voucher,
    reverse_voucher,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_db_record(voucher_id="VR-001", user_id="u1", status="draft", rows=None):
    """Create a mock database voucher record."""
    if rows is None:
        rows = [
            {"line_no": 1, "debit_credit": "S", "debit": 100, "credit": 0},
            {"line_no": 2, "debit_credit": "H", "debit": 0, "credit": 100},
        ]
    return {
        "id": "rec-001",
        "voucher_id": voucher_id,
        "user_id": user_id,
        "status": status,
        "company_code": "1000",
        "document_type": "DR",
        "document_date": "2026-05-31",
        "posting_date": "2026-05-31",
        "reference": "INV-001",
        "header_text": "Test",
        "voucher_data": json.dumps({"rows": rows, "total_amount": 100}),
    }


def _mock_user(user_id="u1", username="testuser", role="user"):
    return {"id": user_id, "username": username, "role": role}


# ── post_voucher() ────────────────────────────────────────────────────────────


class TestPostVoucher:
    @pytest.mark.asyncio
    async def test_post_success(self):
        record = _mock_db_record()
        user = _mock_user(user_id="u1")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record), \
             patch("helpers.voucher.mark_voucher_posted", new_callable=AsyncMock, return_value=True), \
             patch("helpers.voucher.add_audit_log", new_callable=AsyncMock):
            result = await post_voucher("VR-001", user)
        assert result["status"] == "posted"
        assert "VR-001" in result["message"]

    @pytest.mark.asyncio
    async def test_post_voucher_not_found(self):
        user = _mock_user()
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=None):
            result = await post_voucher("VR-FAKE", user)
        assert result["status"] == "error"
        assert "不存在" in result["message"]

    @pytest.mark.asyncio
    async def test_post_forbidden_different_user(self):
        record = _mock_db_record(user_id="other-user")
        user = _mock_user(user_id="u1", role="user")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record):
            result = await post_voucher("VR-001", user)
        assert result["status"] == "forbidden"

    @pytest.mark.asyncio
    async def test_post_admin_can_post_any_voucher(self):
        record = _mock_db_record(user_id="other-user")
        admin = _mock_user(user_id="admin1", role="admin")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record), \
             patch("helpers.voucher.mark_voucher_posted", new_callable=AsyncMock, return_value=True), \
             patch("helpers.voucher.add_audit_log", new_callable=AsyncMock):
            result = await post_voucher("VR-001", admin)
        assert result["status"] == "posted"

    @pytest.mark.asyncio
    async def test_post_already_posted(self):
        record = _mock_db_record(status="posted")
        user = _mock_user(user_id="u1")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record):
            result = await post_voucher("VR-001", user)
        assert result["status"] == "already_posted"

    @pytest.mark.asyncio
    async def test_post_unbalanced_voucher(self):
        rows = [
            {"line_no": 1, "debit_credit": "S", "debit": 100, "credit": 0},
            {"line_no": 2, "debit_credit": "H", "debit": 0, "credit": 50},
        ]
        record = _mock_db_record(rows=rows)
        user = _mock_user(user_id="u1")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record):
            result = await post_voucher("VR-001", user)
        assert result["status"] == "error"
        assert "不平衡" in result["message"]

    @pytest.mark.asyncio
    async def test_post_balanced_within_tolerance(self):
        """Debit and credit within 0.01 tolerance should succeed."""
        rows = [
            {"line_no": 1, "debit_credit": "S", "debit": 100.005, "credit": 0},
            {"line_no": 2, "debit_credit": "H", "debit": 0, "credit": 100.00},
        ]
        record = _mock_db_record(rows=rows)
        user = _mock_user(user_id="u1")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record), \
             patch("helpers.voucher.mark_voucher_posted", new_callable=AsyncMock, return_value=True), \
             patch("helpers.voucher.add_audit_log", new_callable=AsyncMock):
            result = await post_voucher("VR-001", user)
        assert result["status"] == "posted"

    @pytest.mark.asyncio
    async def test_post_creates_audit_log(self):
        record = _mock_db_record()
        user = _mock_user(user_id="u1", username="tester")
        mock_audit = AsyncMock()
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record), \
             patch("helpers.voucher.mark_voucher_posted", new_callable=AsyncMock, return_value=True), \
             patch("helpers.voucher.add_audit_log", mock_audit):
            await post_voucher("VR-001", user)
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["action"] == "voucher.post"
        assert call_kwargs["user_id"] == "u1"


# ── reverse_voucher() ─────────────────────────────────────────────────────────


class TestReverseVoucher:
    @pytest.mark.asyncio
    async def test_reverse_success(self):
        record = _mock_db_record(status="posted")
        user = _mock_user(user_id="u1")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record), \
             patch("helpers.voucher.create_reversal_voucher", new_callable=AsyncMock, return_value="REV-VR-001"), \
             patch("helpers.voucher.add_audit_log", new_callable=AsyncMock):
            result = await reverse_voucher("VR-001", user, "mistake")
        assert result["status"] == "ok"
        assert result["new_voucher_id"] == "REV-VR-001"

    @pytest.mark.asyncio
    async def test_reverse_not_found(self):
        user = _mock_user()
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=None):
            result = await reverse_voucher("VR-FAKE", user, "reason")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_reverse_forbidden(self):
        record = _mock_db_record(status="posted", user_id="other")
        user = _mock_user(user_id="u1", role="user")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record):
            result = await reverse_voucher("VR-001", user, "reason")
        assert result["status"] == "forbidden"

    @pytest.mark.asyncio
    async def test_reverse_admin_can_reverse_any(self):
        record = _mock_db_record(status="posted", user_id="other")
        admin = _mock_user(user_id="admin1", role="admin")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record), \
             patch("helpers.voucher.create_reversal_voucher", new_callable=AsyncMock, return_value="REV-VR-001"), \
             patch("helpers.voucher.add_audit_log", new_callable=AsyncMock):
            result = await reverse_voucher("VR-001", admin, "reason")
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_reverse_not_posted(self):
        record = _mock_db_record(status="draft")
        user = _mock_user(user_id="u1")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record):
            result = await reverse_voucher("VR-001", user, "reason")
        assert result["status"] == "error"
        assert "已过账" in result["message"]

    @pytest.mark.asyncio
    async def test_reverse_reversal_fails(self):
        record = _mock_db_record(status="posted")
        user = _mock_user(user_id="u1")
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record), \
             patch("helpers.voucher.create_reversal_voucher", new_callable=AsyncMock, return_value=None):
            result = await reverse_voucher("VR-001", user, "reason")
        assert result["status"] == "error"
        assert "冲销失败" in result["message"]

    @pytest.mark.asyncio
    async def test_reverse_creates_audit_log(self):
        record = _mock_db_record(status="posted")
        user = _mock_user(user_id="u1", username="tester")
        mock_audit = AsyncMock()
        with patch("helpers.voucher.get_voucher_record", new_callable=AsyncMock, return_value=record), \
             patch("helpers.voucher.create_reversal_voucher", new_callable=AsyncMock, return_value="REV-VR-001"), \
             patch("helpers.voucher.add_audit_log", mock_audit):
            await reverse_voucher("VR-001", user, "wrong amount")
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["action"] == "voucher.reverse"
        assert call_kwargs["details"]["reason"] == "wrong amount"


# ── _voucher_to_front() ──────────────────────────────────────────────────────


class TestVoucherToFront:
    def _make_voucher_obj(self):
        from voucher_models import Voucher, VoucherLine
        return Voucher(
            voucher_id="VR-F001",
            company_code="1000",
            document_type="DR",
            document_date="2026-05-31",
            posting_date="2026-05-31",
            reference="INV-001",
            header_text="Frontend test",
            source_transaction_id="TXN-001",
            confidence=Decimal("0.95"),
            warnings=["test warning"],
            lines=[
                VoucherLine(
                    line_no=1, debit_credit="S", account_code="112200",
                    account_name="Receivables", amount=Decimal("100"),
                    currency="CNY", customer_code="C001", customer_name="Customer",
                    tax_code="X1", profit_center="PC01", cost_center="CC01",
                    assignment="A01", text="Entry 1",
                ),
                VoucherLine(
                    line_no=2, debit_credit="H", account_code="600101",
                    account_name="Revenue", amount=Decimal("100"),
                    currency="CNY", text="Entry 2",
                ),
            ],
        )

    def test_basic_structure(self):
        voucher = self._make_voucher_obj()
        result = _voucher_to_front(voucher)
        assert result["voucher_id"] == "VR-F001"
        assert result["company_code"] == "1000"
        assert result["document_type"] == "DR"
        assert result["confidence"] == "0.95"
        assert result["warnings"] == ["test warning"]

    def test_rows_converted(self):
        voucher = self._make_voucher_obj()
        result = _voucher_to_front(voucher)
        assert len(result["rows"]) == 2

    def test_debit_row(self):
        voucher = self._make_voucher_obj()
        result = _voucher_to_front(voucher)
        debit_row = result["rows"][0]
        assert debit_row["debit_credit"] == "S"
        assert debit_row["debit"] == 100.0
        assert debit_row["credit"] == 0
        assert debit_row["account_code"] == "112200"
        assert debit_row["customer_code"] == "C001"

    def test_credit_row(self):
        voucher = self._make_voucher_obj()
        result = _voucher_to_front(voucher)
        credit_row = result["rows"][1]
        assert credit_row["debit_credit"] == "H"
        assert credit_row["debit"] == 0
        assert credit_row["credit"] == 100.0

    def test_empty_lines(self):
        from voucher_models import Voucher
        voucher = Voucher(
            voucher_id="VR-EMPTY", company_code="1000", document_type="DR",
            document_date="2026-01-01", posting_date="2026-01-01",
            reference="", header_text="", source_transaction_id="",
            confidence=Decimal("1.0"), lines=[],
        )
        result = _voucher_to_front(voucher)
        assert result["rows"] == []


# ── _format_rules_for_frontend() ─────────────────────────────────────────────


class TestFormatRulesForFrontend:
    def _sample_rules(self):
        return [
            {
                "rule_code": "EXPENSE_STANDARD",
                "business_type": "expense",
                "product_type": "*",
                "tax_rate": "*",
                "document_type": "DR",
                "lines": [
                    {
                        "line_no": 1, "debit_credit": "S",
                        "account_code": "660201", "account_name": "Expense",
                        "amount_field": "tax_excluded_amount",
                        "customer_source": "", "tax_code_rule": "by_tax_rate",
                        "profit_center_source": "profit_center",
                        "cost_center_source": "cost_center",
                        "assignment_source": "", "text_template": "test {field}",
                    },
                ],
            },
        ]

    def test_basic_structure(self):
        result = _format_rules_for_frontend(self._sample_rules())
        assert len(result) == 1
        assert result[0]["rule_code"] == "EXPENSE_STANDARD"

    def test_debit_credit_display(self):
        result = _format_rules_for_frontend(self._sample_rules())
        line = result[0]["lines"][0]
        assert line["debit_credit_display"] == "借"

    def test_credit_display(self):
        rules = self._sample_rules()
        rules[0]["lines"][0]["debit_credit"] = "H"
        result = _format_rules_for_frontend(rules)
        assert result[0]["lines"][0]["debit_credit_display"] == "贷"

    def test_amount_field_display_known(self):
        result = _format_rules_for_frontend(self._sample_rules())
        line = result[0]["lines"][0]
        assert line["amount_field_display"] == "不含税金额"

    def test_amount_field_display_total(self):
        rules = self._sample_rules()
        rules[0]["lines"][0]["amount_field"] = "total_amount"
        result = _format_rules_for_frontend(rules)
        assert result[0]["lines"][0]["amount_field_display"] == "价税合计"

    def test_amount_field_display_tax(self):
        rules = self._sample_rules()
        rules[0]["lines"][0]["amount_field"] = "tax_amount"
        result = _format_rules_for_frontend(rules)
        assert result[0]["lines"][0]["amount_field_display"] == "税额"

    def test_amount_field_display_unknown(self):
        rules = self._sample_rules()
        rules[0]["lines"][0]["amount_field"] = "custom_field"
        result = _format_rules_for_frontend(rules)
        assert result[0]["lines"][0]["amount_field_display"] == "custom_field"

    def test_empty_rules(self):
        result = _format_rules_for_frontend([])
        assert result == []

    def test_optional_line_fields_default_empty(self):
        rules = [
            {
                "rule_code": "R1", "business_type": "test", "product_type": "*",
                "tax_rate": "*", "document_type": "DR",
                "lines": [
                    {"line_no": 1, "debit_credit": "S", "account_code": "111",
                     "account_name": "A", "amount_field": "total_amount"},
                ],
            },
        ]
        result = _format_rules_for_frontend(rules)
        line = result[0]["lines"][0]
        assert line["customer_source"] == ""
        assert line["tax_code_rule"] == ""
        assert line["text_template"] == ""
