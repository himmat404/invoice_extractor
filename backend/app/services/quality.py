"""Post-extraction quality pipeline: header sync, validation, confidence, duplicates and the
resulting invoice status (spec 4.5, 4.6, 10, 21.1, 21.2)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    DuplicateDecision,
    DuplicateStatus,
    Invoice,
    InvoiceStatus,
    ValidationResult,
    ValidationStatus,
)
from app.services import confidence, duplicates
from app.services.ai.schema import CanonicalInvoice
from app.services.validation import RULES_VERSION, config_from_settings, validate

_LOCKED = (InvoiceStatus.APPROVED, InvoiceStatus.EXPORTED, InvoiceStatus.ARCHIVED)


def current_data(invoice: Invoice) -> CanonicalInvoice | None:
    raw = invoice.reviewed_data if invoice.reviewed_data is not None else invoice.extraction_result
    if raw is None:
        return None
    return CanonicalInvoice.model_validate(raw)


def sync_header(invoice: Invoice, data: CanonicalInvoice) -> None:
    invoice.invoice_number = data.invoice_number
    invoice.norm_invoice_number = duplicates.normalize_number(data.invoice_number)
    invoice.invoice_date = data.invoice_date
    invoice.due_date = data.due_date
    invoice.supplier_name = data.supplier.name
    invoice.norm_supplier_name = duplicates.normalize_name(data.supplier.name)
    tax_id = (data.supplier.tax_id or "").replace(" ", "").upper()
    invoice.supplier_tax_id = tax_id or None
    invoice.customer_name = data.customer.name
    invoice.currency = data.currency
    invoice.grand_total = data.grand_total
    invoice.total_tax = data.total_tax


def _present_fields(data: CanonicalInvoice, paths: list[str]) -> set[str]:
    present = set()
    for path in paths:
        obj: Any = data
        for part in path.split("."):
            obj = getattr(obj, part, None)
        if obj not in (None, "", []):
            present.add(path)
    return present


def needs_duplicate_decision(db: Session, invoice: Invoice) -> bool:
    return bool(duplicates.pending_matches(db, invoice.id))


def derive_status(db: Session, invoice: Invoice) -> InvoiceStatus:
    pending = needs_duplicate_decision(db, invoice)
    summary = invoice.confidence_summary or {}
    if (
        invoice.validation_status in (ValidationStatus.NEEDS_REVIEW, ValidationStatus.FAILED)
        or summary.get("low_review_fields")
        or pending
    ):
        return InvoiceStatus.NEEDS_REVIEW
    if invoice.validation_status == ValidationStatus.WARNING or summary.get("low_fields"):
        return InvoiceStatus.VALIDATION_WARNING
    return InvoiceStatus.EXTRACTED


def evaluate(db: Session, invoice: Invoice, source: str) -> ValidationResult | None:
    """Recompute every quality signal from the invoice's current data."""
    data = current_data(invoice)
    if data is None:
        return None
    sync_header(invoice, data)
    report = validate(data, config_from_settings(db))
    result = ValidationResult(
        workspace_id=invoice.workspace_id,
        invoice_id=invoice.id,
        source=source,
        rules_version=RULES_VERSION,
        status=report.status,
        issues=report.issues_as_dicts(),
        computed=report.computed,
    )
    db.add(result)
    db.flush()
    invoice.validation_status = report.status.value
    invoice.validation_result_id = result.id

    thresholds = confidence.active_thresholds(db)
    edited = set(invoice.edited_fields or [])
    field_conf = {
        f: v
        for f, v in data.field_confidence.items()
        if f.split("[")[0].split(".")[0] not in edited and f not in edited
    }
    method = (invoice.confidence_summary or {}).get("method")
    if source == "extraction":
        from app.models import ExtractionJob

        job = (
            db.get(ExtractionJob, invoice.extraction_job_id) if invoice.extraction_job_id else None
        )
        method = job.confidence_method if job else method
    invoice.confidence_summary = confidence.summarize(
        field_conf,
        _present_fields(data, thresholds.review_fields),
        thresholds,
        method if field_conf else None,
    )

    invoice.duplicate_status = duplicates.detect(db, invoice).value
    if invoice.status not in _LOCKED:
        invoice.status = derive_status(db, invoice)
    return result


def latest_validation(db: Session, invoice: Invoice) -> ValidationResult | None:
    if invoice.validation_result_id is None:
        return None
    return db.get(ValidationResult, invoice.validation_result_id)


def is_marked_duplicate(db: Session, invoice_id: uuid.UUID) -> bool:
    from sqlalchemy import select

    from app.models import DuplicateMatchResult

    return bool(
        db.scalar(
            select(DuplicateMatchResult.id)
            .where(
                DuplicateMatchResult.invoice_id == invoice_id,
                DuplicateMatchResult.decision == DuplicateDecision.MARKED_DUPLICATE,
            )
            .limit(1)
        )
    )


__all__ = ["DuplicateStatus", "current_data", "derive_status", "evaluate", "sync_header"]
