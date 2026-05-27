"""Data models for accounting voucher generation."""

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class SalesTransaction:
    """A normalized sales revenue business record."""

    transaction_id: str
    company_code: str
    document_date: str
    posting_date: str
    customer_code: str
    customer_name: str
    product_type: str
    contract_no: str
    invoice_no: str
    currency: str
    tax_rate: Decimal
    tax_excluded_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    profit_center: str
    cost_center: str


@dataclass(frozen=True)
class ExpenseTransaction:
    """A normalized expense / reimbursement business record."""

    transaction_id: str
    company_code: str
    document_date: str
    posting_date: str
    vendor_code: str
    vendor_name: str
    expense_category: str
    receipt_no: str
    description: str
    currency: str
    tax_rate: Decimal
    tax_excluded_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    profit_center: str
    cost_center: str


@dataclass(frozen=True)
class VoucherLine:
    """One debit or credit line in a voucher."""

    line_no: int
    debit_credit: str
    account_code: str
    account_name: str
    amount: Decimal
    currency: str
    customer_code: str = ""
    customer_name: str = ""
    tax_code: str = ""
    profit_center: str = ""
    cost_center: str = ""
    assignment: str = ""
    text: str = ""


@dataclass(frozen=True)
class Voucher:
    """A generated accounting voucher draft."""

    voucher_id: str
    company_code: str
    document_type: str
    document_date: str
    posting_date: str
    reference: str
    header_text: str
    source_transaction_id: str
    confidence: Decimal
    warnings: list[str] = field(default_factory=list)
    lines: list[VoucherLine] = field(default_factory=list)
