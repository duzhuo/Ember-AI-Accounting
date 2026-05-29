"""Shared voucher and rule conversion helpers used by multiple route modules."""

import json

from database import (
    add_audit_log,
    create_reversal_voucher,
    get_voucher_record,
    mark_voucher_posted,
    mark_voucher_reversed,
)


async def post_voucher(voucher_id: str, user: dict) -> dict:
    """Post a voucher with ownership + balance checks. Returns result dict."""
    db_record = await get_voucher_record(voucher_id)
    if not db_record:
        return {"status": "error", "message": f"凭证 {voucher_id} 不存在"}
    if user["role"] != "admin" and db_record["user_id"] != user["id"]:
        return {"status": "forbidden", "message": "无权操作此凭证"}
    if db_record.get("status") == "posted":
        return {"status": "already_posted", "message": f"凭证 {voucher_id} 已经过账"}

    voucher_data = json.loads(db_record.get("voucher_data") or "{}")
    rows = voucher_data.get("rows", [])
    total_debit = sum(r.get("debit", 0) for r in rows)
    total_credit = sum(r.get("credit", 0) for r in rows)
    if abs(total_debit - total_credit) > 0.01:
        return {"status": "error", "message": f"借贷不平衡：借方 {total_debit:.2f}，贷方 {total_credit:.2f}"}

    await mark_voucher_posted(voucher_id, user["id"])
    await add_audit_log(
        action="voucher.post", user_id=user["id"], username=user["username"],
        target_type="voucher", target_id=voucher_id,
    )
    return {"status": "posted", "message": f"凭证 {voucher_id} 已成功过账", "record": db_record}


async def reverse_voucher(voucher_id: str, user: dict, reason: str) -> dict:
    """Reverse a posted voucher with ownership check. Returns result dict."""
    db_record = await get_voucher_record(voucher_id)
    if not db_record:
        return {"status": "error", "message": f"凭证 {voucher_id} 不存在"}
    if user["role"] != "admin" and db_record["user_id"] != user["id"]:
        return {"status": "forbidden", "message": "无权操作此凭证"}
    if db_record.get("status") != "posted":
        return {"status": "error", "message": "只有已过账凭证才能冲销"}

    new_voucher_id = await create_reversal_voucher(voucher_id, user["id"], reason)
    if not new_voucher_id:
        return {"status": "error", "message": "冲销失败"}

    await mark_voucher_reversed(voucher_id, user["id"], reason)
    await add_audit_log(
        action="voucher.reverse", user_id=user["id"], username=user["username"],
        target_type="voucher", target_id=voucher_id,
        details={"reversal_voucher_id": new_voucher_id, "reason": reason},
    )
    return {"status": "ok", "message": f"凭证 {voucher_id} 已冲销，冲销凭证号：{new_voucher_id}", "new_voucher_id": new_voucher_id}


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
