"""Intent recognition agent — classifies user intent and extracts structured data."""

import json
import logging
from datetime import date
from decimal import Decimal

from agentscope.agent import AgentBase
from agentscope.message import Msg

from prompts import NL_PARSE_SYSTEM_PROMPT
from voucher_models import SalesTransaction

from .model_factory import create_chat_model, create_formatter

logger = logging.getLogger(__name__)


class IntentAgent(AgentBase):
    """Classify user intent and extract business data from natural language."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.model = create_chat_model()
        self.formatter = create_formatter()
        self.history: list[Msg] = []

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        if msg is None:
            return
        if isinstance(msg, list):
            self.history.extend(msg)
        else:
            self.history.append(msg)

    async def reply(self, msg: Msg | None = None) -> Msg:
        await self.observe(msg)
        if msg is None:
            return Msg(name=self.name, role="assistant", content="{}")

        message = msg.get_text_content()
        conversation_history = msg.metadata.get("history", []) if msg.metadata else []

        today = date.today().strftime("%Y-%m-%d")
        user_prompt = (
            f"当前日期：{today}\n\n用户输入：{message}\n\n"
            "请先判断用户意图（intent），再进行后续处理。"
        )

        # Build messages for the LLM
        messages = [{"role": "system", "content": NL_PARSE_SYSTEM_PROMPT}]
        for hist_msg in conversation_history[-200:]:
            messages.append({"role": hist_msg["role"], "content": hist_msg["content"]})
        messages.append({"role": "user", "content": user_prompt})

        try:
            response = await self.model(messages)
            raw = response.get_text_content() or ""
            logger.info("IntentAgent raw response: %s", raw[:300])
            parse_result = self._parse_response(raw, today)
        except Exception as exc:
            logger.error("IntentAgent LLM call failed: %s", exc)
            parse_result = None

        # Fallback: if parsing failed, treat as chat with a friendly reply
        if parse_result is None:
            parse_result = {
                "intent": "chat",
                "reply": "你好！我是 Ember，你的智能记账助手。我可以帮你生成会计凭证、管理凭证规则、查询凭证记录等。有什么可以帮你的吗？",
                "business_type": None,
                "transaction": None,
            }

        result = Msg(
            name=self.name,
            role="assistant",
            content=json.dumps(parse_result, ensure_ascii=False, default=str) if parse_result else "{}",
            metadata={"parse_result": parse_result},
        )
        await self.print(result)
        return result

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

        if intent == "chat":
            return {
                "intent": "chat",
                "reply": data.get("reply", "你好！我是 Ember，有什么可以帮你的吗？"),
                "business_type": None,
                "transaction": None,
            }

        if intent == "rule_query":
            return {
                "intent": "rule_query",
                "rule_type": data.get("rule_type"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        if intent == "rule_mgmt":
            return {
                "intent": "rule_mgmt",
                "action": data.get("action", "create"),
                "rule_type": data.get("rule_type"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        if intent == "voucher_query":
            return {
                "intent": "voucher_query",
                "status": data.get("status"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        if intent == "user_mgmt":
            return {
                "intent": "user_mgmt",
                "action": data.get("action", "create"),
                "new_username": data.get("new_username"),
                "new_display_name": data.get("new_display_name"),
                "new_role": data.get("new_role", "user"),
                "new_password": data.get("new_password"),
                "reply": data.get("reply", ""),
                "business_type": None,
                "transaction": None,
            }

        # intent == "business"
        business_type = data.get("business_type", "other")

        if business_type != "sales_revenue":
            return {"intent": "business", "business_type": business_type, "transaction": None}

        if data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
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
