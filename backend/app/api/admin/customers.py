import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentAdmin, client_ip, request_id, require_permission
from app.core.db import get_db
from app.core.errors import AppError, NotFoundError
from app.models import AccountStatus, User
from app.schemas.admin import (
    CustomerDetailOut,
    CustomerSummaryOut,
    CustomerWorkspaceOut,
    StatusChangeRequest,
)
from app.schemas.common import Page
from app.services.audit import record_audit
from app.services.auth import revoke_user_sessions

router = APIRouter(prefix="/customers", tags=["admin-customers"])


def _detail(user: User) -> CustomerDetailOut:
    out = CustomerDetailOut.model_validate(user)
    out.workspaces = [
        CustomerWorkspaceOut(
            id=m.workspace.id, name=m.workspace.name, status=m.workspace.status, role=m.role
        )
        for m in user.memberships
    ]
    return out


@router.get("", response_model=Page[CustomerSummaryOut])
def list_customers(
    q: str | None = Query(None, max_length=200),
    status: AccountStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: CurrentAdmin = Depends(require_permission("users.read")),
    db: Session = Depends(get_db),
) -> Page[CustomerSummaryOut]:
    stmt = select(User)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.full_name.ilike(like)))
    if status:
        stmt = stmt.where(User.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    users = db.scalars(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(
        items=[CustomerSummaryOut.model_validate(u) for u in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{user_id}", response_model=CustomerDetailOut)
def get_customer(
    user_id: uuid.UUID,
    _: CurrentAdmin = Depends(require_permission("users.read")),
    db: Session = Depends(get_db),
) -> CustomerDetailOut:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("Customer not found.")
    return _detail(user)


def _change_status(
    db: Session,
    request: Request,
    admin: CurrentAdmin,
    user_id: uuid.UUID,
    new_status: AccountStatus,
    reason: str,
) -> CustomerDetailOut:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("Customer not found.")
    if user.status == AccountStatus.DELETED:
        raise AppError("Deleted accounts cannot be changed.", code="account_deleted")
    previous = user.status
    user.status = new_status
    if new_status == AccountStatus.SUSPENDED:
        revoke_user_sessions(db, user.id)
    verb = "suspended" if new_status == AccountStatus.SUSPENDED else "reactivated"
    record_audit(
        db,
        action=f"customer.{verb}",
        actor_admin_id=admin.admin.id,
        entity_type="user",
        entity_id=user.id,
        summary={"from": previous.value, "to": new_status.value, "reason": reason},
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    db.commit()
    return _detail(user)


@router.post("/{user_id}/suspend", response_model=CustomerDetailOut)
def suspend_customer(
    user_id: uuid.UUID,
    body: StatusChangeRequest,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("users.write")),
    db: Session = Depends(get_db),
) -> CustomerDetailOut:
    return _change_status(db, request, admin, user_id, AccountStatus.SUSPENDED, body.reason)


@router.post("/{user_id}/reactivate", response_model=CustomerDetailOut)
def reactivate_customer(
    user_id: uuid.UUID,
    body: StatusChangeRequest,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("users.write")),
    db: Session = Depends(get_db),
) -> CustomerDetailOut:
    return _change_status(db, request, admin, user_id, AccountStatus.ACTIVE, body.reason)
