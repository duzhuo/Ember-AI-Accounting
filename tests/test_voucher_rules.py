"""Comprehensive tests for voucher_rules.py (rule matching, fallback logic, template rendering)."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from voucher_models import SalesTransaction, ExpenseTransaction
from voucher_rules import (
    DEFAULT_EXPENSE_RULES,
    DEFAULT_SALES_REVENUE_RULES,
    VoucherRuleLine,
    _build_voucher_line,
    _get_decimal_field,
    _get_string_field,
    _matches_tax_rate,
    _matches_token,
    _match_rule_lines,
    _normalize_header,
    _render_template,
    _resolve_tax_code,
    _string,
    build_expense_voucher,
    build_sales_revenue_voucher,
    ensure_default_rule_config,
    load_voucher_rule_lines,
    money,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_sales_txn(**overrides) -> SalesTransaction:
    defaults = dict(
        transaction_id="TXN-S001",
        company_code="1000",
        document_date="2026-05-31",
        posting_date="2026-05-31",
        customer_code="C001",
        customer_name="Test Customer",
        product_type="software",
        contract_no="CON-001",
        invoice_no="INV-001",
        currency="CNY",
        tax_rate=Decimal("0.13"),
        tax_excluded_amount=Decimal("10000.00"),
        tax_amount=Decimal("1300.00"),
        total_amount=Decimal("11300.00"),
        profit_center="PC01",
        cost_center="CC01",
    )
    defaults.update(overrides)
    return SalesTransaction(**defaults)


def _make_expense_txn(**overrides) -> ExpenseTransaction:
    defaults = dict(
        transaction_id="TXN-E001",
        company_code="1000",
        document_date="2026-05-31",
        posting_date="2026-05-31",
        vendor_code="V001",
        vendor_name="Test Vendor",
        expense_category="dining",
        receipt_no="REC-001",
        description="Business dinner",
        currency="CNY",
        tax_rate=Decimal("0.06"),
        tax_excluded_amount=Decimal("5000.00"),
        tax_amount=Decimal("300.00"),
        total_amount=Decimal("5300.00"),
        profit_center="PC01",
        cost_center="CC01",
    )
    defaults.update(overrides)
    return ExpenseTransaction(**defaults)


def _sales_rule_lines() -> list[VoucherRuleLine]:
    """Build VoucherRuleLine objects from DEFAULT_SALES_REVENUE_RULES."""
    lines = []
    for row in DEFAULT_SALES_REVENUE_RULES:
        lines.append(VoucherRuleLine(
            rule_code=row[0], business_type=row[1], product_type=row[2],
            tax_rate=row[3], document_type=row[4], line_no=row[5],
            debit_credit=row[6], account_code=row[7], account_name=row[8],
            amount_field=row[9], customer_source=row[10], tax_code_rule=row[11],
            profit_center_source=row[12], cost_center_source=row[13],
            assignment_source=row[14], text_template=row[15],
        ))
    return lines


def _expense_rule_lines() -> list[VoucherRuleLine]:
    """Build VoucherRuleLine objects from DEFAULT_EXPENSE_RULES."""
    lines = []
    for row in DEFAULT_EXPENSE_RULES:
        lines.append(VoucherRuleLine(
            rule_code=row[0], business_type=row[1], product_type=row[2],
            tax_rate=row[3], document_type=row[4], line_no=row[5],
            debit_credit=row[6], account_code=row[7], account_name=row[8],
            amount_field=row[9], customer_source=row[10], tax_code_rule=row[11],
            profit_center_source=row[12], cost_center_source=row[13],
            assignment_source=row[14], text_template=row[15],
        ))
    return lines


# ── money() ───────────────────────────────────────────────────────────────────


class TestMoney:
    def test_round_half_up(self):
        assert money(Decimal("1.235")) == Decimal("1.24")

    def test_already_two_decimals(self):
        assert money(Decimal("10.50")) == Decimal("10.50")

    def test_integer(self):
        assert money(Decimal("100")) == Decimal("100.00")

    def test_negative(self):
        assert money(Decimal("-5.678")) == Decimal("-5.68")

    def test_zero(self):
        assert money(Decimal("0")) == Decimal("0.00")


# ── _matches_token() ──────────────────────────────────────────────────────────


class TestMatchesToken:
    def test_wildcard_matches_anything(self):
        assert _matches_token("*", "anything") is True

    def test_empty_matches_anything(self):
        assert _matches_token("", "value") is True

    def test_exact_match(self):
        assert _matches_token("software", "software") is True

    def test_exact_no_match(self):
        assert _matches_token("software", "hardware") is False

    def test_pipe_separated_choices(self):
        assert _matches_token("software|service|saas", "service") is True
        assert _matches_token("software|service|saas", "hardware") is False

    def test_case_insensitive(self):
        assert _matches_token("Software", "software") is True

    def test_whitespace_handling(self):
        assert _matches_token(" software | service ", "software") is True


# ── _matches_tax_rate() ──────────────────────────────────────────────────────


class TestMatchesTaxRate:
    def test_wildcard(self):
        assert _matches_tax_rate("*", Decimal("0.13")) is True

    def test_empty(self):
        assert _matches_tax_rate("", Decimal("0.06")) is True

    def test_exact_match(self):
        assert _matches_tax_rate("0.13", Decimal("0.13")) is True

    def test_no_match(self):
        assert _matches_tax_rate("0.13", Decimal("0.06")) is False


# ── _resolve_tax_code() ──────────────────────────────────────────────────────


class TestResolveTaxCode:
    def test_by_tax_rate_13(self):
        txn = _make_sales_txn(tax_rate=Decimal("0.13"))
        assert _resolve_tax_code("by_tax_rate", txn) == "X1"

    def test_by_tax_rate_6(self):
        txn = _make_sales_txn(tax_rate=Decimal("0.06"))
        assert _resolve_tax_code("by_tax_rate", txn) == "X6"

    def test_by_tax_rate_0(self):
        txn = _make_sales_txn(tax_rate=Decimal("0"))
        assert _resolve_tax_code("by_tax_rate", txn) == "X0"

    def test_by_tax_rate_unknown(self):
        txn = _make_sales_txn(tax_rate=Decimal("0.09"))
        assert _resolve_tax_code("by_tax_rate", txn) == "X?"

    def test_literal_rule(self):
        txn = _make_sales_txn()
        assert _resolve_tax_code("V1", txn) == "V1"


# ── _get_decimal_field() / _get_string_field() ───────────────────────────────


class TestFieldExtractors:
    def test_get_decimal_field(self):
        txn = _make_sales_txn()
        assert _get_decimal_field(txn, "total_amount") == Decimal("11300.00")

    def test_get_decimal_field_nonexistent(self):
        txn = _make_sales_txn()
        with pytest.raises(ValueError, match="not a Decimal field"):
            _get_decimal_field(txn, "nonexistent")

    def test_get_string_field(self):
        txn = _make_sales_txn()
        assert _get_string_field(txn, "customer_name") == "Test Customer"

    def test_get_string_field_empty(self):
        txn = _make_sales_txn()
        assert _get_string_field(txn, "") == ""

    def test_get_string_field_nonexistent(self):
        txn = _make_sales_txn()
        assert _get_string_field(txn, "nonexistent_field") == ""


# ── _render_template() ───────────────────────────────────────────────────────


class TestRenderTemplate:
    def test_basic_template(self):
        txn = _make_sales_txn()
        result = _render_template("确认{customer_name}销售收入", txn)
        assert result == "确认Test Customer销售收入"

    def test_multi_field_template(self):
        txn = _make_sales_txn()
        result = _render_template("{customer_name}发票{invoice_no}", txn)
        assert result == "Test Customer发票INV-001"

    def test_empty_template(self):
        txn = _make_sales_txn()
        assert _render_template("", txn) == ""

    def test_template_with_missing_field(self):
        txn = _make_sales_txn()
        # Missing field should default to empty string
        result = _render_template("{nonexistent_field}", txn)
        assert result == ""


# ── _normalize_header() / _string() ──────────────────────────────────────────


class TestStringHelpers:
    def test_normalize_header(self):
        assert _normalize_header("  test  ") == "test"

    def test_normalize_header_none(self):
        assert _normalize_header(None) == ""

    def test_string_normal(self):
        assert _string("  hello  ") == "hello"

    def test_string_none(self):
        assert _string(None) == ""

    def test_string_none_with_default(self):
        assert _string(None, "default") == "default"

    def test_string_non_string(self):
        assert _string(123) == "123"


# ── _match_rule_lines() ──────────────────────────────────────────────────────


class TestMatchRuleLines:
    def test_match_by_business_type(self):
        rules = _sales_rule_lines()
        matched = _match_rule_lines(rules, "sales_revenue", "software", Decimal("0.13"))
        assert len(matched) > 0
        assert all(r.business_type == "sales_revenue" for r in matched)

    def test_no_match_wrong_business_type(self):
        rules = _sales_rule_lines()
        matched = _match_rule_lines(rules, "expense", "software", Decimal("0.13"))
        assert len(matched) == 0

    def test_match_product_type_wildcard(self):
        rules = _sales_rule_lines()
        # Wildcard rules match any product_type
        wildcard_rules = [r for r in rules if r.product_type == "*"]
        assert len(wildcard_rules) > 0

    def test_match_expense_rules(self):
        rules = _expense_rule_lines()
        matched = _match_rule_lines(rules, "expense", "*", Decimal("0.06"))
        assert len(matched) == 3  # All 3 default expense lines match

    def test_match_product_type_specific(self):
        rules = _sales_rule_lines()
        # "software" should match the "software|service|saas" rule but not "goods"
        specific = [r for r in rules if r.product_type != "*"]
        assert len(specific) > 0
        # At least one specific rule should match "software"
        matching_specific = [r for r in specific if _matches_token(r.product_type, "software")]
        assert len(matching_specific) > 0
        # "goods" rule should NOT match "software"
        goods_rules = [r for r in specific if r.product_type == "goods"]
        for r in goods_rules:
            assert _matches_token(r.product_type, "software") is False


# ── _build_voucher_line() ─────────────────────────────────────────────────────


class TestBuildVoucherLine:
    def test_sales_revenue_line(self):
        txn = _make_sales_txn()
        rule = _sales_rule_lines()[0]  # Receivable line
        line = _build_voucher_line(rule, txn, "fallback text")
        assert line.account_code == "112200"
        assert line.amount == money(Decimal("11300.00"))
        assert line.currency == "CNY"

    def test_expense_line_customer_source_vendor(self):
        txn = _make_expense_txn()
        rule = _expense_rule_lines()[2]  # Payable line with vendor source
        line = _build_voucher_line(rule, txn, "fallback")
        assert line.customer_code == "V001"
        assert line.customer_name == "Test Vendor"

    def test_fallback_text(self):
        txn = _make_sales_txn()
        rule = VoucherRuleLine(
            rule_code="TEST", business_type="test", product_type="*",
            tax_rate="*", document_type="DR", line_no=1,
            debit_credit="S", account_code="111", account_name="Test",
            amount_field="total_amount", customer_source="", tax_code_rule="",
            profit_center_source="", cost_center_source="",
            assignment_source="", text_template="",
        )
        line = _build_voucher_line(rule, txn, "fallback text")
        assert line.text == "fallback text"


# ── build_sales_revenue_voucher() ─────────────────────────────────────────────


class TestBuildSalesRevenueVoucher:
    def test_standard_software_sale(self):
        txn = _make_sales_txn(product_type="software", tax_rate=Decimal("0.13"))
        rule_lines = _sales_rule_lines()
        voucher = build_sales_revenue_voucher(txn, rule_lines=rule_lines)

        assert voucher.voucher_id == "VR-TXN-S001"
        assert voucher.company_code == "1000"
        assert voucher.document_type == "DR"
        assert len(voucher.lines) == 3  # receivable + revenue + tax
        assert voucher.confidence == Decimal("0.95")

    def test_balanced_voucher(self):
        txn = _make_sales_txn()
        rule_lines = _sales_rule_lines()
        voucher = build_sales_revenue_voucher(txn, rule_lines=rule_lines)
        debit_total = sum(l.amount for l in voucher.lines if l.debit_credit == "S")
        credit_total = sum(l.amount for l in voucher.lines if l.debit_credit == "H")
        assert money(debit_total) == money(credit_total)

    def test_unbalanced_amount_warning(self):
        txn = _make_sales_txn(
            tax_excluded_amount=Decimal("10000"),
            tax_amount=Decimal("1300"),
            total_amount=Decimal("9999"),  # Deliberately wrong
        )
        rule_lines = _sales_rule_lines()
        voucher = build_sales_revenue_voucher(txn, rule_lines=rule_lines)
        assert len(voucher.warnings) > 0
        assert voucher.confidence == Decimal("0.70")

    def test_no_matching_rules_raises(self):
        txn = _make_sales_txn()
        with pytest.raises(ValueError, match="No voucher rules matched"):
            build_sales_revenue_voucher(txn, rule_lines=[])

    def test_goods_product_type(self):
        txn = _make_sales_txn(product_type="goods", tax_rate=Decimal("0.13"))
        rule_lines = _sales_rule_lines()
        voucher = build_sales_revenue_voucher(txn, rule_lines=rule_lines)
        # Should use 600102 for goods revenue
        revenue_lines = [l for l in voucher.lines if l.debit_credit == "H" and l.account_code.startswith("600")]
        assert len(revenue_lines) == 1
        assert revenue_lines[0].account_code == "600102"


# ── build_expense_voucher() ───────────────────────────────────────────────────


class TestBuildExpenseVoucher:
    def test_standard_expense(self):
        txn = _make_expense_txn(tax_rate=Decimal("0.06"))
        rule_lines = _expense_rule_lines()
        voucher = build_expense_voucher(txn, rule_lines=rule_lines)

        assert voucher.voucher_id == "VR-TXN-E001"
        assert len(voucher.lines) == 3  # expense + tax + payable
        assert voucher.reference == "REC-001"

    def test_balanced_expense_voucher(self):
        txn = _make_expense_txn()
        rule_lines = _expense_rule_lines()
        voucher = build_expense_voucher(txn, rule_lines=rule_lines)
        debit_total = sum(l.amount for l in voucher.lines if l.debit_credit == "S")
        credit_total = sum(l.amount for l in voucher.lines if l.debit_credit == "H")
        assert money(debit_total) == money(credit_total)

    def test_no_matching_expense_rules_raises(self):
        txn = _make_expense_txn()
        with pytest.raises(ValueError, match="No voucher rules matched"):
            build_expense_voucher(txn, rule_lines=[])

    def test_unbalanced_expense_warning(self):
        txn = _make_expense_txn(total_amount=Decimal("9999"))
        rule_lines = _expense_rule_lines()
        voucher = build_expense_voucher(txn, rule_lines=rule_lines)
        assert any("does not equal" in w for w in voucher.warnings)


# ── ensure_default_rule_config() ──────────────────────────────────────────────


class TestEnsureDefaultRuleConfig:
    def test_creates_file_if_missing(self, tmp_path):
        path = tmp_path / "rules.xlsx"
        result = ensure_default_rule_config(path)
        assert result.exists()
        assert result == path

    def test_returns_existing_file(self, tmp_path):
        path = tmp_path / "rules.xlsx"
        path.touch()
        result = ensure_default_rule_config(path)
        assert result == path

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "rules.xlsx"
        result = ensure_default_rule_config(path)
        assert result.exists()


# ── load_voucher_rule_lines() ─────────────────────────────────────────────────


class TestLoadVoucherRuleLines:
    def test_loads_default_rules(self, tmp_path):
        path = tmp_path / "rules.xlsx"
        lines = load_voucher_rule_lines(path)
        assert len(lines) == len(DEFAULT_SALES_REVENUE_RULES) + len(DEFAULT_EXPENSE_RULES)

    def test_all_fields_populated(self, tmp_path):
        path = tmp_path / "rules.xlsx"
        lines = load_voucher_rule_lines(path)
        for line in lines:
            assert isinstance(line, VoucherRuleLine)
            assert line.rule_code
            assert line.business_type
            assert line.account_code
            assert line.account_name

    def test_missing_columns_raises(self, tmp_path):
        """If the Excel file is missing required columns, should raise ValueError."""
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["rule_code", "business_type"])  # Missing most columns
        path = tmp_path / "bad.xlsx"
        wb.save(path)
        with pytest.raises(ValueError, match="missing columns"):
            load_voucher_rule_lines(path)

    def test_empty_sheet_raises(self, tmp_path):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "voucher_rules"
        # Empty but with headers only
        from voucher_rules import RULE_HEADERS
        ws.append(RULE_HEADERS)
        path = tmp_path / "empty.xlsx"
        wb.save(path)
        lines = load_voucher_rule_lines(path)
        assert len(lines) == 0
