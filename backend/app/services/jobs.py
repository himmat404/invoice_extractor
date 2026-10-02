"""Extraction job queue and lifecycle (spec 9, 10, 14, 21.3).

The queue lives in Postgres: jobs are created in the same transaction as their invoice, workers
claim them with ``FOR UPDATE SKIP LOCKED``, and a per-workspace concurrency cap keeps one
workspace from monopolising shared workers. Handlers run outside any DB transaction; results are
applied only if the worker still holds the job's lease.

Credits: one credit per invoice, consumed on the first successful extraction. Reprocessing does
not charge again unless the ``processing.reprocess_consumes_credit`` setting is enabled. Failed
jobs never consume credits.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    ACTIVE_JOB_STATUSES,
    ExtractionJob,
    Invoice,
    InvoiceStatus,
    JobKind,
    JobStatus,
    Workspace,
)
from app.models.base import utcnow
from app.services import credits
from app.services.audit import record_activity
from app.services.entitlements import effective_entitlements
from app.services.settings_store import get_setting
from app.services.subscriptions import current_subscription, record_usage

logger = logging.getLogger("invoiceflow.jobs")


# --- handler contract ---------------------------------------------------------------------------


@dataclass
class JobContext:
    job_id: uuid.UUID
    workspace_id: uuid.UUID
    invoice_id: uuid.UUID
    attempt: int
    filename: str
    content_type: str
    data: bytes


@dataclass
class ExtractionOutcome:
    data: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


class JobError(Exception):
    """Raised by handlers. ``detail`` is internal (admin console only)."""

    retryable = False

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class RetryableJobError(JobError):
    retryable = True


class PermanentJobError(JobError):
    retryable = False


Handler = Callable[[JobContext], ExtractionOutcome]
_handlers: dict[str, Handler] = {}


def register_handler(name: str, handler: Handler) -> None:
    _handlers[name] = handler


def get_handler() -> Handler:
    name = get_settings().extraction_handler
    if name not in _handlers:
        raise RuntimeError(f"No extraction handler registered as {name!r}")
    return _handlers[name]


def _fake_handler(ctx: JobContext) -> ExtractionOutcome:
    """Development stand-in until the AI orchestrator (Phase 4) is wired in."""
    lowered = ctx.filename.lower()
    if "fail" in lowered:
        raise PermanentJobError("extraction_failed", "fake handler: forced permanent failure")
    if "flaky" in lowered and ctx.attempt == 1:
        raise RetryableJobError("provider_unavailable", "fake handler: forced transient failure")
    return ExtractionOutcome(data={}, metadata={"handler": "fake", "bytes": len(ctx.data)})


register_handler("fake", _fake_handler)


# --- customer-safe messages ---------------------------------------------------------------------

CUSTOMER_MESSAGES = {
    "extraction_failed": "We couldn't read this invoice. Try a clearer scan, then retry.",
    "provider_unavailable": "Processing is temporarily unavailable. Please retry in a few minutes.",
    "timeout": "Processing took too long. Please retry.",
    "insufficient_credits": "You ran out of invoice credits. Add credits, then retry.",
    "file_missing": "The original file is no longer available.",
}
DEFAULT_MESSAGE = "Something went wrong while processing this invoice. Please retry."


def customer_message(code: str | None) -> str:
    return CUSTOMER_MESSAGES.get(code or "", DEFAULT_MESSAGE)


# --- lifecycle ----------------------------------------------------------------------------------


def active_job(db: Session, invoice_id: uuid.UUID) -> ExtractionJob | None:
    return db.scalar(
        select(ExtractionJob).where(
            ExtractionJob.invoice_id == invoice_id,
            ExtractionJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    )


def enqueue(
    db: Session,
    invoice: Invoice,
    *,
    kind: JobKind = JobKind.INITIAL,
    requested_by_admin_id: uuid.UUID | None = None,
) -> ExtractionJob:
    """Queue processing for an invoice. Idempotent: returns the existing active job if any."""
    if existing := active_job(db, invoice.id):
        return existing
    job = ExtractionJob(
        workspace_id=invoice.workspace_id,
        invoice_id=invoice.id,
        batch_id=invoice.batch_id,
        kind=kind,
        status=JobStatus.QUEUED,
        max_attempts=get_settings().job_max_attempts,
        available_at=utcnow(),
        attempt_log=[],
        requested_by_admin_id=requested_by_admin_id,
    )
    db.add(job)
    if kind == JobKind.INITIAL or invoice.extraction_result is None:
        invoice.status = InvoiceStatus.QUEUED
    invoice.error_code = invoice.error_message = None
    db.flush()
    return job


def pending_uncharged_jobs(db: Session, workspace_id: uuid.UUID) -> int:
    """Active jobs whose invoice hasn't been charged yet (credits they will need)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(ExtractionJob)
            .join(Invoice, Invoice.id == ExtractionJob.invoice_id)
            .where(
                ExtractionJob.workspace_id == workspace_id,
                ExtractionJob.status.in_(ACTIVE_JOB_STATUSES),
                Invoice.credit_consumed.is_(False),
            )
        )
        or 0
    )


_CLAIM_SQL = text(
    """
    SELECT j.id FROM extraction_jobs j
    WHERE j.status = 'queued' AND j.available_at <= now()
      AND (SELECT count(*) FROM extraction_jobs p
           WHERE p.workspace_id = j.workspace_id AND p.status = 'processing') < :per_workspace
    ORDER BY j.available_at, j.created_at
    LIMIT 1
    FOR UPDATE SKIP LOCKED
    """
)


def claim_next(db: Session, worker_id: str) -> ExtractionJob | None:
    s = get_settings()
    job_id = db.scalar(_CLAIM_SQL, {"per_workspace": s.max_concurrent_jobs_per_workspace})
    if job_id is None:
        db.rollback()
        return None
    job = db.get(ExtractionJob, job_id)
    now = utcnow()
    job.status = JobStatus.PROCESSING
    job.attempts += 1
    job.started_at = now
    job.lease_expires_at = now + timedelta(seconds=s.job_lease_seconds)
    job.worker_id = worker_id
    if job.kind == JobKind.INITIAL or job.invoice.extraction_result is None:
        job.invoice.status = InvoiceStatus.PROCESSING
    db.commit()
    return job


def _log_attempt(job: ExtractionJob, outcome: str, code: str | None = None) -> None:
    job.attempt_log = [
        *(job.attempt_log or []),
        {
            "attempt": job.attempts,
            "worker": job.worker_id,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": utcnow().isoformat(),
            "outcome": outcome,
            "error_code": code,
        },
    ]


def _restore_or_fail(invoice: Invoice, job: ExtractionJob, status: InvoiceStatus, code: str):
    """A failed/canceled reprocess keeps the previous results; otherwise the invoice takes
    ``status``."""
    if job.kind == JobKind.REPROCESS and invoice.extraction_result is not None:
        invoice.status = InvoiceStatus.EXTRACTED
        invoice.error_code = code
        invoice.error_message = "Reprocessing didn't finish; your previous results were kept."
    else:
        invoice.status = status
        if status == InvoiceStatus.FAILED:
            invoice.error_code = code
            invoice.error_message = customer_message(code)


def complete(db: Session, job: ExtractionJob, outcome: ExtractionOutcome) -> None:
    invoice = job.invoice
    workspace = db.get(Workspace, job.workspace_id)
    sub = current_subscription(db, job.workspace_id)
    charge_again = bool(get_setting(db, "processing.reprocess_consumes_credit", False))
    if not invoice.credit_consumed or (job.kind == JobKind.REPROCESS and charge_again):
        key = f"invoice:{invoice.id}" if not invoice.credit_consumed else f"job:{job.id}"
        entitlements = effective_entitlements(sub.plan if sub else None, workspace)
        try:
            credits.consume_credit(
                db,
                job.workspace_id,
                idempotency_key=key,
                reference=credits.Reference("invoice", str(invoice.id)),
                subscription_id=sub.id if sub else None,
                overage_allowed=entitlements.overage_allowed,
            )
        except credits.InsufficientCreditsError:
            fail(db, job, PermanentJobError("insufficient_credits", "no credits at completion"))
            return
        invoice.credit_consumed = True
        record_usage(
            db,
            job.workspace_id,
            "invoice_processed",
            subscription=sub,
            reference=credits.Reference("invoice", str(invoice.id)),
            idempotency_key=key,
        )
    invoice.extraction_result = outcome.data
    invoice.status = InvoiceStatus.EXTRACTED
    invoice.processed_at = utcnow()
    invoice.error_code = invoice.error_message = None
    job.status = JobStatus.SUCCEEDED
    job.finished_at = utcnow()
    job.lease_expires_at = None
    job.error_code = job.error_detail = None
    _log_attempt(job, "succeeded")
    record_activity(
        db,
        workspace_id=job.workspace_id,
        actor_type="system",
        action="invoice.processed",
        entity_type="invoice",
        entity_id=invoice.id,
        summary={"job_id": str(job.id), "kind": job.kind.value},
    )


def fail(db: Session, job: ExtractionJob, error: JobError) -> None:
    s = get_settings()
    job.error_code = error.code
    job.error_detail = (error.detail or "")[:2000]
    job.lease_expires_at = None
    _log_attempt(job, "retrying" if error.retryable else "failed", error.code)
    if error.retryable and job.attempts < job.max_attempts:
        delay = s.job_retry_base_seconds * 2 ** (job.attempts - 1)
        job.status = JobStatus.QUEUED
        job.available_at = utcnow() + timedelta(seconds=delay)
        job.worker_id = None
        if job.kind == JobKind.INITIAL or job.invoice.extraction_result is None:
            job.invoice.status = InvoiceStatus.QUEUED
        return
    job.status = JobStatus.FAILED
    job.finished_at = utcnow()
    _restore_or_fail(job.invoice, job, InvoiceStatus.FAILED, error.code)
    record_activity(
        db,
        workspace_id=job.workspace_id,
        actor_type="system",
        action="invoice.processing_failed",
        entity_type="invoice",
        entity_id=job.invoice_id,
        outcome="failure",
        summary={"job_id": str(job.id), "reason": error.code},
    )


def cancel(db: Session, job: ExtractionJob) -> bool:
    """Cancel a job that hasn't started. Running jobs can't be interrupted."""
    if job.status != JobStatus.QUEUED:
        return False
    job.status = JobStatus.CANCELED
    job.finished_at = utcnow()
    _log_attempt(job, "canceled")
    _restore_or_fail(job.invoice, job, InvoiceStatus.CANCELED, "canceled")
    return True


def requeue_expired(db: Session) -> int:
    """Recover jobs whose worker died mid-processing."""
    expired = db.scalars(
        select(ExtractionJob)
        .where(
            ExtractionJob.status == JobStatus.PROCESSING, ExtractionJob.lease_expires_at < utcnow()
        )
        .with_for_update(skip_locked=True)
    ).all()
    for job in expired:
        fail(db, job, RetryableJobError("timeout", "lease expired before the worker finished"))
    db.commit()
    return len(expired)


def run_job(db: Session, job: ExtractionJob, worker_id: str) -> None:
    """Execute a claimed job and apply its result, unless the lease was lost meanwhile."""
    from app.services.storage import StorageError, get_storage

    invoice = job.invoice
    file = invoice.processing_file or invoice.file
    ctx = JobContext(
        job_id=job.id,
        workspace_id=job.workspace_id,
        invoice_id=invoice.id,
        attempt=job.attempts,
        filename=file.filename,
        content_type=file.content_type,
        data=b"",
    )
    db.commit()  # release the snapshot; don't hold a transaction during the handler
    outcome: ExtractionOutcome | None = None
    error: JobError | None = None
    try:
        ctx.data = get_storage().get(file.storage_key)
        outcome = get_handler()(ctx)
    except StorageError as exc:
        error = PermanentJobError("file_missing", str(exc))
    except JobError as exc:
        error = exc
    except Exception as exc:  # noqa: BLE001 - unknown handler failures are retried
        logger.exception("Handler crashed for job %s", job.id)
        error = RetryableJobError("internal_error", f"{type(exc).__name__}: {exc}")

    locked = db.scalars(
        select(ExtractionJob)
        .where(ExtractionJob.id == job.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if locked.status != JobStatus.PROCESSING or locked.worker_id != worker_id:
        logger.warning("Discarding result for job %s: lease lost", job.id)
        db.rollback()
        return
    db.scalars(
        select(Invoice)
        .where(Invoice.id == locked.invoice_id)
        .with_for_update(of=Invoice)
        .execution_options(populate_existing=True)
    ).one()
    if locked.invoice.deleted_at is not None:
        # Deleted while processing: don't resurrect it or charge for it.
        locked.status = JobStatus.CANCELED
        locked.finished_at = utcnow()
        locked.lease_expires_at = None
        _log_attempt(locked, "canceled", "invoice_deleted")
    elif outcome is not None:
        complete(db, locked, outcome)
    else:
        fail(db, locked, error)
    db.commit()


def work_once(db: Session, worker_id: str) -> bool:
    """Claim and run one job. Returns False when the queue had nothing ready."""
    job = claim_next(db, worker_id)
    if job is None:
        return False
    run_job(db, job, worker_id)
    return True
