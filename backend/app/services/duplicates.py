"""Workspace-scoped duplicate detection with configurable rules (spec 21.1).

Matching never looks outside the invoice's own workspace. Results are advisory unless a matching
rule is ``blocking``, in which case the invoice is held for the customer's decision.
"""

import re
import uuid
from difflib import SequenceMatcher

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    DuplicateDecision,
    DuplicateDetectionRule,
    DuplicateMatchResult,
    DuplicateRuleType,
    DuplicateStatus,
    Invoice,
    InvoiceStatus,
)

MATCH_FIELDS = (
    "supplier",
    "invoice_number",
    "invoice_date",
    "grand_total",
    "currency",
    "supplier_tax_id",
)
_EXCLUDED = (
    InvoiceStatus.UPLOADED,
    InvoiceStatus.QUEUED,
    InvoiceStatus.PROCESSING,
    InvoiceStatus.FAILED,
    InvoiceStatus.CANCELED,
    InvoiceStatus.ARCHIVED,
)
_SEVERITY = {
    DuplicateStatus.POTENTIAL: 1,
    DuplicateStatus.CONFIRMED: 2,
    DuplicateStatus.REVIEW_REQUIRED: 3,
}

DEFAULT_RULES = [
    dict(name="Same file", rule_type=DuplicateRuleType.EXACT_FILE, confirmed=True, priority=10),
    dict(
        name="Same supplier and invoice number",
        rule_type=DuplicateRuleType.INVOICE_NUMBER_SUPPLIER,
        confirmed=True,
        priority=20,
    ),
    dict(
        name="Same supplier, date and total",
        rule_type=DuplicateRuleType.MULTI_FIELD,
        fields=["supplier", "invoice_date", "grand_total", "currency"],
        priority=30,
    ),
    dict(
        name="Similar invoice number, same supplier and total",
        rule_type=DuplicateRuleType.SIMILARITY,
        threshold=0.85,
        priority=40,
    ),
]


def normalize_number(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", value.upper())
    return cleaned or None


_SUFFIXES = re.compile(
    r"\b(PVT|PRIVATE|LTD|LIMITED|LLP|LLC|INC|CORP|CORPORATION|CO|COMPANY|GMBH|PLC)\b"
)


def normalize_name(value: str | None) -> str | None:
    if not value:
        return None
    upper = re.sub(r"[^A-Z0-9 ]", " ", value.upper())
    cleaned = " ".join(_SUFFIXES.sub(" ", upper).split())
    return cleaned or None


def ensure_default_rules(db: Session) -> None:
    if db.scalar(
        select(DuplicateDetectionRule.id)
        .where(DuplicateDetectionRule.workspace_id.is_(None))
        .limit(1)
    ):
        return
    for spec in DEFAULT_RULES:
        db.add(DuplicateDetectionRule(workspace_id=None, **spec))
    db.flush()


def active_rules(
    db: Session, workspace_id: uuid.UUID, include_workspace: bool = True
) -> list[DuplicateDetectionRule]:
    ensure_default_rules(db)
    scope = DuplicateDetectionRule.workspace_id.is_(None)
    if include_workspace:
        scope = or_(scope, DuplicateDetectionRule.workspace_id == workspace_id)
    return list(
        db.scalars(
            select(DuplicateDetectionRule)
            .where(DuplicateDetectionRule.is_active.is_(True), scope)
            .order_by(DuplicateDetectionRule.priority, DuplicateDetectionRule.created_at)
        )
    )


def exact_file_rule_active(db: Session, workspace_id: uuid.UUID) -> bool:
    return any(r.rule_type == DuplicateRuleType.EXACT_FILE for r in active_rules(db, workspace_id))


def _same_supplier(a: Invoice, b: Invoice) -> bool:
    if a.supplier_tax_id and b.supplier_tax_id:
        return a.supplier_tax_id == b.supplier_tax_id
    return bool(a.norm_supplier_name) and a.norm_supplier_name == b.norm_supplier_name


def _field_equal(name: str, a: Invoice, b: Invoice) -> bool:
    if name == "supplier":
        return _same_supplier(a, b)
    attr = {"invoice_number": "norm_invoice_number"}.get(name, name)
    va, vb = getattr(a, attr), getattr(b, attr)
    return va is not None and va == vb


def _candidates(db: Session, invoice: Invoice, rule: DuplicateDetectionRule) -> list[Invoice]:
    base = select(Invoice).where(
        Invoice.workspace_id == invoice.workspace_id,  # never cross-tenant
        Invoice.id != invoice.id,
        Invoice.deleted_at.is_(None),
        Invoice.status.not_in(_EXCLUDED),
    )
    if rule.rule_type == DuplicateRuleType.INVOICE_NUMBER_SUPPLIER:
        if not invoice.norm_invoice_number:
            return []
        base = base.where(Invoice.norm_invoice_number == invoice.norm_invoice_number)
    elif rule.rule_type == DuplicateRuleType.MULTI_FIELD:
        fields = [f for f in rule.fields if f in MATCH_FIELDS]
        if not fields:
            return []
        for f in fields:
            if f == "supplier":
                continue
            attr = {"invoice_number": "norm_invoice_number"}.get(f, f)
            value = getattr(invoice, attr)
            if value is None:
                return []
            base = base.where(getattr(Invoice, attr) == value)
    elif rule.rule_type == DuplicateRuleType.SIMILARITY:
        if invoice.grand_total is None or not invoice.norm_invoice_number:
            return []
        base = base.where(Invoice.grand_total == invoice.grand_total)
    else:
        return []
    return list(db.scalars(base.order_by(Invoice.created_at).limit(200)).unique())


def _evaluate(
    rule: DuplicateDetectionRule, invoice: Invoice, other: Invoice
) -> tuple[bool, float | None, list[str]]:
    if rule.rule_type == DuplicateRuleType.INVOICE_NUMBER_SUPPLIER:
        ok = _same_supplier(invoice, other)
        return ok, None, ["invoice_number", "supplier"] if ok else []
    if rule.rule_type == DuplicateRuleType.MULTI_FIELD:
        fields = [f for f in rule.fields if f in MATCH_FIELDS]
        ok = all(_field_equal(f, invoice, other) for f in fields)
        return ok, None, fields if ok else []
    if rule.rule_type == DuplicateRuleType.SIMILARITY:
        if not other.norm_invoice_number or not _same_supplier(invoice, other):
            return False, None, []
        score = SequenceMatcher(
            None, invoice.norm_invoice_number, other.norm_invoice_number
        ).ratio()
        ok = score >= (rule.threshold or 0.85)
        return ok, round(score, 3), ["invoice_number~", "supplier", "grand_total"] if ok else []
    return False, None, []


def detect(db: Session, invoice: Invoice) -> DuplicateStatus:
    """Re-run duplicate rules for an invoice; keeps decisions already made on the same match."""
    previous = {
        m.matched_invoice_id: m
        for m in db.scalars(
            select(DuplicateMatchResult).where(DuplicateMatchResult.invoice_id == invoice.id)
        )
    }
    best: dict[uuid.UUID, tuple[DuplicateDetectionRule, float | None, list[str], DuplicateStatus]]
    best = {}
    for rule in active_rules(db, invoice.workspace_id):
        if rule.rule_type == DuplicateRuleType.EXACT_FILE:
            continue  # applied at upload time
        for other in _candidates(db, invoice, rule):
            ok, score, fields = _evaluate(rule, invoice, other)
            if not ok:
                continue
            outcome = (
                DuplicateStatus.REVIEW_REQUIRED
                if rule.blocking
                else DuplicateStatus.CONFIRMED
                if rule.confirmed
                else DuplicateStatus.POTENTIAL
            )
            current = best.get(other.id)
            if current is None or _SEVERITY[outcome] > _SEVERITY[current[3]]:
                best[other.id] = (rule, score, fields, outcome)

    for matched_id, match in previous.items():
        if matched_id not in best and match.decision == DuplicateDecision.PENDING:
            db.delete(match)
    for matched_id, (rule, score, fields, outcome) in best.items():
        match = previous.get(matched_id)
        if match is None:
            match = DuplicateMatchResult(
                workspace_id=invoice.workspace_id,
                invoice_id=invoice.id,
                matched_invoice_id=matched_id,
            )
            db.add(match)
        match.rule_id, match.rule_name = rule.id, rule.name
        match.outcome, match.score, match.matched_fields = outcome, score, fields
    db.flush()

    if not best:
        return DuplicateStatus.NO_MATCH
    return max((m[3] for m in best.values()), key=_SEVERITY.__getitem__)


def pending_matches(db: Session, invoice_id: uuid.UUID) -> list[DuplicateMatchResult]:
    return list(
        db.scalars(
            select(DuplicateMatchResult).where(
                DuplicateMatchResult.invoice_id == invoice_id,
                DuplicateMatchResult.decision == DuplicateDecision.PENDING,
            )
        )
    )
