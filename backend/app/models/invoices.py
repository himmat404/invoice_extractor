import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.models.identity import _enum


class InvoiceStatus(enum.StrEnum):
    """Processing status model (spec 10)."""

    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    VALIDATION_WARNING = "validation_warning"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    EXPORTED = "exported"
    FAILED = "failed"
    CANCELED = "canceled"
    ARCHIVED = "archived"


#: Customer-facing labels; avoid exposing implementation details (spec 10).
INVOICE_STATUS_LABELS = {
    InvoiceStatus.UPLOADED: "Uploaded",
    InvoiceStatus.QUEUED: "Waiting to process",
    InvoiceStatus.PROCESSING: "Processing",
    InvoiceStatus.EXTRACTED: "Ready",
    InvoiceStatus.VALIDATION_WARNING: "Check warnings",
    InvoiceStatus.NEEDS_REVIEW: "Needs review",
    InvoiceStatus.APPROVED: "Approved",
    InvoiceStatus.EXPORTED: "Exported",
    InvoiceStatus.FAILED: "Couldn't process",
    InvoiceStatus.CANCELED: "Canceled",
    InvoiceStatus.ARCHIVED: "Deleted",
}


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.PROCESSING)


class JobKind(enum.StrEnum):
    INITIAL = "initial"
    REPROCESS = "reprocess"


class BatchSource(enum.StrEnum):
    WEB = "web"
    API = "api"


class BatchItemStatus(enum.StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


class FileKind(enum.StrEnum):
    ORIGINAL = "original"  # exactly what the customer uploaded
    DERIVED = "derived"  # e.g. HEIC converted to JPEG for processing


class Batch(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "batches"
    __table_args__ = (Index("ix_batches_ws_created", "workspace_id", "created_at"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[BatchSource] = mapped_column(
        _enum(BatchSource, "batch_source"), default=BatchSource.WEB
    )
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["BatchItem"]] = relationship(
        back_populates="batch", order_by="BatchItem.position"
    )


class BatchItem(UUIDPrimaryKey, Base):
    __tablename__ = "batch_items"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batches.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(300))
    archive_name: Mapped[str | None] = mapped_column(String(300))  # source ZIP, if any
    status: Mapped[BatchItemStatus] = mapped_column(_enum(BatchItemStatus, "batch_item_status"))
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    rejection_message: Mapped[str | None] = mapped_column(String(300))
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL")
    )
    duplicate_of_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL")
    )

    batch: Mapped[Batch] = relationship(back_populates="items")
    invoice: Mapped["Invoice | None"] = relationship(foreign_keys=[invoice_id])


class InvoiceFile(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "invoice_files"
    __table_args__ = (Index("ix_invoice_files_ws_sha256", "workspace_id", "sha256"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    kind: Mapped[FileKind] = mapped_column(_enum(FileKind, "file_kind"), default=FileKind.ORIGINAL)
    parent_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoice_files.id", ondelete="CASCADE")
    )
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invoice(UUIDPrimaryKey, Timestamps, Base):
    """A customer-uploaded business invoice (never InvoiceFlow's own billing invoices)."""

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_ws_created", "workspace_id", "created_at"),
        Index("ix_invoices_ws_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batches.id", ondelete="SET NULL"), index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoice_files.id", ondelete="RESTRICT")
    )
    processing_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoice_files.id", ondelete="SET NULL")
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        _enum(InvoiceStatus, "invoice_status"), default=InvoiceStatus.UPLOADED
    )
    # Customer-safe failure explanation; detailed diagnostics live on the job (admin only).
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))
    extraction_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    credit_consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    file: Mapped[InvoiceFile] = relationship(foreign_keys=[file_id], lazy="joined")
    processing_file: Mapped[InvoiceFile | None] = relationship(foreign_keys=[processing_file_id])


class ExtractionJob(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "extraction_jobs"
    __table_args__ = (
        # At most one active job per invoice: makes enqueueing idempotent.
        Index(
            "uq_extraction_jobs_active_per_invoice",
            "invoice_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
        Index("ix_extraction_jobs_claim", "status", "available_at"),
        Index("ix_extraction_jobs_ws_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batches.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[JobKind] = mapped_column(_enum(JobKind, "job_kind"), default=JobKind.INITIAL)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(100))
    # Internal diagnostics: shown in the admin console only.
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(String(2000))
    attempt_log: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    requested_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    # Recorded on success for support, cost analysis and audit (spec 9, 14). Internal only.
    provider_code: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(100))
    model_configuration_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    extraction_schema_version: Mapped[str | None] = mapped_column(String(16))
    confidence_method: Mapped[str | None] = mapped_column(String(64))

    invoice: Mapped[Invoice] = relationship()
