import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentAdmin, client_ip, request_id, require_permission
from app.core.db import get_db
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.security import hash_password
from app.models import AccountStatus, AdminRole, AdminSession, AdminUser
from app.models.base import utcnow
from app.schemas.admin import (
    AdminRoleCreate,
    AdminRoleOut,
    AdminRoleUpdate,
    AdminUserCreate,
    AdminUserOut,
    AdminUserUpdate,
    PermissionOut,
)
from app.services.audit import record_audit
from app.services.auth import normalize_email
from app.services.permissions import ALL, PERMISSIONS

router = APIRouter(tags=["admin-staff"])

manage = require_permission("admins.manage")


def _validate_permissions(perms: list[str]) -> list[str]:
    unknown = sorted(p for p in set(perms) if p != ALL and p not in PERMISSIONS)
    if unknown:
        raise AppError("Unknown permissions.", code="unknown_permission", details=unknown)
    return sorted(set(perms))


@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(_: CurrentAdmin = Depends(manage)) -> list[PermissionOut]:
    return [PermissionOut(name=k, description=v) for k, v in PERMISSIONS.items()]


@router.get("/roles", response_model=list[AdminRoleOut])
def list_roles(_: CurrentAdmin = Depends(manage), db: Session = Depends(get_db)):
    return db.scalars(select(AdminRole).order_by(AdminRole.name)).all()


@router.post("/roles", response_model=AdminRoleOut, status_code=status.HTTP_201_CREATED)
def create_role(
    body: AdminRoleCreate,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    if db.scalar(select(AdminRole.id).where(AdminRole.name == body.name)):
        raise ConflictError("A role with this name already exists.")
    role = AdminRole(
        name=body.name,
        description=body.description,
        permissions=_validate_permissions(body.permissions),
    )
    db.add(role)
    db.flush()
    record_audit(
        db,
        action="admin_role.created",
        actor_admin_id=admin.admin.id,
        entity_type="admin_role",
        entity_id=role.id,
        summary={"name": role.name, "permissions": role.permissions},
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    db.commit()
    return role


@router.patch("/roles/{role_id}", response_model=AdminRoleOut)
def update_role(
    role_id: uuid.UUID,
    body: AdminRoleUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    role = db.get(AdminRole, role_id)
    if role is None:
        raise NotFoundError("Role not found.")
    if role.id == admin.admin.role_id and body.permissions is not None:
        raise AppError("You cannot change the permissions of your own role.", code="self_change")
    before = list(role.permissions)
    if body.description is not None:
        role.description = body.description
    if body.permissions is not None:
        role.permissions = _validate_permissions(body.permissions)
    record_audit(
        db,
        action="admin_role.updated",
        actor_admin_id=admin.admin.id,
        entity_type="admin_role",
        entity_id=role.id,
        summary={"permissions_before": before, "permissions_after": list(role.permissions)},
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    db.commit()
    return role


@router.get("/admins", response_model=list[AdminUserOut])
def list_admins(_: CurrentAdmin = Depends(manage), db: Session = Depends(get_db)):
    return db.scalars(select(AdminUser).order_by(AdminUser.created_at)).all()


@router.post("/admins", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_admin(
    body: AdminUserCreate,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    email = normalize_email(body.email)
    if db.scalar(select(AdminUser.id).where(AdminUser.email == email)):
        raise ConflictError("An admin with this email already exists.")
    if db.get(AdminRole, body.role_id) is None:
        raise NotFoundError("Role not found.")
    new_admin = AdminUser(
        email=email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role_id=body.role_id,
    )
    db.add(new_admin)
    db.flush()
    record_audit(
        db,
        action="admin_user.created",
        actor_admin_id=admin.admin.id,
        entity_type="admin_user",
        entity_id=new_admin.id,
        summary={"email": email, "role_id": str(body.role_id)},
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(new_admin)
    return new_admin


@router.patch("/admins/{admin_id}", response_model=AdminUserOut)
def update_admin(
    admin_id: uuid.UUID,
    body: AdminUserUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    target = db.get(AdminUser, admin_id)
    if target is None:
        raise NotFoundError("Admin not found.")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if target.id == admin.admin.id and ({"role_id", "status"} & changes.keys()):
        raise AppError("You cannot change your own role or status.", code="self_change")
    if "role_id" in changes and db.get(AdminRole, changes["role_id"]) is None:
        raise NotFoundError("Role not found.")
    for field, value in changes.items():
        setattr(target, field, value)
    if changes.get("status") and changes["status"] != AccountStatus.ACTIVE:
        for s in db.scalars(
            select(AdminSession).where(
                AdminSession.admin_user_id == target.id, AdminSession.revoked_at.is_(None)
            )
        ):
            s.revoked_at = utcnow()
    record_audit(
        db,
        action="admin_user.updated",
        actor_admin_id=admin.admin.id,
        entity_type="admin_user",
        entity_id=target.id,
        summary={k: str(v) for k, v in changes.items()},
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(target)
    return target
