import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_workspace_context, request_id
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.ratelimit import check_rate_limit
from app.models import (
    Batch,
    DuplicateDecision,
    DuplicateMatchResult,
    Invoice,
    InvoiceFile,
    InvoiceStatus,
    JobKind,
)
from app.models.base import utcnow
from app.schemas.common import Message, Page
from app.schemas.invoices import (
    ApproveRequest,
    BatchOut,
    BatchSummaryOut,
    DownloadOut,
    DuplicateDecisionRequest,
    InvoiceDataIn,
    InvoiceDetailOut,
    InvoiceOut,
)
from app.services import credits, duplicates, jobs, quality
from app.services.ai.schema import CanonicalInvoice
from app.services.audit import record_activity
from app.services.credits import InsufficientCreditsError
from app.services.invoice_views import (
    batch_counts,
    batch_out,
    batch_summary,
    can_edit,
    can_reprocess,
    can_retry,
    invoice_detail,
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


_SORTS = {
    "uploaded_desc": Invoice.created_at.desc(),
    "uploaded_asc": Invoice.created_at.asc(),
    "date_desc": Invoice.invoice_date.desc().nulls_last(),
    "date_asc": Invoice.invoice_date.asc().nulls_last(),
    "amount_desc": Invoice.grand_total.desc().nulls_last(),
    "amount_asc": Invoice.grand_total.asc().nulls_last(),
}


@router.get("/invoices", response_model=Page[InvoiceOut])
def list_invoices(
    status_: list[InvoiceStatus] | None = Query(None, alias="status"),
    batch_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=200, description="Invoice number, supplier, customer"),
    supplier: str | None = Query(None, max_length=300),
    currency: str | None = Query(None, pattern=r"^[A-Z]{3}$"),
    date_from: date | None = None,
    date_to: date | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    validation_status: str | None = Query(None, max_length=16),
    duplicate_status: str | None = Query(None, max_length=24),
    sort: str = Query("uploaded_desc", pattern="^(" + "|".join(_SORTS) + ")$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> Page[InvoiceOut]:
    stmt = select(Invoice).where(
        Invoice.workspace_id == ctx.workspace_id, Invoice.deleted_at.is_(None)
    )
    if status_:
        stmt = stmt.where(Invoice.status.in_(status_))
    if batch_id:
        stmt = stmt.where(Invoice.batch_id == batch_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Invoice.invoice_number.ilike(like),
                Invoice.supplier_name.ilike(like),
                Invoice.customer_name.ilike(like),
                InvoiceFile.filename.ilike(like),
            )
        )
    if supplier:
        stmt = stmt.where(Invoice.supplier_name.ilike(f"%{supplier.strip()}%"))
    if currency:
        stmt = stmt.where(Invoice.currency == currency)
    if date_from:
        stmt = stmt.where(Invoice.invoice_date >= date_from)
    if date_to:
        stmt = stmt.where(Invoice.invoice_date <= date_to)
    if min_amount is not None:
        stmt = stmt.where(Invoice.grand_total >= min_amount)
    if max_amount is not None:
        stmt = stmt.where(Invoice.grand_total <= max_amount)
    if validation_status:
        stmt = stmt.where(Invoice.validation_status == validation_status)
    if duplicate_status:
        stmt = stmt.where(Invoice.duplicate_status == duplicate_status)
    if q:
        stmt = stmt.join(InvoiceFile, InvoiceFile.id == Invoice.file_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.scalars(stmt.order_by(_SORTS[sort], Invoice.id).limit(limit).offset(offset))
        .unique()
        .all()
    )
    return Page(items=[invoice_out(i) for i in rows], total=total, limit=limit, offset=offset)


@router.get("/invoices/review-queue", response_model=Page[InvoiceOut])
def review_queue(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> Page[InvoiceOut]:
    """Invoices needing attention, most urgent first; marked duplicates are excluded."""
    marked = select(DuplicateMatchResult.invoice_id).where(
        DuplicateMatchResult.workspace_id == ctx.workspace_id,
        DuplicateMatchResult.decision == DuplicateDecision.MARKED_DUPLICATE,
    )
    stmt = select(Invoice).where(
        Invoice.workspace_id == ctx.workspace_id,
        Invoice.deleted_at.is_(None),
        Invoice.status.in_((InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.VALIDATION_WARNING)),
        Invoice.id.not_in(marked),
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    urgency = case((Invoice.status == InvoiceStatus.NEEDS_REVIEW, 0), else_=1)
    rows = (
        db.scalars(stmt.order_by(urgency, Invoice.created_at).limit(limit).offset(offset))
        .unique()
        .all()
    )
    return Page(items=[invoice_out(i) for i in rows], total=total, limit=limit, offset=offset)


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetailOut)
def get_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> InvoiceDetailOut:
    return invoice_detail(db, _invoice(db, ctx, invoice_id))


def _changed_fields(before: dict | None, after: dict) -> list[str]:
    before = before or {}
    skip = {"field_confidence", "parse_warnings"}
    return sorted(k for k in after if k not in skip and before.get(k) != after.get(k))


@router.put("/invoices/{invoice_id}/data", response_model=InvoiceDetailOut)
def save_review(
    invoice_id: uuid.UUID,
    body: InvoiceDataIn,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> InvoiceDetailOut:
    """Save the customer's corrections and re-run validation. The original extraction is kept;
    editing an approved invoice reopens it for approval."""
    invoice = _invoice(db, ctx, invoice_id, lock=True)
    if not can_edit(invoice) or invoice.status == InvoiceStatus.EXPORTED:
        raise AppError("This invoice can't be edited right now.", code="not_editable")
    previous = quality.current_data(invoice)
    previous_dump = previous.model_dump(mode="json") if previous else None
    new = CanonicalInvoice(
        **body.model_dump(),
        field_confidence=previous.field_confidence if previous else {},
    ).model_dump(mode="json")
    changed = _changed_fields(previous_dump, new)
    if not changed:
        return invoice_detail(db, invoice)
    invoice.reviewed_data = new
    invoice.edited_fields = sorted(set(invoice.edited_fields or []) | set(changed))
    invoice.reviewed_at = utcnow()
    reopened = invoice.status == InvoiceStatus.APPROVED
    if reopened:
        invoice.status = InvoiceStatus.EXTRACTED
        invoice.approved_at = invoice.approved_by_user_id = None
    quality.evaluate(db, invoice, "review")
    _activity(db, ctx, request, "invoice.edited", invoice, fields=changed, reopened=reopened)
    db.commit()
    return invoice_detail(db, invoice)


@router.post("/invoices/{invoice_id}/approve", response_model=InvoiceDetailOut)
def approve_invoice(
    invoice_id: uuid.UUID,
    body: ApproveRequest,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> InvoiceDetailOut:
    invoice = _invoice(db, ctx, invoice_id, lock=True)
    detail = invoice_detail(db, invoice)
    if invoice.status == InvoiceStatus.APPROVED:
        return detail
    if not detail.can_approve:
        if quality.is_marked_duplicate(db, invoice.id):
            raise AppError("This invoice is marked as a duplicate.", code="marked_duplicate")
        if invoice.validation_status == "failed":
            raise AppError(
                "Fix the problems on this invoice before approving it.", code="validation_failed"
            )
        raise AppError("This invoice can't be approved right now.", code="not_approvable")
    blocking = [
        d for d in detail.duplicates if d.decision == "pending" and d.outcome == "review_required"
    ]
    if blocking:
        raise AppError(
            "Decide what to do with the possible duplicate first.",
            code="duplicate_decision_required",
        )
    open_issues = [
        i
        for i in (detail.validation.issues if detail.validation else [])
        if i.severity in ("error", "warning")
    ]
    low = (detail.confidence or {}).get("low_fields") or []
    pending = [d for d in detail.duplicates if d.decision == "pending"]
    if (open_issues or low or pending) and not body.acknowledge_issues:
        raise ConflictError(
            "This invoice has open issues. Review them, then approve again to confirm.",
            code="issues_need_acknowledgement",
            details={
                "issues": len(open_issues),
                "low_confidence_fields": low,
                "pending_duplicates": len(pending),
            },
        )
    invoice.status = InvoiceStatus.APPROVED
    invoice.approved_at = utcnow()
    invoice.approved_by_user_id = ctx.user.id
    _activity(
        db,
        ctx,
        request,
        "invoice.approved",
        invoice,
        acknowledged_issues=len(open_issues),
        low_confidence_fields=len(low),
    )
    db.commit()
    return invoice_detail(db, invoice)


@router.post("/invoices/{invoice_id}/duplicates/decision", response_model=InvoiceDetailOut)
def decide_duplicate(
    invoice_id: uuid.UUID,
    body: DuplicateDecisionRequest,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> InvoiceDetailOut:
    """Resolve possible duplicates: keep both, mark this one as a duplicate, or cancel it."""
    invoice = _invoice(db, ctx, invoice_id, lock=True)
    pending = duplicates.pending_matches(db, invoice.id)
    if not pending:
        raise AppError(
            "There are no duplicate matches waiting for a decision.", code="no_pending_duplicates"
        )
    decision = {
        "keep_both": DuplicateDecision.KEPT_BOTH,
        "mark_duplicate": DuplicateDecision.MARKED_DUPLICATE,
        "cancel": DuplicateDecision.CANCELED,
    }[body.decision]
    now = utcnow()
    for match in pending:
        match.decision = decision
        match.decided_by_user_id = ctx.user.id
        match.decided_at = now
    severity = {"potential_duplicate": 1, "confirmed_duplicate": 2, "review_required": 3}
    strongest = max(pending, key=lambda m: severity[m.outcome.value])
    refunded = False
    if decision == DuplicateDecision.MARKED_DUPLICATE:
        invoice.duplicate_of_invoice_id = strongest.matched_invoice_id
        invoice.duplicate_status = "confirmed_duplicate"
    elif decision == DuplicateDecision.CANCELED:
        invoice.duplicate_of_invoice_id = strongest.matched_invoice_id
        invoice.deleted_at = now
        invoice.status = InvoiceStatus.ARCHIVED
        invoice.file.deleted_at = now
        if invoice.credit_consumed and get_setting(db, "duplicates.refund_on_cancel", True):
            refunded = credits.reverse_consumption(db, ctx.workspace_id, f"invoice:{invoice.id}")
            invoice.credit_consumed = not refunded
    if invoice.status not in (
        InvoiceStatus.APPROVED,
        InvoiceStatus.EXPORTED,
        InvoiceStatus.ARCHIVED,
    ):
        db.flush()
        invoice.status = quality.derive_status(db, invoice)
    _activity(
        db,
        ctx,
        request,
        "invoice.duplicate_decision",
        invoice,
        decision=body.decision,
        matched_invoice_id=str(strongest.matched_invoice_id),
        credit_refunded=refunded,
    )
    db.commit()
    return invoice_detail(db, invoice)


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
