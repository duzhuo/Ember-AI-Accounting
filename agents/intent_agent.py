"""Intent recognition agent — classifies user intent and extracts structured data."""

import inspect
import json
import logging
import uuid
from datetime import date
from decimal import Decimal

from agentscope.agent import Agent, ContextConfig
from agentscope.event import (
    ModelCallStartEvent,
    ModelCallEndEvent,
    TextBlockStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
)
from agentscope.message import Msg, AssistantMsg, TextBlock

from prompts import NL_PARSE_SYSTEM_PROMPT
from voucher_models import SalesTransaction, ExpenseTransaction

from .agent_config import IDENTITY_CONTEXT, AGENT_NAME, AGENT_CAPABILITIES
from .middleware import SystemPromptMiddleware, LoggingMiddleware, TimingMiddleware, TracingMiddleware
from .model_factory import create_chat_model

logger = logging.getLogger(__name__)


class IntentAgent(Agent):
    """Classify user intent and extract business data from natural language."""

    def __init__(self, name: str, offloader=None) -> None:
        super().__init__(
            name=name,
            system_prompt=NL_PARSE_SYSTEM_PROMPT,
            model=create_chat_model(),
            middlewares=[
                SystemPromptMiddleware(),
                LoggingMiddleware(),
                TimingMiddleware(),
                TracingMiddleware(),
            ],
            offloader=offloader,
            context_config=ContextConfig(
                trigger_ratio=0.8,
                reserve_ratio=0.1,
            ),
        )

    async def reply(self, msg: Msg) -> Msg:
        self.state.context.clear()
        return await super().reply(msg)

    async def _reasoning_impl(self, tool_choice=None):
        yield ModelCallStartEvent(
            reply_id=self.state.reply_id,
            model_name=self.model.model,
        )

        kwargs = await self._prepare_model_input()
        today = date.today().strftime("%Y-%m-%d")

        # Stream tokens from the model
        full_text = ""
        block_id = uuid.uuid4().hex
        block_started = False
        input_tokens = 0
        output_tokens = 0

        try:
            res = await self._call_model(messages=kwargs["messages"], tools=[])

            if inspect.isasyncgen(res):
                async for chunk in res:
                    if chunk.is_last:
                        if chunk.usage:
                            input_tokens = chunk.usage.input_tokens
                            output_tokens = chunk.usage.output_tokens
                        continue
                    for block in chunk.content:
                        if isinstance(block, TextBlock) and block.text:
                            if not block_started:
                                yield TextBlockStartEvent(
                                    reply_id=self.state.reply_id, block_id=block_id,
                                )
                                block_started = True
                            full_text += block.text
                            yield TextBlockDeltaEvent(
                                reply_id=self.state.reply_id, block_id=block_id, delta=block.text,
                            )
            else:
                for block in res.content:
                    if isinstance(block, TextBlock) and block.text:
                        full_text += block.text
                if res.usage:
                    input_tokens = res.usage.input_tokens
                    output_tokens = res.usage.output_tokens
                if full_text:
                    yield TextBlockStartEvent(reply_id=self.state.reply_id, block_id=block_id)
                    block_started = True
                    yield TextBlockDeltaEvent(reply_id=self.state.reply_id, block_id=block_id, delta=full_text)

            logger.info("IntentAgent raw response: %s", full_text[:300])
            parse_result = self._parse_response(full_text, today)
        except Exception as exc:
            logger.error("IntentAgent LLM call failed: %s", exc)
            parse_result = None

        if block_started:
            yield TextBlockEndEvent(reply_id=self.state.reply_id, block_id=block_id)

        yield ModelCallEndEvent(
            reply_id=self.state.reply_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        if parse_result is None:
            parse_result = {
                "intent": "chat",
                "reply": f"你好！我是 {AGENT_NAME}，{AGENT_CAPABILITIES.replace('、', '、')}。有什么可以帮你的吗？",
                "business_type": None,
                "transaction": None,
            }

        text = json.dumps(parse_result, ensure_ascii=False, default=str)

        yield AssistantMsg(
            id=self.state.reply_id,
            name=self.name,
            content=text,
            metadata={"parse_result": parse_result},
        )

    def _parse_response(self, raw: str, today: str) -> dict | None:
        """Parse LLM response JSON into a structured result."""
        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM response as JSON: %s", raw[:200])
            return None

        intent = data.get("intent", "unknown")
        reply = data.get("reply", "")

        if intent == "chat":
            return {"intent": "chat", "reply": reply or "你好！我是 Ember，有什么可以帮你的吗？", "business_type": None, "transaction": None}

        if intent == "rule_query":
            return {"intent": "rule_query", "rule_type": data.get("rule_type"), "reply": reply, "business_type": None, "transaction": None}

        if intent == "rule_mgmt":
            return {"intent": "rule_mgmt", "action": data.get("action", "create"), "rule_type": data.get("rule_type"), "reply": reply, "business_type": None, "transaction": None}

        if intent == "voucher_query":
            return {"intent": "voucher_query", "status": data.get("status"), "reply": reply, "business_type": None, "transaction": None}

        if intent == "user_mgmt":
            return {"intent": "user_mgmt", "action": data.get("action", "create"), "new_username": data.get("new_username"), "new_display_name": data.get("new_display_name"), "new_role": data.get("new_role", "user"), "new_password": data.get("new_password"), "reply": reply, "business_type": None, "transaction": None}

        # intent == "business"
        business_type = data.get("business_type", "other")

        if business_type == "expense" and data.get("tax_excluded_amount") is not None and data.get("total_amount") is not None:
            txn = ExpenseTransaction(
                transaction_id=data.get("transaction_id", ""),
                company_code=data.get("company_code", "1000"),
                document_date=data.get("document_date", today),
                posting_date=data.get("posting_date", today),
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
                cost_center=data.get("cost_center", "CC-DEFAULT"),
            )
            return {"intent": "business", "business_type": business_type, "transaction": txn}

        if business_type != "sales_revenue" or data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
            return {"intent": "business", "business_type": business_type, "transaction": None}

        txn = SalesTransaction(
            transaction_id=data["transaction_id"],
            company_code=data.get("company_code", "1000"),
            document_date=data.get("document_date", today),
            posting_date=data.get("posting_date", today),
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
            cost_center=data.get("cost_center", "CC-DEFAULT"),
        )
        return {"intent": "business", "business_type": business_type, "transaction": txn}
