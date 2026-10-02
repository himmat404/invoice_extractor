import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentAdmin, client_ip, request_id, require_permission
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.models import (
    CreditLedgerEntry,
    LedgerEntryType,
    Plan,
    SubscriptionSource,
    Workspace,
)
from app.schemas.billing import (
    AdminLedgerEntryOut,
    AdminSubscriptionOut,
    AdminWorkspaceOut,
    ChangePlanRequest,
    CreditAdjustRequest,
    OverridesRequest,
    UsageOut,
)
from app.schemas.common import Page
from app.services import credits
from app.services.audit import record_audit
from app.services.entitlements import effective_entitlements, validate_overrides
from app.services.subscriptions import ensure_current_period, start_subscription, usage_summary

router = APIRouter(prefix="/workspaces", tags=["admin-workspaces"])


def _load(db: Session, workspace_id: uuid.UUID) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError("Workspace not found.")
    return workspace


def _out(db: Session, workspace: Workspace) -> AdminWorkspaceOut:
    sub = ensure_current_period(db, workspace.id)
    db.commit()
    summary = usage_summary(db, workspace.id, sub)
    return AdminWorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        status=workspace.status.value,
        created_at=workspace.created_at,
        subscription=AdminSubscriptionOut.model_validate(sub),
        plan_name=sub.plan.name,
        entitlement_overrides=workspace.entitlement_overrides or {},
        effective_entitlements=effective_entitlements(sub.plan, workspace),
        usage=UsageOut(**vars(summary), total_credits_remaining=summary.total_remaining),
    )


def _audit(db, request, admin, action, workspace_id, summary) -> None:
    record_audit(
        db,
        action=action,
        actor_admin_id=admin.admin.id,
        entity_type="workspace",
        entity_id=workspace_id,
        workspace_id=workspace_id,
        summary=summary,
        request_id=request_id(request),
        ip_address=client_ip(request),
    )


@router.get("/{workspace_id}", response_model=AdminWorkspaceOut)
def get_workspace(
    workspace_id: uuid.UUID,
    _: CurrentAdmin = Depends(require_permission("users.read")),
    db: Session = Depends(get_db),
) -> AdminWorkspaceOut:
    return _out(db, _load(db, workspace_id))


@router.get("/{workspace_id}/credits/ledger", response_model=Page[AdminLedgerEntryOut])
def get_ledger(
    workspace_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: CurrentAdmin = Depends(require_permission("users.read")),
    db: Session = Depends(get_db),
) -> Page[AdminLedgerEntryOut]:
    _load(db, workspace_id)
    base = select(CreditLedgerEntry).where(CreditLedgerEntry.workspace_id == workspace_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(CreditLedgerEntry.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[AdminLedgerEntryOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{workspace_id}/credits/grant", response_model=AdminWorkspaceOut)
def grant_credits(
    workspace_id: uuid.UUID,
    body: CreditAdjustRequest,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("credits.write")),
    db: Session = Depends(get_db),
) -> AdminWorkspaceOut:
    workspace = _load(db, workspace_id)
    credits.add_extra_credits(
        db,
        workspace.id,
        body.amount,
        entry_type=LedgerEntryType.GRANT,
        description=f"Granted by InvoiceFlow: {body.reason}",
        actor_admin_id=admin.admin.id,
    )
    _audit(
        db,
        request,
        admin,
        "credits.granted",
        workspace.id,
        {"amount": body.amount, "bucket": "extra", "reason": body.reason},
    )
    db.commit()
    return _out(db, workspace)


@router.post("/{workspace_id}/credits/revoke", response_model=AdminWorkspaceOut)
def revoke_credits(
    workspace_id: uuid.UUID,
    body: CreditAdjustRequest,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("credits.write")),
    db: Session = Depends(get_db),
) -> AdminWorkspaceOut:
    workspace = _load(db, workspace_id)
    credits.revoke_credits(
        db,
        workspace.id,
        body.amount,
        body.bucket,
        description=f"Adjusted by InvoiceFlow: {body.reason}",
        actor_admin_id=admin.admin.id,
    )
    _audit(
        db,
        request,
        admin,
        "credits.revoked",
        workspace.id,
        {"amount": body.amount, "bucket": body.bucket.value, "reason": body.reason},
    )
    db.commit()
    return _out(db, workspace)


@router.post("/{workspace_id}/subscription", response_model=AdminWorkspaceOut)
def change_plan(
    workspace_id: uuid.UUID,
    body: ChangePlanRequest,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("billing.write")),
    db: Session = Depends(get_db),
) -> AdminWorkspaceOut:
    workspace = _load(db, workspace_id)
    plan = db.get(Plan, body.plan_id)
    if plan is None:
        raise NotFoundError("Plan not found.")
    previous = ensure_current_period(db, workspace.id)
    previous_plan = previous.plan.code
    sub = start_subscription(
        db, workspace.id, plan, interval=body.billing_interval, source=SubscriptionSource.ADMIN
    )
    _audit(
        db,
        request,
        admin,
        "subscription.plan_changed",
        workspace.id,
        {
            "from_plan": previous_plan,
            "to_plan": plan.code,
            "interval": body.billing_interval.value,
            "subscription_id": str(sub.id),
            "reason": body.reason,
        },
    )
    db.commit()
    return _out(db, workspace)


@router.put("/{workspace_id}/entitlement-overrides", response_model=AdminWorkspaceOut)
def set_overrides(
    workspace_id: uuid.UUID,
    body: OverridesRequest,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("plans.write")),
    db: Session = Depends(get_db),
) -> AdminWorkspaceOut:
    workspace = _load(db, workspace_id)
    before = dict(workspace.entitlement_overrides or {})
    workspace.entitlement_overrides = validate_overrides(body.overrides)
    _audit(
        db,
        request,
        admin,
        "workspace.entitlements_overridden",
        workspace.id,
        {"before": before, "after": workspace.entitlement_overrides, "reason": body.reason},
    )
    db.commit()
    return _out(db, workspace)
