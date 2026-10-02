"""Validation results, confidence thresholds and duplicate detection (spec 4.6, 21.1, 21.2)."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.models.identity import _enum


class ValidationStatus(enum.StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class DuplicateStatus(enum.StrEnum):
    NO_MATCH = "no_match"
    POTENTIAL = "potential_duplicate"
    CONFIRMED = "confirmed_duplicate"
    REVIEW_REQUIRED = "review_required"


class DuplicateRuleType(enum.StrEnum):
    EXACT_FILE = "exact_file"
    INVOICE_NUMBER_SUPPLIER = "invoice_number_supplier"
    MULTI_FIELD = "multi_field"
    SIMILARITY = "similarity"


class DuplicateDecision(enum.StrEnum):
    PENDING = "pending"
    KEPT_BOTH = "kept_both"
    MARKED_DUPLICATE = "marked_duplicate"
    CANCELED = "canceled"


class ValidationResult(UUIDPrimaryKey, Base):
    __tablename__ = "validation_results"
    __table_args__ = (Index("ix_validation_results_invoice", "invoice_id", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String(16))  # extraction | review
    rules_version: Mapped[str] = mapped_column(String(16))
    status: Mapped[ValidationStatus] = mapped_column(_enum(ValidationStatus, "validation_status"))
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    computed: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ConfidenceThresholdVersion(UUIDPrimaryKey, Base):
    """Versioned review-priority bands. Bands are labels, not accuracy guarantees."""

    __tablename__ = "confidence_threshold_versions"
    __table_args__ = (
        Index(
            "uq_confidence_thresholds_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, unique=True)
    high_min: Mapped[float] = mapped_column(Float)
    medium_min: Mapped[float] = mapped_column(Float)
    review_fields: Mapped[list[str]] = mapped_column(ARRAY(String(100)))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(String(300))
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL")
    )


class DuplicateDetectionRule(UUIDPrimaryKey, Timestamps, Base):
    """Global rule (``workspace_id`` null) or a workspace's own additional rule."""

    __tablename__ = "duplicate_detection_rules"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    rule_type: Mapped[DuplicateRuleType] = mapped_column(
        _enum(DuplicateRuleType, "duplicate_rule_type")
    )
    fields: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    threshold: Mapped[float | None] = mapped_column(Float)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)  # else "potential"
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)


class DuplicateMatchResult(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "duplicate_match_results"
    __table_args__ = (Index("ix_duplicate_matches_invoice", "invoice_id"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE")
    )
    matched_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE")
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("duplicate_detection_rules.id", ondelete="SET NULL")
    )
    rule_name: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[DuplicateStatus] = mapped_column(_enum(DuplicateStatus, "duplicate_status"))
    score: Mapped[float | None] = mapped_column(Float)
    matched_fields: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    decision: Mapped[DuplicateDecision] = mapped_column(
        _enum(DuplicateDecision, "duplicate_decision"), default=DuplicateDecision.PENDING
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
