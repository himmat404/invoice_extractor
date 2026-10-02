"""Shared read helpers turning invoices and batches into API views."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    INVOICE_STATUS_LABELS,
    Batch,
    BatchItem,
    Invoice,
    InvoiceStatus,
)
from app.schemas.invoices import BatchCounts, BatchItemOut, BatchOut, BatchSummaryOut, InvoiceOut

_RESULT_STATUSES = {
    InvoiceStatus.EXTRACTED,
    InvoiceStatus.VALIDATION_WARNING,
    InvoiceStatus.NEEDS_REVIEW,
}
_COMPLETED = {InvoiceStatus.EXTRACTED, InvoiceStatus.APPROVED, InvoiceStatus.EXPORTED}
_REVIEW = {InvoiceStatus.VALIDATION_WARNING, InvoiceStatus.NEEDS_REVIEW}


def can_cancel(inv: Invoice) -> bool:
    return inv.status == InvoiceStatus.QUEUED and inv.deleted_at is None


def can_retry(inv: Invoice) -> bool:
    return inv.status in (InvoiceStatus.FAILED, InvoiceStatus.CANCELED) and inv.deleted_at is None


def can_reprocess(inv: Invoice) -> bool:
    return (
        inv.deleted_at is None
        and inv.extraction_result is not None
        and inv.status in _RESULT_STATUSES
    )


_HEADER = (
    "invoice_number",
    "invoice_date",
    "supplier_name",
    "customer_name",
    "currency",
    "grand_total",
    "validation_status",
    "duplicate_status",
    "approved_at",
)


def invoice_out(inv: Invoice) -> InvoiceOut:
    return InvoiceOut(
        **{f: getattr(inv, f) for f in _HEADER},
        id=inv.id,
        status=inv.status,
        status_label=INVOICE_STATUS_LABELS[inv.status],
        batch_id=inv.batch_id,
        filename=inv.file.filename,
        content_type=inv.file.content_type,
        size_bytes=inv.file.size_bytes,
        page_count=inv.file.page_count,
        error_code=inv.error_code,
        error_message=inv.error_message,
        extraction_result=inv.extraction_result,
        created_at=inv.created_at,
        processed_at=inv.processed_at,
        can_cancel=can_cancel(inv),
        can_retry=can_retry(inv),
        can_reprocess=can_reprocess(inv),
    )


def batch_counts(db: Session, batch_ids: list[uuid.UUID]) -> dict[uuid.UUID, BatchCounts]:
    counts = {bid: BatchCounts() for bid in batch_ids}
    if not batch_ids:
        return counts
    for bid, status, n in db.execute(
        select(BatchItem.batch_id, BatchItem.status, func.count())
        .where(BatchItem.batch_id.in_(batch_ids))
        .group_by(BatchItem.batch_id, BatchItem.status)
    ):
        c = counts[bid]
        setattr(c, status.value, n)
        c.total += n
    for bid, status, n in db.execute(
        select(BatchItem.batch_id, Invoice.status, func.count())
        .join(BatchItem, BatchItem.invoice_id == Invoice.id)
        .where(BatchItem.batch_id.in_(batch_ids))
        .group_by(BatchItem.batch_id, Invoice.status)
    ):
        c = counts[bid]
        if status in (InvoiceStatus.QUEUED, InvoiceStatus.UPLOADED):
            c.queued += n
        elif status == InvoiceStatus.PROCESSING:
            c.processing += n
        elif status in _COMPLETED:
            c.completed += n
        elif status in _REVIEW:
            c.review_required += n
        elif status == InvoiceStatus.FAILED:
            c.failed += n
        elif status == InvoiceStatus.CANCELED:
            c.canceled += n
    return counts


def batch_status(batch: Batch, c: BatchCounts) -> str:
    if c.queued or c.processing:
        return "processing"
    if batch.canceled_at is not None and c.canceled:
        return "canceled"
    if c.failed or c.rejected:
        return "completed_with_errors"
    return "completed"


def batch_summary(batch: Batch, counts: BatchCounts) -> BatchSummaryOut:
    return BatchSummaryOut(
        id=batch.id,
        name=batch.name,
        source=batch.source,
        status=batch_status(batch, counts),
        created_at=batch.created_at,
        canceled_at=batch.canceled_at,
        counts=counts,
    )


def batch_out(db: Session, batch: Batch) -> BatchOut:
    counts = batch_counts(db, [batch.id])[batch.id]
    items = db.scalars(
        select(BatchItem).where(BatchItem.batch_id == batch.id).order_by(BatchItem.position)
    ).all()
    summary = batch_summary(batch, counts)
    return BatchOut(
        **summary.model_dump(),
        items=[
            BatchItemOut(
                id=i.id,
                position=i.position,
                filename=i.filename,
                archive_name=i.archive_name,
                status=i.status,
                rejection_code=i.rejection_code,
                rejection_message=i.rejection_message,
                invoice_id=i.invoice_id,
                invoice_status=i.invoice.status if i.invoice else None,
                invoice_status_label=INVOICE_STATUS_LABELS[i.invoice.status] if i.invoice else None,
                duplicate_of_invoice_id=i.duplicate_of_invoice_id,
            )
            for i in items
        ],
    )


_EDITABLE = {
    InvoiceStatus.EXTRACTED,
    InvoiceStatus.VALIDATION_WARNING,
    InvoiceStatus.NEEDS_REVIEW,
    InvoiceStatus.APPROVED,
}


def can_edit(inv: Invoice) -> bool:
    return inv.deleted_at is None and inv.extraction_result is not None and inv.status in _EDITABLE


def invoice_detail(db: Session, inv: Invoice):
    from app.models import DuplicateDecision, DuplicateMatchResult
    from app.schemas.invoices import (
        DuplicateMatchOut,
        InvoiceDetailOut,
        MatchedInvoiceOut,
        ValidationOut,
    )
    from app.services import quality

    validation = quality.latest_validation(db, inv)
    data = quality.current_data(inv)
    matches = db.scalars(
        select(DuplicateMatchResult)
        .where(DuplicateMatchResult.invoice_id == inv.id)
        .order_by(DuplicateMatchResult.created_at)
    ).all()
    dupes = []
    for m in matches:
        other = db.get(Invoice, m.matched_invoice_id)
        if other is None or other.workspace_id != inv.workspace_id:
            continue
        dupes.append(
            DuplicateMatchOut(
                id=m.id,
                rule_name=m.rule_name,
                outcome=m.outcome.value,
                score=m.score,
                matched_fields=m.matched_fields,
                decision=m.decision.value,
                matched_invoice=MatchedInvoiceOut(
                    id=other.id,
                    invoice_number=other.invoice_number,
                    supplier_name=other.supplier_name,
                    invoice_date=other.invoice_date,
                    grand_total=other.grand_total,
                    currency=other.currency,
                    status=other.status.value,
                    filename=other.file.filename,
                ),
            )
        )
    marked = any(m.decision == DuplicateDecision.MARKED_DUPLICATE for m in matches)
    base = invoice_out(inv)
    return InvoiceDetailOut(
        **base.model_dump(),
        data=data.model_dump(mode="json") if data else None,
        edited_fields=list(inv.edited_fields or []),
        reviewed_at=inv.reviewed_at,
        validation=ValidationOut(
            status=validation.status.value,
            rules_version=validation.rules_version,
            issues=validation.issues,
            computed=validation.computed,
        )
        if validation
        else None,
        confidence=inv.confidence_summary,
        duplicates=dupes,
        can_edit=can_edit(inv) and inv.status != InvoiceStatus.EXPORTED,
        can_approve=(
            inv.status
            in (
                InvoiceStatus.EXTRACTED,
                InvoiceStatus.VALIDATION_WARNING,
                InvoiceStatus.NEEDS_REVIEW,
            )
            and not marked
            and inv.validation_status != "failed"
            and inv.deleted_at is None
        ),
    )
