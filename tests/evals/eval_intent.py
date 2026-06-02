"""IntentAgent evaluation — tests intent classification with real LLM calls."""

import json
import pytest
import pytest_asyncio
from decimal import Decimal

from agentscope.message import Msg, UserMsg

from tests.evals.helpers import load_cases, assert_intent, assert_transaction, score_results

# Load test cases
INTENT_CASES = load_cases("intent_cases.json")


@pytest_asyncio.fixture(scope="module")
async def intent_agent():
    """Create a real IntentAgent instance."""
    from agents.intent_agent import IntentAgent
    agent = IntentAgent(name="eval_intent_agent")
    yield agent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    INTENT_CASES,
    ids=[c["id"] for c in INTENT_CASES],
)
async def test_intent_classification(intent_agent, case):
    """Test IntentAgent classification with real LLM call."""
    input_text = case["input"]
    expected = case["expected"]

    # Call the real agent
    msg = UserMsg(name="user", content=input_text)
    result_msg = await intent_agent.reply(msg)

    # Get parse_result from metadata (contains proper types)
    parse_result = result_msg.metadata.get("parse_result") if result_msg.metadata else None
    if parse_result is None:
        raw = result_msg.get_text_content() or ""
        pytest.fail(f"Case {case['id']}: No parse_result in metadata. Raw: {raw[:300]}")

    # Build data dict for assertion
    data = {
        "intent": parse_result.get("intent"),
        "business_type": parse_result.get("business_type"),
        "rule_type": parse_result.get("rule_type"),
        "status": parse_result.get("status"),
        "action": parse_result.get("action"),
    }

    # Assert intent classification
    intent_failures = assert_intent(data, expected)
    if intent_failures:
        pytest.fail(
            f"Case {case['id']} ({case['name']}):\n"
            f"  Input: {input_text}\n"
            f"  Result: {parse_result}\n"
            f"  Failures: {intent_failures}"
        )

    # Assert transaction data extraction
    txn = parse_result.get("transaction")
    if txn is not None:
        # Convert transaction object to dict for assertion
        if hasattr(txn, '__dataclass_fields__'):
            from dataclasses import asdict
            txn_dict = {k: str(v) if v is not None else None for k, v in asdict(txn).items()}
        elif isinstance(txn, dict):
            txn_dict = txn
        else:
            txn_dict = {}

        data_with_txn = {**data, "transaction": txn_dict}
        txn_failures = assert_transaction(data_with_txn, expected)
        if txn_failures:
            pytest.fail(
                f"Case {case['id']} ({case['name']}):\n"
                f"  Input: {input_text}\n"
                f"  Transaction: {txn_dict}\n"
                f"  Transaction failures: {txn_failures}"
            )


@pytest.mark.asyncio
async def test_intent_agent_returns_valid_json(intent_agent):
    """Test that IntentAgent always returns valid JSON."""
    msg = UserMsg(name="user", content="你好")
    result_msg = await intent_agent.reply(msg)

    # Check metadata has parse_result
    parse_result = result_msg.metadata.get("parse_result") if result_msg.metadata else None
    assert parse_result is not None, "No parse_result in metadata"
    assert "intent" in parse_result, f"parse_result missing 'intent': {parse_result}"


@pytest.mark.asyncio
async def test_intent_agent_handles_empty_input(intent_agent):
    """Test that IntentAgent handles empty input gracefully."""
    msg = UserMsg(name="user", content="")
    result_msg = await intent_agent.reply(msg)

    # Should return something, not crash
    raw = result_msg.get_text_content() or ""
    assert raw, "Agent returned empty response for empty input"
