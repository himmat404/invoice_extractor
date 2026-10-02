import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import CurrentAdmin, client_ip, request_id, require_permission
from app.core.db import get_db
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import CreditPackage, Plan
from app.schemas.billing import (
    AdminCreditPackageOut,
    AdminPlanOut,
    CreditPackageCreate,
    CreditPackageUpdate,
    PlanCreate,
    PlanUpdate,
)
from app.services.audit import record_audit
from app.services.entitlements import EXPORT_FORMATS, PlanEntitlements, validate_entitlements

router = APIRouter(tags=["admin-plans"])

read = require_permission("billing.read")
write = require_permission("plans.write")


def _jsonable(changes: dict[str, Any]) -> dict[str, Any]:
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in changes.items()}


def _audit(db, request, admin, action, entity_type, entity_id, summary) -> None:
    record_audit(
        db,
        action=action,
        actor_admin_id=admin.admin.id,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=_jsonable(summary),
        request_id=request_id(request),
        ip_address=client_ip(request),
    )


@router.get("/entitlements/schema")
def entitlement_schema(_: CurrentAdmin = Depends(read)) -> dict:
    """Entitlement keys, defaults and the export-format catalogue, for the plan editor."""
    return {"defaults": PlanEntitlements().model_dump(), "export_formats": EXPORT_FORMATS}


@router.get("/plans", response_model=list[AdminPlanOut])
def list_plans(_: CurrentAdmin = Depends(read), db: Session = Depends(get_db)):
    return db.scalars(select(Plan).order_by(Plan.sort_order, Plan.monthly_price)).all()


@router.get("/plans/{plan_id}", response_model=AdminPlanOut)
def get_plan(plan_id: uuid.UUID, _: CurrentAdmin = Depends(read), db: Session = Depends(get_db)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise NotFoundError("Plan not found.")
    return plan


def _make_default(db: Session, plan: Plan) -> None:
    if not plan.is_active:
        raise AppError("The default plan must be active.", code="default_plan_inactive")
    db.execute(update(Plan).where(Plan.id != plan.id).values(is_default=False))
    db.flush()


@router.post("/plans", response_model=AdminPlanOut, status_code=status.HTTP_201_CREATED)
def create_plan(
    body: PlanCreate,
    request: Request,
    admin: CurrentAdmin = Depends(write),
    db: Session = Depends(get_db),
):
    if db.scalar(select(Plan.id).where(Plan.code == body.code)):
        raise ConflictError("A plan with this code already exists.", code="plan_code_taken")
    data = body.model_dump()
    data["entitlements"] = validate_entitlements(body.entitlements)
    is_default = data.pop("is_default")
    plan = Plan(**data, is_default=False)
    db.add(plan)
    db.flush()
    if is_default:
        _make_default(db, plan)
        plan.is_default = True
    _audit(db, request, admin, "plan.created", "plan", plan.id, body.model_dump())
    db.commit()
    return plan


@router.patch("/plans/{plan_id}", response_model=AdminPlanOut)
def update_plan(
    plan_id: uuid.UUID,
    body: PlanUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(write),
    db: Session = Depends(get_db),
):
    """Edit a plan. Price changes apply to new subscriptions only; entitlement changes apply to
    current subscribers immediately; included-credit changes apply from the next period."""
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise NotFoundError("Plan not found.")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "entitlements" in changes:
        changes["entitlements"] = validate_entitlements(changes["entitlements"])
    if plan.is_default and (
        changes.get("is_active") is False or changes.get("is_default") is False
    ):
        raise AppError(
            "Choose another default plan before deactivating or unsetting this one.",
            code="default_plan_required",
        )
    before = {k: getattr(plan, k) for k in changes}
    make_default = changes.pop("is_default", None)
    for field, value in changes.items():
        setattr(plan, field, value)
    if make_default:
        _make_default(db, plan)
        plan.is_default = True
    if make_default is not None:
        changes["is_default"] = make_default
    _audit(
        db,
        request,
        admin,
        "plan.updated",
        "plan",
        plan.id,
        {"before": _jsonable(before), "after": _jsonable(changes)},
    )
    db.commit()
    db.refresh(plan)
    return plan


@router.get("/credit-packages", response_model=list[AdminCreditPackageOut])
def list_packages(_: CurrentAdmin = Depends(read), db: Session = Depends(get_db)):
    return db.scalars(select(CreditPackage).order_by(CreditPackage.sort_order)).all()


@router.post(
    "/credit-packages", response_model=AdminCreditPackageOut, status_code=status.HTTP_201_CREATED
)
def create_package(
    body: CreditPackageCreate,
    request: Request,
    admin: CurrentAdmin = Depends(write),
    db: Session = Depends(get_db),
):
    package = CreditPackage(**body.model_dump())
    db.add(package)
    db.flush()
    _audit(
        db,
        request,
        admin,
        "credit_package.created",
        "credit_package",
        package.id,
        body.model_dump(),
    )
    db.commit()
    return package


@router.patch("/credit-packages/{package_id}", response_model=AdminCreditPackageOut)
def update_package(
    package_id: uuid.UUID,
    body: CreditPackageUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(write),
    db: Session = Depends(get_db),
):
    package = db.get(CreditPackage, package_id)
    if package is None:
        raise NotFoundError("Credit package not found.")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    before = {k: getattr(package, k) for k in changes}
    for field, value in changes.items():
        setattr(package, field, value)
    _audit(
        db,
        request,
        admin,
        "credit_package.updated",
        "credit_package",
        package.id,
        {"before": _jsonable(before), "after": _jsonable(changes)},
    )
    db.commit()
    db.refresh(package)
    return package
