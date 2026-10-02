import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from app.models.identity import _enum

Money = Numeric(12, 2)


class SubscriptionStatus(enum.StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


#: Statuses that represent the workspace's single current subscription.
CURRENT_SUBSCRIPTION_STATUSES = (
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
)


class BillingInterval(enum.StrEnum):
    MONTH = "month"
    YEAR = "year"


class SubscriptionSource(enum.StrEnum):
    SYSTEM = "system"  # default plan assigned automatically
    ADMIN = "admin"  # assigned by an administrator
    GATEWAY = "gateway"  # paid through a payment gateway (renewed by webhooks)


class CreditBucket(enum.StrEnum):
    PERIOD = "period"  # included plan credits; expire at the end of the billing period
    EXTRA = "extra"  # purchased or granted credits; do not expire with the period
    OVERAGE = "overage"  # consumption beyond balances when overage is enabled


class LedgerEntryType(enum.StrEnum):
    ALLOCATION = "allocation"
    EXPIRY = "expiry"
    PURCHASE = "purchase"
    GRANT = "grant"
    REVOKE = "revoke"
    ADJUSTMENT = "adjustment"
    CONSUMPTION = "consumption"
    REVERSAL = "reversal"


class Plan(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    monthly_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    annual_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    included_credits: Mapped[int] = mapped_column(Integer, default=0)
    entitlements: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    features: Mapped[list[str]] = mapped_column(ARRAY(String(200)), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        CheckConstraint("monthly_price >= 0 AND annual_price >= 0", name="non_negative_price"),
        CheckConstraint("included_credits >= 0", name="non_negative_credits"),
        # At most one default plan.
        Index(
            "uq_plans_single_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    @property
    def is_free(self) -> bool:
        return self.monthly_price == 0 and self.annual_price == 0


class Subscription(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        # A workspace has at most one current subscription.
        Index(
            "uq_subscriptions_current_per_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("status IN ('trialing', 'active', 'past_due')"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="RESTRICT")
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        _enum(SubscriptionStatus, "subscription_status")
    )
    billing_interval: Mapped[BillingInterval] = mapped_column(
        _enum(BillingInterval, "billing_interval"), default=BillingInterval.MONTH
    )
    source: Mapped[SubscriptionSource] = mapped_column(
        _enum(SubscriptionSource, "subscription_source"), default=SubscriptionSource.SYSTEM
    )
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Price captured at subscription time so later plan price edits don't rewrite history.
    price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    plan: Mapped[Plan] = relationship(lazy="joined")


class CreditPackage(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "credit_packages"
    __table_args__ = (
        CheckConstraint("credits > 0", name="positive_credits"),
        CheckConstraint("price >= 0", name="non_negative_price"),
    )

    name: Mapped[str] = mapped_column(String(100))
    credits: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class CreditAccount(Base):
    """Per-workspace running balances. Row-locked for every change; the ledger is the history."""

    __tablename__ = "credit_accounts"
    __table_args__ = (
        CheckConstraint("period_balance >= 0 AND extra_balance >= 0", name="non_negative"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    period_balance: Mapped[int] = mapped_column(Integer, default=0)
    extra_balance: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    @property
    def total(self) -> int:
        return self.period_balance + self.extra_balance


class CreditLedgerEntry(UUIDPrimaryKey, Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (Index("ix_credit_ledger_ws_created", "workspace_id", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    entry_type: Mapped[LedgerEntryType] = mapped_column(_enum(LedgerEntryType, "ledger_entry_type"))
    bucket: Mapped[CreditBucket] = mapped_column(_enum(CreditBucket, "credit_bucket"))
    amount: Mapped[int] = mapped_column(Integer)  # signed: + adds credits, - removes them
    balance_after: Mapped[int] = mapped_column(Integer)  # total (period + extra) after entry
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="SET NULL")
    )
    reference_type: Mapped[str | None] = mapped_column(String(64))
    reference_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    actor_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(String(300))


class UsageRecord(UUIDPrimaryKey, Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        Index("ix_usage_records_ws_metric_created", "workspace_id", "metric", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="SET NULL")
    )
    metric: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference_type: Mapped[str | None] = mapped_column(String(64))
    reference_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
