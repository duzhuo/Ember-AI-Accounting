"""Evaluation helpers — assertions, scoring, comparison utilities."""

import json
from decimal import Decimal
from pathlib import Path


DATA_DIR = Path(__file__).parent / "data"


def load_cases(filename: str) -> list[dict]:
    """Load test cases from a JSON file."""
    path = DATA_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def assert_intent(result: dict, expected: dict) -> list[str]:
    """Assert intent classification results. Returns list of failures."""
    failures = []

    if "intent" in expected:
        if result.get("intent") != expected["intent"]:
            failures.append(f"intent: expected '{expected['intent']}', got '{result.get('intent')}'")

    if "business_type" in expected:
        if result.get("business_type") != expected["business_type"]:
            failures.append(f"business_type: expected '{expected['business_type']}', got '{result.get('business_type')}'")

    if "rule_type" in expected:
        if result.get("rule_type") != expected["rule_type"]:
            failures.append(f"rule_type: expected '{expected['rule_type']}', got '{result.get('rule_type')}'")

    if "status" in expected:
        if result.get("status") != expected["status"]:
            failures.append(f"status: expected '{expected['status']}', got '{result.get('status')}'")

    if "action" in expected:
        if result.get("action") != expected["action"]:
            failures.append(f"action: expected '{expected['action']}', got '{result.get('action')}'")

    return failures


def assert_transaction(result: dict, expected: dict, tolerance: str = "0.01") -> list[str]:
    """Assert transaction data extraction. Returns list of failures."""
    failures = []
    txn = result.get("transaction")
    if txn is None:
        if any(k.startswith("transaction.") for k in expected):
            failures.append("transaction is None but expected transaction fields")
        return failures

    tol = Decimal(tolerance)

    for key, exp_val in expected.items():
        if not key.startswith("transaction."):
            continue
        field = key.split(".", 1)[1]
        actual = txn.get(field)

        if actual is None:
            failures.append(f"transaction.{field}: missing")
            continue

        # Try numeric comparison first, fall back to string
        try:
            actual_d = Decimal(str(actual))
            expected_d = Decimal(str(exp_val))
            if abs(actual_d - expected_d) > tol:
                failures.append(f"transaction.{field}: expected {exp_val}, got {actual} (tolerance {tolerance})")
        except Exception:
            # Fuzzy string comparison — normalize and check containment
            actual_str = str(actual).lower().strip()
            expected_str = str(exp_val).lower().strip()
            if actual_str != expected_str:
                # Check if expected is contained in actual (e.g., "saas" in "saas服务费")
                if expected_str not in actual_str and actual_str not in expected_str:
                    failures.append(f"transaction.{field}: expected '{exp_val}', got '{actual}'")

    return failures


def assert_voucher_balanced(voucher: dict) -> str | None:
    """Assert voucher debit == credit. Returns error message or None."""
    rows = voucher.get("rows", [])
    if not rows:
        return "voucher has no rows"

    debit_total = Decimal("0")
    credit_total = Decimal("0")
    for row in rows:
        amount = Decimal(str(row.get("debit", 0) or 0)) if row.get("debit_credit") == "S" else Decimal("0")
        debit_total += amount
        amount = Decimal(str(row.get("credit", 0) or 0)) if row.get("debit_credit") == "H" else Decimal("0")
        credit_total += amount

    if abs(debit_total - credit_total) > Decimal("0.01"):
        return f"voucher not balanced: debit={debit_total}, credit={credit_total}"
    return None


def assert_voucher_structure(voucher: dict, expected: dict) -> list[str]:
    """Assert voucher structure against expected values. Returns list of failures."""
    failures = []

    if "balanced" in expected and expected["balanced"]:
        err = assert_voucher_balanced(voucher)
        if err:
            failures.append(err)

    if "line_count" in expected:
        actual_count = len(voucher.get("rows", []))
        if actual_count != expected["line_count"]:
            failures.append(f"line_count: expected {expected['line_count']}, got {actual_count}")

    if "debit_account" in expected:
        debit_rows = [r for r in voucher.get("rows", []) if r.get("debit_credit") == "S"]
        debit_accounts = {r.get("account_code") for r in debit_rows}
        if expected["debit_account"] not in debit_accounts:
            failures.append(f"debit_account: expected '{expected['debit_account']}' in {debit_accounts}")

    if "credit_accounts" in expected:
        credit_rows = [r for r in voucher.get("rows", []) if r.get("debit_credit") == "H"]
        credit_accounts = {r.get("account_code") for r in credit_rows}
        for acc in expected["credit_accounts"]:
            if acc not in credit_accounts:
                failures.append(f"credit_account: expected '{acc}' in {credit_accounts}")

    if "tax_code" in expected:
        all_tax_codes = {r.get("tax_code") for r in voucher.get("rows", []) if r.get("tax_code")}
        if expected["tax_code"] not in all_tax_codes:
            failures.append(f"tax_code: expected '{expected['tax_code']}' in {all_tax_codes}")

    return failures


def score_results(results: list[dict]) -> dict:
    """Calculate aggregate scores from eval results."""
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    failed = total - passed

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": f"{passed / total * 100:.1f}%" if total > 0 else "N/A",
    }
