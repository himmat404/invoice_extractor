from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_workspace_context
from app.core.db import get_db
from app.models import CreditLedgerEntry, CreditPackage, Plan, Subscription
from app.schemas.billing import (
    CreditPackageOut,
    CustomerLedgerEntryOut,
    PublicPlanOut,
    SubscriptionOut,
    UsageOut,
)
from app.schemas.common import Page
from app.services.entitlements import effective_entitlements
from app.services.subscriptions import ensure_current_period, usage_summary

public_router = APIRouter(tags=["pricing"])
router = APIRouter(tags=["billing"])


@public_router.get("/plans", response_model=list[PublicPlanOut])
def list_plans(db: Session = Depends(get_db)):
    """Active, public plans for the pricing page. No authentication required."""
    return db.scalars(
        select(Plan)
        .where(Plan.is_active.is_(True), Plan.is_public.is_(True))
        .order_by(Plan.sort_order, Plan.monthly_price)
    ).all()


@public_router.get("/credit-packages", response_model=list[CreditPackageOut])
def list_credit_packages(db: Session = Depends(get_db)):
    return db.scalars(
        select(CreditPackage)
        .where(CreditPackage.is_active.is_(True))
        .order_by(CreditPackage.sort_order, CreditPackage.credits)
    ).all()


def _subscription_out(sub: Subscription, ctx: WorkspaceContext) -> SubscriptionOut:
    renews = not sub.cancel_at_period_end and not sub.plan.is_free
    return SubscriptionOut(
        id=sub.id,
        status=sub.status,
        billing_interval=sub.billing_interval,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        next_billing_date=sub.current_period_end if renews else None,
        cancel_at_period_end=sub.cancel_at_period_end,
        price=sub.price,
        currency=sub.currency,
        plan=PublicPlanOut.model_validate(sub.plan),
        entitlements=effective_entitlements(sub.plan, ctx.workspace),
    )


@router.get("/billing/subscription", response_model=SubscriptionOut)
def get_subscription(
    ctx: WorkspaceContext = Depends(get_workspace_context), db: Session = Depends(get_db)
) -> SubscriptionOut:
    sub = ensure_current_period(db, ctx.workspace_id)
    db.commit()
    return _subscription_out(sub, ctx)


@router.get("/usage", response_model=UsageOut)
def get_usage(
    ctx: WorkspaceContext = Depends(get_workspace_context), db: Session = Depends(get_db)
) -> UsageOut:
    sub = ensure_current_period(db, ctx.workspace_id)
    db.commit()
    summary = usage_summary(db, ctx.workspace_id, sub)
    return UsageOut(**vars(summary), total_credits_remaining=summary.total_remaining)


@router.get("/credits/ledger", response_model=Page[CustomerLedgerEntryOut])
def get_ledger(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> Page[CustomerLedgerEntryOut]:
    base = select(CreditLedgerEntry).where(CreditLedgerEntry.workspace_id == ctx.workspace_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(CreditLedgerEntry.created_at.desc(), CreditLedgerEntry.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[CustomerLedgerEntryOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
