"""Comprehensive tests for sap_exporter.py (CSV generation, record conversion)."""

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from voucher_models import Voucher, VoucherLine
from sap_exporter import (
    SAP_COLUMNS,
    export_sap_csv,
    record_to_sap_rows,
    voucher_to_sap_rows,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_voucher(**overrides) -> Voucher:
    defaults = dict(
        voucher_id="VR-TEST-001",
        company_code="1000",
        document_type="DR",
        document_date="2026-05-31",
        posting_date="2026-05-31",
        reference="INV-001",
        header_text="Test voucher",
        source_transaction_id="TXN-001",
        confidence=Decimal("0.95"),
        warnings=[],
        lines=[
            VoucherLine(
                line_no=1, debit_credit="S", account_code="112200",
                account_name="Receivables", amount=Decimal("11300.00"),
                currency="CNY", customer_code="C001", customer_name="Customer A",
                tax_code="X1", profit_center="PC01", cost_center="CC01",
                assignment="ASN001", text="Revenue entry",
            ),
            VoucherLine(
                line_no=2, debit_credit="H", account_code="600101",
                account_name="Revenue", amount=Decimal("10000.00"),
                currency="CNY", tax_code="X1", profit_center="PC01",
                cost_center="CC01", text="Revenue entry",
            ),
            VoucherLine(
                line_no=3, debit_credit="H", account_code="22210105",
                account_name="Tax Payable", amount=Decimal("1300.00"),
                currency="CNY", tax_code="X1", profit_center="PC01",
                text="Revenue entry",
            ),
        ],
    )
    defaults.update(overrides)
    return Voucher(**defaults)


def _make_db_record(**overrides) -> dict:
    voucher_data = {
        "rows": [
            {
                "line_no": 1, "debit_credit": "S", "account_code": "112200",
                "account_name": "Receivables", "debit": 11300.0, "credit": 0,
                "currency": "CNY", "customer_code": "C001", "customer_name": "Customer A",
                "tax_code": "X1", "profit_center": "PC01", "cost_center": "CC01",
                "assignment": "ASN001", "text": "Revenue entry",
            },
            {
                "line_no": 2, "debit_credit": "H", "account_code": "600101",
                "account_name": "Revenue", "debit": 0, "credit": 10000.0,
                "currency": "CNY", "tax_code": "X1", "profit_center": "PC01",
                "cost_center": "CC01", "text": "Revenue entry",
            },
        ],
        "total_amount": 11300,
    }
    defaults = dict(
        company_code="1000",
        document_type="DR",
        document_date="2026-05-31",
        posting_date="2026-05-31",
        reference="INV-001",
        header_text="Test voucher",
        voucher_data=json.dumps(voucher_data),
    )
    defaults.update(overrides)
    return defaults


# ── SAP_COLUMNS ───────────────────────────────────────────────────────────────


class TestSAPColumns:
    def test_column_count(self):
        assert len(SAP_COLUMNS) == 19

    def test_contains_required_fields(self):
        required = {"BUKRS", "BLART", "BLDAT", "BUDAT", "BUZEI", "SHKZG", "HKONT", "WRBTR"}
        assert required.issubset(set(SAP_COLUMNS))

    def test_column_names_are_uppercase(self):
        for col in SAP_COLUMNS:
            assert col == col.upper()


# ── record_to_sap_rows() ─────────────────────────────────────────────────────


class TestRecordToSapRows:
    def test_basic_conversion(self):
        record = _make_db_record()
        rows = record_to_sap_rows(record)
        assert len(rows) == 2

    def test_header_fields_propagated(self):
        record = _make_db_record()
        rows = record_to_sap_rows(record)
        for row in rows:
            assert row["BUKRS"] == "1000"
            assert row["BLART"] == "DR"
            assert row["BLDAT"] == "2026-05-31"
            assert row["BUDAT"] == "2026-05-31"
            assert row["XBLNR"] == "INV-001"
            assert row["BKTXT"] == "Test voucher"

    def test_line_fields_correct(self):
        record = _make_db_record()
        rows = record_to_sap_rows(record)
        # First row is debit
        assert rows[0]["BUZEI"] == 1
        assert rows[0]["SHKZG"] == "S"
        assert rows[0]["HKONT"] == "112200"
        assert rows[0]["ACCOUNT_NAME"] == "Receivables"
        assert rows[0]["WRBTR"] == 11300.0
        assert rows[0]["KUNNR"] == "C001"
        assert rows[0]["CUSTOMER_NAME"] == "Customer A"

    def test_credit_row_amount(self):
        record = _make_db_record()
        rows = record_to_sap_rows(record)
        # Second row is credit, should use credit amount
        assert rows[1]["SHKZG"] == "H"
        assert rows[1]["WRBTR"] == 10000.0

    def test_empty_voucher_data(self):
        record = {"voucher_data": "{}"}
        rows = record_to_sap_rows(record)
        assert rows == []

    def test_empty_rows_array(self):
        record = {"voucher_data": json.dumps({"rows": []})}
        rows = record_to_sap_rows(record)
        assert rows == []

    def test_missing_voucher_data(self):
        record = {}
        rows = record_to_sap_rows(record)
        assert rows == []

    def test_default_currency(self):
        record = {"voucher_data": json.dumps({"rows": [{"line_no": 1}]})}
        rows = record_to_sap_rows(record)
        assert rows[0]["WAERS"] == "CNY"

    def test_optional_fields_default_empty(self):
        record = {"voucher_data": json.dumps({"rows": [{"line_no": 1}]})}
        rows = record_to_sap_rows(record)
        assert rows[0]["KUNNR"] == ""
        assert rows[0]["MWSKZ"] == ""
        assert rows[0]["PRCTR"] == ""
        assert rows[0]["KOSTL"] == ""
        assert rows[0]["ZUONR"] == ""
        assert rows[0]["SGTXT"] == ""


# ── voucher_to_sap_rows() ────────────────────────────────────────────────────


class TestVoucherToSapRows:
    def test_basic_conversion(self):
        voucher = _make_voucher()
        rows = voucher_to_sap_rows(voucher)
        assert len(rows) == 3

    def test_header_fields(self):
        voucher = _make_voucher()
        rows = voucher_to_sap_rows(voucher)
        for row in rows:
            assert row["BUKRS"] == "1000"
            assert row["BLART"] == "DR"
            assert row["BLDAT"] == "2026-05-31"
            assert row["BUDAT"] == "2026-05-31"
            assert row["XBLNR"] == "INV-001"
            assert row["BKTXT"] == "Test voucher"

    def test_line_fields(self):
        voucher = _make_voucher()
        rows = voucher_to_sap_rows(voucher)
        assert rows[0]["BUZEI"] == 1
        assert rows[0]["SHKZG"] == "S"
        assert rows[0]["HKONT"] == "112200"
        assert rows[0]["WRBTR"] == Decimal("11300.00")
        assert rows[0]["WAERS"] == "CNY"
        assert rows[0]["KUNNR"] == "C001"
        assert rows[0]["SGTXT"] == "Revenue entry"

    def test_empty_lines(self):
        voucher = _make_voucher(lines=[])
        rows = voucher_to_sap_rows(voucher)
        assert rows == []

    def test_single_line(self):
        voucher = _make_voucher(lines=[
            VoucherLine(
                line_no=1, debit_credit="S", account_code="111",
                account_name="Cash", amount=Decimal("100"),
                currency="CNY",
            ),
        ])
        rows = voucher_to_sap_rows(voucher)
        assert len(rows) == 1
        assert rows[0]["WRBTR"] == Decimal("100")


# ── export_sap_csv() ─────────────────────────────────────────────────────────


class TestExportSAPCSV:
    def test_creates_csv_file(self, tmp_path):
        output = tmp_path / "export.csv"
        voucher = _make_voucher()
        export_sap_csv([voucher], output)
        assert output.exists()

    def test_csv_has_header(self, tmp_path):
        output = tmp_path / "export.csv"
        voucher = _make_voucher()
        export_sap_csv([voucher], output)
        with output.open(encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == list(SAP_COLUMNS)

    def test_csv_row_count(self, tmp_path):
        output = tmp_path / "export.csv"
        voucher = _make_voucher()  # 3 lines
        export_sap_csv([voucher], output)
        with output.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 3

    def test_csv_values_correct(self, tmp_path):
        output = tmp_path / "export.csv"
        voucher = _make_voucher()
        export_sap_csv([voucher], output)
        with output.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert rows[0]["BUKRS"] == "1000"
        assert rows[0]["HKONT"] == "112200"
        assert rows[0]["SGTXT"] == "Revenue entry"

    def test_multiple_vouchers(self, tmp_path):
        output = tmp_path / "export.csv"
        v1 = _make_voucher(voucher_id="VR-001")
        v2 = _make_voucher(voucher_id="VR-002", lines=[
            VoucherLine(
                line_no=1, debit_credit="S", account_code="999",
                account_name="Other", amount=Decimal("50"),
                currency="USD",
            ),
        ])
        export_sap_csv([v1, v2], output)
        with output.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 4  # 3 + 1

    def test_empty_voucher_list(self, tmp_path):
        output = tmp_path / "export.csv"
        export_sap_csv([], output)
        with output.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 0

    def test_creates_parent_directories(self, tmp_path):
        output = tmp_path / "deep" / "nested" / "export.csv"
        voucher = _make_voucher()
        export_sap_csv([voucher], output)
        assert output.exists()

    def test_utf8_bom_encoding(self, tmp_path):
        """CSV should be encoded with UTF-8 BOM for Excel compatibility."""
        output = tmp_path / "export.csv"
        voucher = _make_voucher()
        export_sap_csv([voucher], output)
        raw = output.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf"
