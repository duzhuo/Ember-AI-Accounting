"""VoucherAgent evaluation — tests voucher generation with real LLM calls."""

import json
import pytest
import pytest_asyncio
from decimal import Decimal
from dataclasses import asdict

from agentscope.message import Msg, UserMsg

from voucher_models import SalesTransaction, ExpenseTransaction
from voucher_rules import load_voucher_rule_lines, ensure_default_rule_config

from tests.evals.helpers import load_cases, assert_voucher_balanced, assert_voucher_structure, score_results

# Load test cases
VOUCHER_CASES = load_cases("voucher_cases.json")

# Ensure rule config exists
ensure_default_rule_config()


@pytest_asyncio.fixture(scope="module")
async def voucher_agent():
    """Create a real VoucherAgent instance."""
    from agents.voucher_agent import VoucherAgent
    agent = Agent = VoucherAgent(name="eval_voucher_agent")
    yield agent


def dict_to_sales_transaction(data: dict) -> SalesTransaction:
    """Convert dict to SalesTransaction."""
    return SalesTransaction(
        transaction_id=data["transaction_id"],
        company_code=data.get("company_code", "1000"),
        document_date=data.get("document_date", "2026-05-30"),
        posting_date=data.get("posting_date", "2026-05-30"),
        customer_code=data.get("customer_code", "C99999"),
        customer_name=data.get("customer_name", "未知客户"),
        product_type=data.get("product_type", "service"),
        contract_no=data.get("contract_no", ""),
        invoice_no=data.get("invoice_no", ""),
        currency=data.get("currency", "CNY"),
        tax_rate=Decimal(str(data.get("tax_rate", "0.13"))),
        tax_excluded_amount=Decimal(str(data["tax_excluded_amount"])),
        tax_amount=Decimal(str(data.get("tax_amount", "0"))),
        total_amount=Decimal(str(data["total_amount"])),
        profit_center=data.get("profit_center", "PC-DEFAULT"),
        cost_center=data.get("cost_center", ""),
    )


def dict_to_expense_transaction(data: dict) -> ExpenseTransaction:
    """Convert dict to ExpenseTransaction."""
    return ExpenseTransaction(
        transaction_id=data["transaction_id"],
        company_code=data.get("company_code", "1000"),
        document_date=data.get("document_date", "2026-05-30"),
        posting_date=data.get("posting_date", "2026-05-30"),
        vendor_code=data.get("vendor_code", "V99999"),
        vendor_name=data.get("vendor_name", "未知商户"),
        expense_category=data.get("expense_category", "other"),
        receipt_no=data.get("receipt_no", ""),
        description=data.get("description", ""),
        currency=data.get("currency", "CNY"),
        tax_rate=Decimal(str(data.get("tax_rate", "0.06"))),
        tax_excluded_amount=Decimal(str(data["tax_excluded_amount"])),
        tax_amount=Decimal(str(data.get("tax_amount", "0"))),
        total_amount=Decimal(str(data["total_amount"])),
        profit_center=data.get("profit_center", "PC-DEFAULT"),
        cost_center=data.get("cost_center", ""),
    )


def voucher_to_dict(voucher) -> dict:
    """Convert Voucher to dict for assertion."""
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
            "tax_code": line.tax_code,
        })
    return {
        "voucher_id": voucher.voucher_id,
        "rows": rows,
        "confidence": str(voucher.confidence),
        "warnings": voucher.warnings,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    VOUCHER_CASES,
    ids=[c["id"] for c in VOUCHER_CASES],
)
async def test_voucher_generation(voucher_agent, case):
    """Test VoucherAgent with real LLM call.

    Verifies:
    - Voucher is balanced (debit == credit)
    - Has reasonable line count (2-4 lines)
    - Has at least one debit and one credit line
    - Amounts are valid numbers
    """
    business_type = case["input"]["business_type"]
    txn_data = case["input"]["transaction"]

    # Build transaction
    if business_type == "sales_revenue":
        txn = dict_to_sales_transaction(txn_data)
    else:
        txn = dict_to_expense_transaction(txn_data)

    # Call the real VoucherAgent
    txn_dict = asdict(txn)
    for key, value in txn_dict.items():
        if isinstance(value, Decimal):
            txn_dict[key] = str(value)

    msg = UserMsg(
        name="user",
        content=json.dumps(txn_dict, ensure_ascii=False),
        metadata={"transaction": txn, "business_type": business_type},
    )
    result_msg = await voucher_agent.reply(msg)

    # Parse the result
    raw = result_msg.get_text_content() or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if "```json" in raw:
            json_str = raw.split("```json")[1].split("```")[0].strip()
            data = json.loads(json_str)
        elif "```" in raw:
            json_str = raw.split("```")[1].split("```")[0].strip()
            data = json.loads(json_str)
        else:
            pytest.fail(f"Case {case['id']}: Failed to parse LLM response as JSON:\n{raw[:500]}")

    # Verify structure
    rows = data.get("rows", [])
    assert len(rows) >= 2, f"Case {case['id']}: expected at least 2 lines, got {len(rows)}"
    assert len(rows) <= 5, f"Case {case['id']}: expected at most 5 lines, got {len(rows)}"

    # Verify has both debit and credit lines
    debit_lines = [r for r in rows if r.get("debit_credit") == "S"]
    credit_lines = [r for r in rows if r.get("debit_credit") == "H"]
    assert len(debit_lines) >= 1, f"Case {case['id']}: no debit lines"
    assert len(credit_lines) >= 1, f"Case {case['id']}: no credit lines"

    # Verify balanced
    err = assert_voucher_balanced(data)
    assert err is None, f"Case {case['id']}: {err}"

    # Verify amounts are valid numbers
    for row in rows:
        assert isinstance(row.get("debit", 0), (int, float)), f"Case {case['id']}: invalid debit amount"
        assert isinstance(row.get("credit", 0), (int, float)), f"Case {case['id']}: invalid credit amount"


@pytest.mark.asyncio
async def test_voucher_agent_returns_valid_json(voucher_agent):
    """Test that VoucherAgent always returns valid JSON."""
    txn = SalesTransaction(
        transaction_id="SO-20260530-TEST",
        company_code="1000",
        document_date="2026-05-30",
        posting_date="2026-05-30",
        customer_code="C10001",
        customer_name="测试客户",
        product_type="software",
        contract_no="CTR-TEST-001",
        invoice_no="INV-TEST-001",
        currency="CNY",
        tax_rate=Decimal("0.13"),
        tax_excluded_amount=Decimal("100000"),
        tax_amount=Decimal("13000"),
        total_amount=Decimal("113000"),
        profit_center="PC-SOFTWARE",
        cost_center="",
    )

    txn_dict = asdict(txn)
    for key, value in txn_dict.items():
        if isinstance(value, Decimal):
            txn_dict[key] = str(value)

    msg = UserMsg(
        name="user",
        content=json.dumps(txn_dict, ensure_ascii=False),
        metadata={"transaction": txn, "business_type": "sales_revenue"},
    )
    result_msg = await voucher_agent.reply(msg)
    raw = result_msg.get_text_content() or ""

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if "```json" in raw:
            json_str = raw.split("```json")[1].split("```")[0].strip()
            data = json.loads(json_str)
        else:
            pytest.fail(f"Response is not valid JSON:\n{raw[:500]}")

    assert "rows" in data, f"Response missing 'rows' field: {list(data.keys())}"
    assert len(data["rows"]) > 0, "Response has empty rows"


@pytest.mark.asyncio
async def test_voucher_is_balanced(voucher_agent):
    """Test that generated voucher is balanced (debit == credit)."""
    txn = SalesTransaction(
        transaction_id="SO-20260530-BAL",
        company_code="1000",
        document_date="2026-05-30",
        posting_date="2026-05-30",
        customer_code="C10001",
        customer_name="测试客户",
        product_type="software",
        contract_no="CTR-BAL-001",
        invoice_no="INV-BAL-001",
        currency="CNY",
        tax_rate=Decimal("0.13"),
        tax_excluded_amount=Decimal("100000"),
        tax_amount=Decimal("13000"),
        total_amount=Decimal("113000"),
        profit_center="PC-SOFTWARE",
        cost_center="",
    )

    txn_dict = asdict(txn)
    for key, value in txn_dict.items():
        if isinstance(value, Decimal):
            txn_dict[key] = str(value)

    msg = UserMsg(
        name="user",
        content=json.dumps(txn_dict, ensure_ascii=False),
        metadata={"transaction": txn, "business_type": "sales_revenue"},
    )
    result_msg = await voucher_agent.reply(msg)
    raw = result_msg.get_text_content() or ""
    data = json.loads(raw) if raw.strip().startswith("{") else json.loads(raw.split("```json")[1].split("```")[0].strip())

    err = assert_voucher_balanced(data)
    assert err is None, f"Voucher not balanced: {err}\nResponse: {raw[:300]}"
