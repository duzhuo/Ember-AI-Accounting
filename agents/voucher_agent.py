"""Voucher generation agent — generates accounting vouchers from business transactions."""

import json
import logging
from dataclasses import asdict
from decimal import Decimal

from agentscope.agent import Agent
from agentscope.message import Msg, UserMsg, AssistantMsg
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit

from prompts import VOUCHER_GENERATION_PROMPT
from voucher_models import SalesTransaction, Voucher, VoucherLine
from voucher_rules import build_sales_revenue_voucher
from database import list_rules

from .middleware import SystemPromptMiddleware, LoggingMiddleware, TimingMiddleware, TracingMiddleware
from .model_factory import create_chat_model

logger = logging.getLogger(__name__)


async def query_rules(business_type: str) -> str:
    """查询指定业务类型的凭证生成规则。

    Args:
        business_type: 业务类型，如 sales_revenue
    """
    rules = await list_rules(business_type=business_type)
    if not rules:
        return json.dumps({"error": f"未找到 {business_type} 的规则"}, ensure_ascii=False)
    return json.dumps(rules, ensure_ascii=False, default=str)


class VoucherAgent(Agent):
    """Generate accounting voucher drafts from SalesTransaction data."""

    def __init__(self, name: str, offloader=None) -> None:
        toolkit = Toolkit(tools=[FunctionTool(query_rules, is_read_only=True)])
        state = AgentState(
            permission_context=PermissionContext(mode=PermissionMode.DONT_ASK),
        )
        super().__init__(
            name=name,
            system_prompt=VOUCHER_GENERATION_PROMPT,
            model=create_chat_model(),
            toolkit=toolkit,
            state=state,
            offloader=offloader,
            middlewares=[
                SystemPromptMiddleware(),
                LoggingMiddleware(),
                TimingMiddleware(),
                TracingMiddleware(),
            ],
        )

    async def reply(self, msg: Msg) -> Msg:
        txn = self._extract_transaction(msg)
        user_prompt = _build_user_prompt(txn)
        await self.observe(UserMsg(name="user", content=user_prompt))

        result_msg = await super().reply(msg)

        raw = result_msg.get_text_content() or ""
        voucher = _parse_llm_response(raw, txn)
        if voucher is None:
            logger.warning("LLM voucher parsing failed, falling back to rule engine.")
            voucher = build_sales_revenue_voucher(txn)

        voucher_dict = _voucher_to_dict(voucher)
        text = json.dumps(voucher_dict, ensure_ascii=False, default=str)

        return AssistantMsg(
            id=result_msg.id,
            name=self.name,
            content=text,
            metadata={"status": "generated", "voucher": voucher},
        )

    async def stream_reply(self, msg: Msg):
        """Streaming reply — yields events and the final Msg for SSE."""
        txn = self._extract_transaction(msg)
        user_prompt = _build_user_prompt(txn)
        await self.observe(UserMsg(name="user", content=user_prompt))

        final_msg = None
        async for event_or_msg in self._reply(inputs=msg):
            if isinstance(event_or_msg, Msg):
                final_msg = event_or_msg
            else:
                yield event_or_msg

        if final_msg:
            raw = final_msg.get_text_content() or ""
            voucher = _parse_llm_response(raw, txn)
            if voucher is None:
                logger.warning("LLM voucher parsing failed, falling back to rule engine.")
                voucher = build_sales_revenue_voucher(txn)
            voucher_dict = _voucher_to_dict(voucher)
            text = json.dumps(voucher_dict, ensure_ascii=False, default=str)
            yield AssistantMsg(
                id=final_msg.id,
                name=self.name,
                content=text,
                metadata={"status": "generated", "voucher": voucher},
            )

    def _extract_transaction(self, msg: Msg) -> SalesTransaction:
        """Extract SalesTransaction from message content or metadata."""
        if msg.metadata and "transaction" in msg.metadata:
            return msg.metadata["transaction"]

        data = json.loads(msg.get_text_content() or "{}")
        return _dict_to_transaction(data)


def _build_user_prompt(txn: SalesTransaction) -> str:
    txn_dict = asdict(txn)
    for key, value in txn_dict.items():
        if isinstance(value, Decimal):
            txn_dict[key] = str(value)

    return (
        "请根据以下销售业务数据生成会计凭证草稿：\n\n"
        f"```json\n{json.dumps(txn_dict, ensure_ascii=False, indent=2)}\n```"
    )


def _dict_to_transaction(data: dict) -> SalesTransaction:
    return SalesTransaction(
        transaction_id=data["transaction_id"],
        company_code=data.get("company_code", "1000"),
        document_date=data.get("document_date", ""),
        posting_date=data.get("posting_date", ""),
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


def _parse_llm_response(raw: str, txn: SalesTransaction) -> Voucher | None:
    """Parse LLM JSON response into a Voucher object. Returns None on failure."""
    json_str = _extract_json(raw)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse voucher JSON: %s", raw[:200])
        return None

    if not isinstance(data, dict) or "lines" not in data:
        return None

    try:
        lines = [
            VoucherLine(
                line_no=line_data["line_no"],
                debit_credit=line_data["debit_credit"],
                account_code=line_data["account_code"],
                account_name=line_data["account_name"],
                amount=Decimal(str(line_data["amount"])),
                currency=txn.currency,
                customer_code=line_data.get("customer_code", ""),
                customer_name=line_data.get("customer_name", ""),
                tax_code=line_data.get("tax_code", ""),
                profit_center=line_data.get("profit_center", ""),
                cost_center=line_data.get("cost_center", ""),
                assignment=line_data.get("assignment", ""),
                text=line_data.get("text", ""),
            )
            for line_data in data["lines"]
        ]
    except (KeyError, ValueError) as exc:
        logger.warning("Failed to parse voucher lines: %s", exc)
        return None

    warnings: list[str] = list(data.get("warnings", []))

    debit_total = sum(line.amount for line in lines if line.debit_credit == "S")
    credit_total = sum(line.amount for line in lines if line.debit_credit == "H")
    if abs(debit_total - credit_total) > Decimal("0.01"):
        warnings.append(f"Voucher not balanced: debit={debit_total}, credit={credit_total}")

    confidence = Decimal(str(data.get("confidence", 0.70)))
    if warnings and confidence > Decimal("0.70"):
        confidence = Decimal("0.70")

    return Voucher(
        voucher_id=f"VR-{txn.transaction_id}",
        company_code=txn.company_code,
        document_type="DR",
        document_date=txn.document_date,
        posting_date=txn.posting_date,
        reference=txn.invoice_no,
        header_text=data.get("header_text", ""),
        source_transaction_id=txn.transaction_id,
        confidence=confidence,
        warnings=warnings,
        lines=lines,
    )


def _extract_json(text: str) -> str:
    """Extract JSON from a possibly markdown-wrapped LLM response."""
    text = text.strip()
    if "```json" in text:
        return text.split("```json")[1].split("```")[0].strip()
    if "```" in text:
        return text.split("```")[1].split("```")[0].strip()
    return text


def _voucher_to_dict(voucher) -> dict:
    """Convert a Voucher object to a frontend-compatible dict."""
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
