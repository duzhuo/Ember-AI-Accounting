"""CSV export functions for SAP voucher posting."""

import csv
import json
from pathlib import Path

from sap_exporter import SAP_COLUMNS

PROJECT_ROOT = Path(__file__).parent.parent
POSTED_CSV = PROJECT_ROOT / "data" / "output" / "posted_vouchers.csv"
POSTED_CSV.parent.mkdir(parents=True, exist_ok=True)


def _append_posted_csv(voucher) -> None:
    """Append one voucher's lines to the persistent posted_vouchers.csv."""
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
