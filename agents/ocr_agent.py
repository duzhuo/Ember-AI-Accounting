"""OCR agent — extracts transaction data from invoice images and PDFs."""

import base64
import inspect
import json
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

from agentscope.agent import Agent
from agentscope.event import (
    ModelCallStartEvent,
    ModelCallEndEvent,
    TextBlockStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
)
from agentscope.message import Msg, UserMsg, SystemMsg, AssistantMsg, DataBlock, TextBlock, Base64Source

from prompts import IMAGE_PARSE_SYSTEM_PROMPT
from voucher_models import SalesTransaction, ExpenseTransaction

from .middleware import SystemPromptMiddleware, LoggingMiddleware, TimingMiddleware, TracingMiddleware
from .model_factory import create_chat_model

logger = logging.getLogger(__name__)


class OcrAgent(Agent):
    """Extract structured transaction data from invoice images or PDFs."""

    def __init__(self, name: str) -> None:
        super().__init__(
            name=name,
            system_prompt=IMAGE_PARSE_SYSTEM_PROMPT,
            model=create_chat_model(vision=True),
            middlewares=[
                SystemPromptMiddleware(),
                LoggingMiddleware(),
                TimingMiddleware(),
                TracingMiddleware(),
            ],
        )
        self._file_path: Path = Path()
        self._file_type: str = "image"

    async def reply(self, msg: Msg) -> Msg:
        self._file_path = Path(msg.metadata.get("file_path", "")) if msg.metadata else Path()
        self._file_type = msg.metadata.get("file_type", "image") if msg.metadata else "image"
        return await super().reply(msg)

    async def _prepare_model_input(self) -> dict:
        today = date.today().strftime("%Y-%m-%d")

        if self._file_type == "pdf":
            blocks = _build_pdf_blocks(self._file_path, today)
        else:
            blocks = _build_image_blocks(self._file_path, today)

        messages = [
            SystemMsg(name="system", content=self._system_prompt),
            UserMsg(name="user", content=blocks),
        ]
        return {"messages": messages, "tools": []}

    async def _reasoning_impl(self, tool_choice=None):
        yield ModelCallStartEvent(
            reply_id=self.state.reply_id,
            model_name=self.model.model,
        )

        kwargs = await self._prepare_model_input()
        today = date.today().strftime("%Y-%m-%d")

        try:
            res = await self._call_model(messages=kwargs["messages"], tools=[])

            full_text = ""
            if inspect.isasyncgen(res):
                async for chunk in res:
                    if chunk.is_last:
                        continue
                    for block in chunk.content:
                        if isinstance(block, TextBlock) and block.text:
                            full_text += block.text
            else:
                for block in res.content:
                    if isinstance(block, TextBlock) and block.text:
                        full_text += block.text

            logger.info("OcrAgent raw response: %s", full_text[:500])
            result_data = self._parse_llm_response(full_text, today)
        except Exception as exc:
            logger.error("OcrAgent parse failed: %s", exc)
            result_data = None

        text = json.dumps(result_data, ensure_ascii=False, default=str) if result_data else "{}"

        yield ModelCallEndEvent(reply_id=self.state.reply_id, input_tokens=0, output_tokens=0)

        block_id = __import__("uuid").uuid4().hex
        yield TextBlockStartEvent(reply_id=self.state.reply_id, block_id=block_id)
        yield TextBlockDeltaEvent(reply_id=self.state.reply_id, block_id=block_id, delta=text)
        yield TextBlockEndEvent(reply_id=self.state.reply_id, block_id=block_id)

        yield AssistantMsg(
            id=self.state.reply_id,
            name=self.name,
            content=text,
            metadata={"ocr_result": result_data},
        )

    def _parse_llm_response(self, raw: str, today: str) -> dict | None:
        """Parse LLM JSON response into a structured result."""
        json_str = raw.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse OCR response as JSON: %s", raw[:300])
            return None
        business_type = data.get("business_type", "other")

        if business_type not in ("sales_revenue", "expense"):
            return {"business_type": business_type, "transaction": None}

        if data.get("tax_excluded_amount") is None or data.get("total_amount") is None:
            return {"business_type": business_type, "transaction": None}

        if business_type == "expense":
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
            return {"business_type": business_type, "transaction": txn}

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
        return {"business_type": business_type, "transaction": txn}


def _build_image_blocks(image_path: Path, today: str) -> list:
    image_bytes = image_path.read_bytes()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    ext = image_path.suffix.lower()
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }
    mime_type = mime_map.get(ext, "image/jpeg")

    return [
        DataBlock(source=Base64Source(data=b64_image, media_type=mime_type)),
        TextBlock(text=f"当前日期：{today}\n\n请识别这张发票/单据图片，提取交易数据。"),
    ]


def _build_pdf_blocks(pdf_path: Path, today: str) -> list:
    pages = _pdf_to_images(pdf_path)
    blocks = []
    for img_bytes, mime_type in pages:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        blocks.append(DataBlock(source=Base64Source(data=b64, media_type=mime_type)))

    page_note = ""
    if len(pages) > 1:
        page_note = f"该PDF共{len(pages)}页，请识别其中包含发票/单据的页面并提取数据。"

    blocks.append(TextBlock(text=f"当前日期：{today}\n\n请识别这张发票/单据，提取交易数据。{page_note}"))
    return blocks


def _pdf_to_images(pdf_path: Path) -> list[tuple[bytes, str]]:
    """Convert PDF pages to PNG images."""
    import fitz

    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        pages.append((pix.tobytes("png"), "image/png"))
    doc.close()
    return pages
