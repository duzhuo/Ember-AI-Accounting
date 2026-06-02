"""Export voucher drafts to a simple SAP import-style CSV."""

import csv
from pathlib import Path

from voucher_models import Voucher


SAP_COLUMNS = [
    "BUKRS",
    "BLART",
    "BLDAT",
    "BUDAT",
    "XBLNR",
    "BKTXT",
    "BUZEI",
    "SHKZG",
    "HKONT",
    "ACCOUNT_NAME",
    "WRBTR",
    "WAERS",
    "KUNNR",
    "CUSTOMER_NAME",
    "MWSKZ",
    "PRCTR",
    "KOSTL",
    "ZUONR",
    "SGTXT",
]


def record_to_sap_rows(record: dict) -> list[dict]:
    """Convert a DB voucher record dict to SAP CSV row dicts."""
    import json
    voucher_data = json.loads(record.get("voucher_data") or "{}")
    rows = voucher_data.get("rows", [])
    result = []
    for row in rows:
        result.append({
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
    return result


def voucher_to_sap_rows(voucher: Voucher) -> list[dict]:
    """Convert a Voucher object to SAP CSV row dicts."""
    result = []
    for line in voucher.lines:
        result.append({
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
    return result


def export_sap_csv(vouchers: list[Voucher], output_path: Path) -> None:
    """Write voucher lines to a SAP-style CSV file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=SAP_COLUMNS)
        writer.writeheader()
        for voucher in vouchers:
            writer.writerows(voucher_to_sap_rows(voucher))
