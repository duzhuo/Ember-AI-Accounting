"""End-to-end evaluation — tests the full pipeline: natural language → intent → voucher."""

import json
import pytest
import pytest_asyncio
from decimal import Decimal
from dataclasses import asdict

from agentscope.message import Msg, UserMsg

from voucher_models import SalesTransaction, ExpenseTransaction
from voucher_rules import load_voucher_rule_lines, ensure_default_rule_config

from tests.evals.helpers import load_cases, assert_intent, assert_transaction, assert_voucher_balanced

# Load test cases
E2E_CASES = load_cases("e2e_cases.json")

# Ensure rule config exists
ensure_default_rule_config()


@pytest_asyncio.fixture(scope="module")
async def intent_agent():
    """Create a real IntentAgent instance."""
    from agents.intent_agent import IntentAgent
    agent = IntentAgent(name="eval_e2e_intent")
    yield agent


@pytest_asyncio.fixture(scope="module")
async def voucher_agent():
    """Create a real VoucherAgent instance."""
    from agents.voucher_agent import VoucherAgent
    agent = VoucherAgent(name="eval_e2e_voucher")
    yield agent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [c for c in E2E_CASES if c["expected"].get("intent") == "business"],
    ids=[c["id"] for c in E2E_CASES if c["expected"].get("intent") == "business"],
)
async def test_e2e_business_pipeline(intent_agent, voucher_agent, case):
    """Test full pipeline: natural language → intent → voucher generation."""
    input_text = case["input"]
    expected = case["expected"]

    # Step 1: Intent recognition
    msg = UserMsg(name="user", content=input_text)
    intent_result = await intent_agent.reply(msg)

    # Get parse_result from metadata (contains proper types)
    parse_result = intent_result.metadata.get("parse_result") if intent_result.metadata else None
    if parse_result is None:
        pytest.fail(f"Case {case['id']}: No parse_result in metadata")

    # Verify intent
    assert parse_result.get("intent") == expected["intent"], (
        f"Case {case['id']}: expected intent '{expected['intent']}', got '{parse_result.get('intent')}'"
    )
    assert parse_result.get("business_type") == expected["business_type"], (
        f"Case {case['id']}: expected business_type '{expected['business_type']}', "
        f"got '{parse_result.get('business_type')}'"
    )

    # Step 2: Voucher generation (if transaction was extracted)
    txn_obj = parse_result.get("transaction")
    if txn_obj is None:
        pytest.skip(f"Case {case['id']}: IntentAgent did not extract transaction data")

    business_type = parse_result.get("business_type", "sales_revenue")

    # Serialize transaction for VoucherAgent
    txn_dict = asdict(txn_obj)
    for key, value in txn_dict.items():
        if isinstance(value, Decimal):
            txn_dict[key] = str(value)

    voucher_msg = UserMsg(
        name="user",
        content=json.dumps(txn_dict, ensure_ascii=False),
        metadata={"transaction": txn_obj, "business_type": business_type},
    )
    voucher_result = await voucher_agent.reply(voucher_msg)
    voucher_raw = voucher_result.get_text_content() or ""

    try:
        voucher_data = json.loads(voucher_raw)
    except json.JSONDecodeError:
        if "```json" in voucher_raw:
            json_str = voucher_raw.split("```json")[1].split("```")[0].strip()
            voucher_data = json.loads(json_str)
        else:
            pytest.fail(f"Case {case['id']}: VoucherAgent response not JSON:\n{voucher_raw[:300]}")

    # Verify voucher
    if "voucher_balanced" in expected and expected["voucher_balanced"]:
        err = assert_voucher_balanced(voucher_data)
        assert err is None, f"Case {case['id']}: {err}"

    if "voucher_line_count" in expected:
        actual_count = len(voucher_data.get("rows", []))
        assert actual_count == expected["voucher_line_count"], (
            f"Case {case['id']}: expected {expected['voucher_line_count']} lines, got {actual_count}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [c for c in E2E_CASES if c["expected"].get("intent") != "business"],
    ids=[c["id"] for c in E2E_CASES if c["expected"].get("intent") != "business"],
)
async def test_e2e_non_business_intent(intent_agent, case):
    """Test non-business intent classification (rule_query, chat, etc.)."""
    input_text = case["input"]
    expected = case["expected"]

    msg = UserMsg(name="user", content=input_text)
    result_msg = await intent_agent.reply(msg)

    # Get parse_result from metadata
    parse_result = result_msg.metadata.get("parse_result") if result_msg.metadata else None
    if parse_result is None:
        pytest.fail(f"Case {case['id']}: No parse_result in metadata")

    assert parse_result.get("intent") == expected["intent"], (
        f"Case {case['id']}: expected intent '{expected['intent']}', got '{parse_result.get('intent')}'"
    )

    if "rule_type" in expected:
        assert parse_result.get("rule_type") == expected["rule_type"], (
            f"Case {case['id']}: expected rule_type '{expected['rule_type']}', got '{parse_result.get('rule_type')}'"
        )
