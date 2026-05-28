"""Shared voucher and rule conversion helpers used by multiple route modules."""


def _voucher_to_front(voucher) -> dict:
    rows = []
    for line in voucher.lines:
        debit = float(line.amount) if line.debit_credit == "S" else 0
        credit = float(line.amount) if line.debit_credit == "H" else 0
        rows.append({
            "line_no": line.line_no,
            "account_code": line.account_code,
            "account_name": line.account_name,
            "debit_credit": line.debit_credit,
            "debit": debit,
            "credit": credit,
            "currency": line.currency,
            "customer_code": line.customer_code,
            "customer_name": line.customer_name,
            "tax_code": line.tax_code,
            "profit_center": line.profit_center,
            "cost_center": line.cost_center,
            "assignment": line.assignment,
            "text": line.text,
        })
    return {
        "voucher_id": voucher.voucher_id,
        "company_code": voucher.company_code,
        "document_type": voucher.document_type,
        "document_date": voucher.document_date,
        "posting_date": voucher.posting_date,
        "reference": voucher.reference,
        "header_text": voucher.header_text,
        "confidence": str(voucher.confidence),
        "warnings": voucher.warnings,
        "rows": rows,
    }


def _format_rules_for_frontend(rules: list[dict]) -> list[dict]:
    """Convert database rule dicts to the frontend display format."""
    result = []
    for rule in rules:
        formatted = {
            "rule_code": rule["rule_code"],
            "business_type": rule["business_type"],
            "product_type": rule["product_type"],
            "tax_rate": rule["tax_rate"],
            "document_type": rule["document_type"],
            "lines": [],
        }
        for line in rule.get("lines", []):
            formatted["lines"].append({
                "line_no": line["line_no"],
                "debit_credit": line["debit_credit"],
                "debit_credit_display": "借" if line["debit_credit"] == "S" else "贷",
                "account_code": line["account_code"],
                "account_name": line["account_name"],
                "amount_field": line["amount_field"],
                "amount_field_display": {
                    "total_amount": "价税合计",
                    "tax_excluded_amount": "不含税金额",
                    "tax_amount": "税额",
                }.get(line["amount_field"], line["amount_field"]),
                "customer_source": line.get("customer_source", ""),
                "tax_code_rule": line.get("tax_code_rule", ""),
                "profit_center_source": line.get("profit_center_source", ""),
                "cost_center_source": line.get("cost_center_source", ""),
                "assignment_source": line.get("assignment_source", ""),
                "text_template": line.get("text_template", ""),
            })
        result.append(formatted)
    return result
