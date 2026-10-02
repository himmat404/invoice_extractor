"""Subscription lifecycle, billing periods and processing eligibility (spec 5.6, 5.7, 14).

Renewal rules:
- ``system`` / ``admin`` subscriptions renew automatically at period end.
- ``gateway`` subscriptions are renewed by payment webhooks (Phase 8). When a period ends without
  renewal the subscription becomes ``past_due``; after the grace period it expires and the
  workspace falls back to the default plan.
- ``cancel_at_period_end`` subscriptions end at period end and fall back to the default plan.

Changing plan starts a fresh billing period: unused period credits expire and the new plan's
included credits are allocated. Extra (purchased/granted) credits are never affected.
"""

import calendar
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import (
    CURRENT_SUBSCRIPTION_STATUSES,
    BillingInterval,
    CreditLedgerEntry,
    LedgerEntryType,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    UsageRecord,
    Workspace,
)
from app.models.base import utcnow
from app.services import credits
from app.services.entitlements import PlanEntitlements, effective_entitlements
from app.services.settings_store import get_setting

DEFAULT_PLAN_CODE = "free"


class SubscriptionInactiveError(AppError):
    status_code = 402
    code = "subscription_inactive"


def add_interval(start: datetime, interval: BillingInterval, count: int = 1) -> datetime:
    months = count * (12 if interval == BillingInterval.YEAR else 1)
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def ensure_default_plan(db: Session) -> Plan:
    plan = db.scalar(select(Plan).where(Plan.is_default.is_(True)))
    if plan is None:
        plan = db.scalar(select(Plan).where(Plan.code == DEFAULT_PLAN_CODE))
    if plan is None:
        plan = Plan(
            code=DEFAULT_PLAN_CODE,
            name="Free",
            description="Try InvoiceFlow with a small monthly allowance.",
            included_credits=10,
            entitlements=PlanEntitlements().model_dump(),
            features=["10 invoices per month", "CSV, Excel and JSON exports"],
            is_default=True,
        )
        db.add(plan)
        db.flush()
    return plan


def current_subscription(db: Session, workspace_id: uuid.UUID) -> Subscription | None:
    return db.scalar(
        select(Subscription).where(
            Subscription.workspace_id == workspace_id,
            Subscription.status.in_(CURRENT_SUBSCRIPTION_STATUSES),
        )
    )


def _end(sub: Subscription, status: SubscriptionStatus, at: datetime) -> None:
    sub.status = status
    sub.ended_at = at
    if status == SubscriptionStatus.CANCELED and sub.canceled_at is None:
        sub.canceled_at = at


def start_subscription(
    db: Session,
    workspace_id: uuid.UUID,
    plan: Plan,
    *,
    interval: BillingInterval = BillingInterval.MONTH,
    source: SubscriptionSource = SubscriptionSource.SYSTEM,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    start: datetime | None = None,
) -> Subscription:
    """End the current subscription (if any) and start a new one with a fresh period."""
    if not plan.is_active:
        raise AppError("This plan is not available.", code="plan_inactive")
    now = start or utcnow()
    if existing := current_subscription(db, workspace_id):
        _end(existing, SubscriptionStatus.CANCELED, now)
        db.flush()
    sub = Subscription(
        workspace_id=workspace_id,
        plan_id=plan.id,
        status=status,
        billing_interval=interval,
        source=source,
        current_period_start=now,
        current_period_end=add_interval(now, interval),
        price=plan.annual_price if interval == BillingInterval.YEAR else plan.monthly_price,
        currency=plan.currency,
    )
    db.add(sub)
    db.flush()
    sub.plan = plan
    _allocate(db, sub, plan)
    return sub


def _allocate(db: Session, sub: Subscription, plan: Plan) -> None:
    credits.allocate_period_credits(
        db,
        sub.workspace_id,
        plan.included_credits,
        subscription_id=sub.id,
        idempotency_key=f"alloc:{sub.id}:{sub.current_period_start.isoformat()}",
        description=f"{plan.name} plan credits for billing period",
    )


def _roll_forward(db: Session, sub: Subscription, now: datetime) -> None:
    """Advance to the period containing ``now`` (skipping idle periods) and allocate once."""
    start = sub.current_period_end
    while add_interval(start, sub.billing_interval) <= now:
        start = add_interval(start, sub.billing_interval)
    sub.current_period_start = start
    sub.current_period_end = add_interval(start, sub.billing_interval)
    _allocate(db, sub, sub.plan)


def _needs_attention(sub: Subscription | None, now: datetime) -> bool:
    return sub is None or now >= sub.current_period_end


def ensure_current_period(db: Session, workspace_id: uuid.UUID) -> Subscription:
    """Return the workspace's current subscription, applying any due period transitions."""
    now = utcnow()
    sub = current_subscription(db, workspace_id)
    if not _needs_attention(sub, now):
        return sub
    # Serialise transitions per workspace.
    db.scalars(select(Workspace).where(Workspace.id == workspace_id).with_for_update()).one()
    sub = current_subscription(db, workspace_id)
    if sub is None:
        return start_subscription(db, workspace_id, ensure_default_plan(db))
    if now < sub.current_period_end:
        return sub

    grace = timedelta(days=int(get_setting(db, "billing.grace_period_days", 3)))
    if sub.cancel_at_period_end:
        _end(sub, SubscriptionStatus.CANCELED, sub.current_period_end)
    elif sub.source == SubscriptionSource.GATEWAY:
        if now < sub.current_period_end + grace:
            sub.status = SubscriptionStatus.PAST_DUE
            return sub
        _end(sub, SubscriptionStatus.EXPIRED, sub.current_period_end + grace)
    elif not sub.plan.is_active and not sub.plan.is_default:
        # Retired plans are not renewed.
        _end(sub, SubscriptionStatus.EXPIRED, sub.current_period_end)
    else:
        _roll_forward(db, sub, now)
        return sub
    db.flush()
    return start_subscription(db, workspace_id, ensure_default_plan(db))


@dataclass
class Eligibility:
    subscription: Subscription
    entitlements: PlanEntitlements
    available_credits: int


def check_can_process(db: Session, workspace_id: uuid.UUID, count: int = 1) -> Eligibility:
    """Raise unless the workspace may start processing ``count`` more invoices."""
    sub = ensure_current_period(db, workspace_id)
    workspace = db.get(Workspace, workspace_id)
    if sub.status not in (
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.TRIALING,
        SubscriptionStatus.PAST_DUE,
    ):
        raise SubscriptionInactiveError(
            "Your subscription is not active. Choose a plan to continue processing invoices."
        )
    entitlements = effective_entitlements(sub.plan, workspace)
    available = credits.get_account(db, workspace_id).total
    if available < count and not entitlements.overage_allowed:
        raise credits.InsufficientCreditsError(
            "You don't have enough invoice credits. Upgrade your plan or buy more credits.",
            details={"available": available, "required": count},
        )
    return Eligibility(subscription=sub, entitlements=entitlements, available_credits=available)


def record_usage(
    db: Session,
    workspace_id: uuid.UUID,
    metric: str,
    *,
    quantity: int = 1,
    subscription: Subscription | None = None,
    reference: credits.Reference | None = None,
    idempotency_key: str | None = None,
) -> UsageRecord | None:
    if idempotency_key and db.scalar(
        select(UsageRecord.id).where(UsageRecord.idempotency_key == idempotency_key)
    ):
        return None
    record = UsageRecord(
        workspace_id=workspace_id,
        subscription_id=subscription.id if subscription else None,
        metric=metric,
        quantity=quantity,
        period_start=subscription.current_period_start if subscription else None,
        period_end=subscription.current_period_end if subscription else None,
        reference_type=reference.type if reference else None,
        reference_id=reference.id if reference else None,
        idempotency_key=idempotency_key,
    )
    db.add(record)
    db.flush()
    return record


@dataclass
class UsageSummary:
    period_start: datetime
    period_end: datetime
    included_credits: int
    period_credits_remaining: int
    extra_credits_remaining: int
    credits_consumed: int
    overage_consumed: int
    invoices_processed: int

    @property
    def total_remaining(self) -> int:
        return self.period_credits_remaining + self.extra_credits_remaining


def usage_summary(db: Session, workspace_id: uuid.UUID, sub: Subscription) -> UsageSummary:
    account = credits.get_account(db, workspace_id)
    in_period = (
        CreditLedgerEntry.workspace_id == workspace_id,
        CreditLedgerEntry.created_at >= sub.current_period_start,
        CreditLedgerEntry.created_at < sub.current_period_end,
    )
    rows = db.execute(
        select(
            CreditLedgerEntry.entry_type,
            CreditLedgerEntry.bucket,
            func.coalesce(func.sum(CreditLedgerEntry.amount), 0),
        )
        .where(
            *in_period,
            CreditLedgerEntry.entry_type.in_(
                [LedgerEntryType.CONSUMPTION, LedgerEntryType.REVERSAL]
            ),
        )
        .group_by(CreditLedgerEntry.entry_type, CreditLedgerEntry.bucket)
    ).all()
    consumed = -sum(int(total) for _, bucket, total in rows if bucket != "overage")
    overage = -sum(int(total) for _, bucket, total in rows if bucket == "overage")
    processed = db.scalar(
        select(func.coalesce(func.sum(UsageRecord.quantity), 0)).where(
            UsageRecord.workspace_id == workspace_id,
            UsageRecord.metric == "invoice_processed",
            UsageRecord.created_at >= sub.current_period_start,
            UsageRecord.created_at < sub.current_period_end,
        )
    )
    return UsageSummary(
        period_start=sub.current_period_start,
        period_end=sub.current_period_end,
        included_credits=sub.plan.included_credits,
        period_credits_remaining=account.period_balance,
        extra_credits_remaining=account.extra_balance,
        credits_consumed=consumed,
        overage_consumed=overage,
        invoices_processed=int(processed or 0),
    )
