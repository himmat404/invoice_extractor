import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models import (
    BatchItemStatus,
    BatchSource,
    InvoiceStatus,
    JobKind,
    JobStatus,
)


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
