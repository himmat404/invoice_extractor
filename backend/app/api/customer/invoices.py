import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_workspace_context, request_id
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, NotFoundError, PermissionDeniedError
from app.core.ratelimit import check_rate_limit
from app.models import Batch, Invoice, InvoiceStatus, JobKind
from app.models.base import utcnow
from app.schemas.common import Message, Page
from app.schemas.invoices import BatchOut, BatchSummaryOut, DownloadOut, InvoiceOut
from app.services import jobs
from app.services.audit import record_activity
from app.services.credits import InsufficientCreditsError
from app.services.invoice_views import (
    batch_counts,
    batch_out,
    batch_summary,
    can_reprocess,
    can_retry,
    invoice_out,
)
from app.services.settings_store import get_setting
from app.services.storage import content_disposition, get_storage, verify_payload
from app.services.subscriptions import check_can_process
from app.services.uploads import IncomingFile, create_batch, read_limit_bytes

router = APIRouter(tags=["invoices"])
files_router = APIRouter(tags=["files"])


def _read_upload(upload: UploadFile, limit: int) -> IncomingFile:
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        return IncomingFile(upload.filename or "file", None, truncated=True)
    return IncomingFile(upload.filename or "file", data)


@router.post("/uploads", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def upload(
    request: Request,
    files: list[UploadFile] = File(..., description="PDF, JPG, PNG, HEIC or ZIP files"),
    allow_duplicates: bool = Form(False),
    name: str | None = Form(None, max_length=200),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> BatchOut:
    check_rate_limit(f"upload:{ctx.workspace_id}", limit=30, window_seconds=60)
    if get_setting(db, "uploads.require_verified_email", True) and not ctx.user.is_verified:
        raise PermissionDeniedError(
            "Please verify your email address before uploading invoices.",
            code="email_not_verified",
        )
    limit = read_limit_bytes()
    incoming = [_read_upload(f, limit) for f in files]
    batch = create_batch(
        db,
        workspace=ctx.workspace,
        user=ctx.user,
        incoming=incoming,
        allow_duplicates=allow_duplicates,
        name=name,
        request_id=request_id(request),
    )
    db.commit()
    return batch_out(db, batch)


# --- batches -----------------------------------------------------------------------------------


def _batch(db: Session, ctx: WorkspaceContext, batch_id: uuid.UUID) -> Batch:
    batch = db.scalar(
        select(Batch).where(Batch.id == batch_id, Batch.workspace_id == ctx.workspace_id)
    )
    if batch is None:
        raise NotFoundError("Batch not found.")
    return batch


@router.get("/batches", response_model=Page[BatchSummaryOut])
def list_batches(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> Page[BatchSummaryOut]:
    base = select(Batch).where(Batch.workspace_id == ctx.workspace_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    batches = db.scalars(base.order_by(Batch.created_at.desc()).limit(limit).offset(offset)).all()
    counts = batch_counts(db, [b.id for b in batches])
    return Page(
        items=[batch_summary(b, counts[b.id]) for b in batches],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/batches/{batch_id}", response_model=BatchOut)
def get_batch(
    batch_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> BatchOut:
    return batch_out(db, _batch(db, ctx, batch_id))


def _batch_invoices(db: Session, batch: Batch, statuses: tuple[InvoiceStatus, ...]):
    return db.scalars(
        select(Invoice)
        .where(
            Invoice.batch_id == batch.id,
            Invoice.workspace_id == batch.workspace_id,
            Invoice.status.in_(statuses),
            Invoice.deleted_at.is_(None),
        )
        .with_for_update(of=Invoice)
    ).all()


@router.post("/batches/{batch_id}/cancel", response_model=BatchOut)
def cancel_batch(
    batch_id: uuid.UUID,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> BatchOut:
    batch = _batch(db, ctx, batch_id)
    canceled = 0
    for invoice in _batch_invoices(db, batch, (InvoiceStatus.QUEUED,)):
        job = jobs.active_job(db, invoice.id)
        if job and jobs.cancel(db, job):
            canceled += 1
    batch.canceled_at = utcnow()
    record_activity(
        db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user.id,
        action="batch.canceled",
        entity_type="batch",
        entity_id=batch.id,
        summary={"canceled": canceled},
        request_id=request_id(request),
    )
    db.commit()
    return batch_out(db, batch)


def _ensure_credits_for_retry(db: Session, ctx: WorkspaceContext, invoices: list[Invoice]):
    needed = sum(1 for i in invoices if not i.credit_consumed)
    if not needed:
        return
    eligibility = check_can_process(db, ctx.workspace_id, count=0)
    available = eligibility.available_credits - jobs.pending_uncharged_jobs(db, ctx.workspace_id)
    if available < needed and not eligibility.entitlements.overage_allowed:
        raise InsufficientCreditsError(
            "You don't have enough invoice credits to retry. Add credits and try again.",
            details={"available": max(available, 0), "required": needed},
        )


def _requeue(db: Session, invoice: Invoice) -> None:
    kind = JobKind.REPROCESS if invoice.extraction_result is not None else JobKind.INITIAL
    jobs.enqueue(db, invoice, kind=kind)


@router.post("/batches/{batch_id}/retry-failed", response_model=BatchOut)
def retry_batch(
    batch_id: uuid.UUID,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> BatchOut:
    batch = _batch(db, ctx, batch_id)
    invoices = list(_batch_invoices(db, batch, (InvoiceStatus.FAILED, InvoiceStatus.CANCELED)))
    _ensure_credits_for_retry(db, ctx, invoices)
    for invoice in invoices:
        _requeue(db, invoice)
    batch.canceled_at = None
    record_activity(
        db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user.id,
        action="batch.retried",
        entity_type="batch",
        entity_id=batch.id,
        summary={"retried": len(invoices)},
        request_id=request_id(request),
    )
    db.commit()
    return batch_out(db, batch)


# --- invoices ----------------------------------------------------------------------------------


def _invoice(db: Session, ctx: WorkspaceContext, invoice_id: uuid.UUID, lock: bool = False):
    stmt = select(Invoice).where(
        Invoice.id == invoice_id,
        Invoice.workspace_id == ctx.workspace_id,
        Invoice.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update(of=Invoice)
    invoice = db.scalar(stmt)
    if invoice is None:
        raise NotFoundError("Invoice not found.")
    return invoice


@router.get("/invoices", response_model=Page[InvoiceOut])
def list_invoices(
    status_: InvoiceStatus | None = Query(None, alias="status"),
    batch_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> Page[InvoiceOut]:
    stmt = select(Invoice).where(
        Invoice.workspace_id == ctx.workspace_id, Invoice.deleted_at.is_(None)
    )
    if status_:
        stmt = stmt.where(Invoice.status == status_)
    if batch_id:
        stmt = stmt.where(Invoice.batch_id == batch_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Invoice.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[invoice_out(i) for i in rows], total=total, limit=limit, offset=offset)


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> InvoiceOut:
    return invoice_out(_invoice(db, ctx, invoice_id))


@router.get("/invoices/{invoice_id}/download", response_model=DownloadOut)
def download_invoice(
    invoice_id: uuid.UUID,
    inline: bool = False,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> DownloadOut:
    """Short-lived link to the original file (``inline=true`` for in-browser preview)."""
    invoice = _invoice(db, ctx, invoice_id)
    ttl = get_settings().signed_url_ttl_seconds
    url = get_storage().signed_url(
        invoice.file.storage_key,
        filename=invoice.file.filename,
        content_type=invoice.file.content_type,
        expires_in=ttl,
        inline=inline,
    )
    return DownloadOut(url=url, expires_in=ttl)


def _activity(db, ctx, request, action, invoice, **summary):
    record_activity(
        db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user.id,
        action=action,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=summary or None,
        request_id=request_id(request),
    )


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceOut)
def cancel_invoice(
    invoice_id: uuid.UUID,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> InvoiceOut:
    invoice = _invoice(db, ctx, invoice_id, lock=True)
    job = jobs.active_job(db, invoice.id)
    if job is None or not jobs.cancel(db, job):
        raise AppError("Only invoices waiting to process can be canceled.", code="not_cancelable")
    _activity(db, ctx, request, "invoice.canceled", invoice)
    db.commit()
    return invoice_out(invoice)


@router.post("/invoices/{invoice_id}/retry", response_model=InvoiceOut)
def retry_invoice(
    invoice_id: uuid.UUID,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> InvoiceOut:
    invoice = _invoice(db, ctx, invoice_id, lock=True)
    if not can_retry(invoice):
        raise AppError("Only failed or canceled invoices can be retried.", code="not_retryable")
    _ensure_credits_for_retry(db, ctx, [invoice])
    _requeue(db, invoice)
    _activity(db, ctx, request, "invoice.retried", invoice)
    db.commit()
    return invoice_out(invoice)


@router.post("/invoices/{invoice_id}/reprocess", response_model=InvoiceOut)
def reprocess_invoice(
    invoice_id: uuid.UUID,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> InvoiceOut:
    invoice = _invoice(db, ctx, invoice_id, lock=True)
    if not can_reprocess(invoice):
        raise AppError("This invoice can't be reprocessed right now.", code="not_reprocessable")
    if get_setting(db, "processing.reprocess_consumes_credit", False):
        check_can_process(db, ctx.workspace_id, count=1)
    jobs.enqueue(db, invoice, kind=JobKind.REPROCESS)
    _activity(db, ctx, request, "invoice.reprocess_requested", invoice)
    db.commit()
    return invoice_out(invoice)


@router.delete("/invoices/{invoice_id}", response_model=Message)
def delete_invoice(
    invoice_id: uuid.UUID,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> Message:
    """Soft delete. Files are purged later by the retention job (Phase 14)."""
    invoice = _invoice(db, ctx, invoice_id, lock=True)
    if job := jobs.active_job(db, invoice.id):
        jobs.cancel(db, job)  # a running job notices the deletion and discards its result
    now = utcnow()
    invoice.deleted_at = now
    invoice.status = InvoiceStatus.ARCHIVED
    invoice.file.deleted_at = now
    _activity(db, ctx, request, "invoice.deleted", invoice)
    db.commit()
    return Message(message="Invoice deleted.")


# --- signed file downloads (local storage driver) ----------------------------------------------


@files_router.get("/files/signed", include_in_schema=False)
def signed_download(token: str = Query(..., max_length=4000)) -> Response:
    payload = verify_payload(token)
    if payload is None:
        raise PermissionDeniedError(
            "This download link is invalid or has expired.", code="invalid_link"
        )
    from app.services.storage import StorageError

    try:
        data = get_storage().get(payload["k"])
    except StorageError as exc:
        raise NotFoundError("File not found.") from exc
    return Response(
        content=data,
        media_type=payload["t"],
        headers={
            "Content-Disposition": content_disposition(payload["f"], bool(payload.get("i"))),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
