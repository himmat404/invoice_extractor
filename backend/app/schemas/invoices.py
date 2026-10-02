import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    BatchItemStatus,
    BatchSource,
    InvoiceStatus,
    JobKind,
    JobStatus,
)
from app.services.ai.schema import BankDetails, LineItem, Party, TaxLine


class InvoiceOut(BaseModel):
    id: uuid.UUID
    status: InvoiceStatus
    status_label: str
    batch_id: uuid.UUID | None
    filename: str
    content_type: str
    size_bytes: int
    page_count: int | None
    error_code: str | None
    error_message: str | None
    extraction_result: dict[str, Any] | None
    created_at: datetime
    processed_at: datetime | None
    can_cancel: bool
    can_retry: bool
    can_reprocess: bool
    # searchable header (from the current data)
    invoice_number: str | None = None
    invoice_date: date | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    currency: str | None = None
    grand_total: Decimal | None = None
    validation_status: str | None = None
    duplicate_status: str = "no_match"
    approved_at: datetime | None = None


class ValidationIssueOut(BaseModel):
    code: str
    severity: str
    message: str
    field: str | None
    details: dict[str, Any] | None = None


class ValidationOut(BaseModel):
    status: str
    rules_version: str
    issues: list[ValidationIssueOut]
    computed: dict[str, Any]


class MatchedInvoiceOut(BaseModel):
    id: uuid.UUID
    invoice_number: str | None
    supplier_name: str | None
    invoice_date: date | None
    grand_total: Decimal | None
    currency: str | None
    status: str
    filename: str


class DuplicateMatchOut(BaseModel):
    id: uuid.UUID
    matched_invoice: MatchedInvoiceOut
    rule_name: str
    outcome: str
    score: float | None
    matched_fields: list[str]
    decision: str


class InvoiceDetailOut(InvoiceOut):
    data: dict[str, Any] | None
    edited_fields: list[str]
    reviewed_at: datetime | None
    validation: ValidationOut | None
    confidence: dict[str, Any] | None
    duplicates: list[DuplicateMatchOut]
    can_edit: bool
    can_approve: bool


class InvoiceDataIn(BaseModel):
    """Corrected invoice data submitted from the review screen."""

    model_config = ConfigDict(extra="forbid")

    invoice_number: str | None = Field(None, max_length=100)
    invoice_date: date | None = None
    due_date: date | None = None
    po_number: str | None = Field(None, max_length=100)
    reference_number: str | None = Field(None, max_length=100)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    payment_terms: str | None = Field(None, max_length=300)
    notes: str | None = Field(None, max_length=2000)
    supplier: Party = Field(default_factory=Party)
    customer: Party = Field(default_factory=Party)
    bank_details: BankDetails = Field(default_factory=BankDetails)
    line_items: list[LineItem] = Field(default_factory=list, max_length=500)
    subtotal: Decimal | None = None
    shipping: Decimal | None = None
    other_charges: Decimal | None = None
    total_discount: Decimal | None = None
    taxes: list[TaxLine] = Field(default_factory=list, max_length=50)
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    total_tax: Decimal | None = None
    grand_total: Decimal | None = None
    amount_paid: Decimal | None = None
    balance_due: Decimal | None = None


class ApproveRequest(BaseModel):
    acknowledge_issues: bool = False


class DuplicateDecisionRequest(BaseModel):
    decision: Literal["keep_both", "mark_duplicate", "cancel"]


class BatchItemOut(BaseModel):
    id: uuid.UUID
    position: int
    filename: str
    archive_name: str | None
    status: BatchItemStatus
    rejection_code: str | None
    rejection_message: str | None
    invoice_id: uuid.UUID | None
    invoice_status: InvoiceStatus | None
    invoice_status_label: str | None
    duplicate_of_invoice_id: uuid.UUID | None


class BatchCounts(BaseModel):
    total: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicate: int = 0
    queued: int = 0
    processing: int = 0
    completed: int = 0
    review_required: int = 0
    failed: int = 0
    canceled: int = 0


class BatchSummaryOut(BaseModel):
    id: uuid.UUID
    name: str | None
    source: BatchSource
    status: str
    created_at: datetime
    canceled_at: datetime | None
    counts: BatchCounts


class BatchOut(BatchSummaryOut):
    items: list[BatchItemOut]


class DownloadOut(BaseModel):
    url: str
    expires_in: int


class AdminJobOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    invoice_id: uuid.UUID
    batch_id: uuid.UUID | None
    kind: JobKind
    status: JobStatus
    attempts: int
    max_attempts: int
    available_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    lease_expires_at: datetime | None
    worker_id: str | None
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    requested_by_admin_id: uuid.UUID | None


class AdminJobDetailOut(AdminJobOut):
    attempt_log: list[dict[str, Any]]
    invoice_status: InvoiceStatus
    filename: str


class QueueStatsOut(BaseModel):
    by_status: dict[str, int]
    ready_now: int
    oldest_ready_seconds: float | None
    failed_last_24h: int
