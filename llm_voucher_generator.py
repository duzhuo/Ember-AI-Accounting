"""LLM-based accounting voucher generator.

Uses an OpenAI-compatible API (DeepSeek) to generate accounting voucher drafts
from sales transactions. Falls back to the rule engine if the LLM call fails.
"""

import json
import logging
import os
from dataclasses import asdict
from decimal import Decimal

from openai import AsyncOpenAI

from prompts import VOUCHER_GENERATION_PROMPT
from voucher_models import SalesTransaction, Voucher, VoucherLine
from voucher_rules import build_sales_revenue_voucher

logger = logging.getLogger(__name__)

# ── Model configuration ──────────────────────────────────────────────────────

MODEL_BASE_URL = os.environ.get(
    "PMDE_BASE_URL",
    "https://ark.cn-beijing.volces.com/api/coding/v3",
)
MODEL_API_KEY = os.environ.get(
    "PMDE_API_KEY",
    "4fea2171-9079-434e-bdf5-d98a00db9363",
)
MODEL_NAME = os.environ.get("PMDE_MODEL_NAME", "deepseek-v4-pro")

# ── Generator ─────────────────────────────────────────────────────────────────

class LLMVoucherGenerator:
    """Generate accounting voucher drafts using an LLM, with rule-engine fallback."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=MODEL_BASE_URL,
            api_key=MODEL_API_KEY,
        )

    async def generate(self, txn: SalesTransaction) -> Voucher:
        """Generate a voucher draft. Falls back to the rule engine on LLM failure."""
        try:
            return await self._generate_with_llm(txn)
        except Exception as exc:
            logger.warning(
                "LLM generation failed (%s: %s). Falling back to rule engine.",
                type(exc).__name__,
                exc,
            )
            return build_sales_revenue_voucher(txn)

    # ── LLM call ──────────────────────────────────────────────────────────

    async def _generate_with_llm(self, txn: SalesTransaction) -> Voucher:
        user_prompt = _build_user_prompt(txn)

        completion = await self._client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": VOUCHER_GENERATION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )

        raw = completion.choices[0].message.content
        return _parse_llm_response(raw, txn)


# ── Prompt helpers ────────────────────────────────────────────────────────────

def _build_user_prompt(txn: SalesTransaction) -> str:
    txn_dict = asdict(txn)
    for key, value in txn_dict.items():
        if isinstance(value, Decimal):
            txn_dict[key] = str(value)

    return (
        "请根据以下销售业务数据生成会计凭证草稿：\n\n"
        f"```json\n{json.dumps(txn_dict, ensure_ascii=False, indent=2)}\n```"
    )


# ── Response parsing ──────────────────────────────────────────────────────────

def _parse_llm_response(raw: str, txn: SalesTransaction) -> Voucher:
    json_str = _extract_json(raw)
    data = json.loads(json_str)

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
        for line_data in data.get("lines", [])
    ]

    warnings: list[str] = list(data.get("warnings", []))

    # Validate balance
    debit_total = sum(line.amount for line in lines if line.debit_credit == "S")
    credit_total = sum(line.amount for line in lines if line.debit_credit == "H")
    if abs(debit_total - credit_total) > Decimal("0.01"):
        warnings.append(
            f"Voucher not balanced: debit={debit_total}, credit={credit_total}"
        )

    # Validate amount consistency
    calculated_total = txn.tax_excluded_amount + txn.tax_amount
    if abs(calculated_total - txn.total_amount) > Decimal("0.01"):
        warnings.append("Total amount does not equal tax excluded amount plus tax amount.")

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
