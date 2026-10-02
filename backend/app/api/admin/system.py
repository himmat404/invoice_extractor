import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentAdmin, client_ip, request_id, require_permission
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.models import AuditLog, SystemSetting
from app.schemas.admin import AuditLogOut, SystemSettingOut, SystemSettingUpdate
from app.schemas.common import Page
from app.services.audit import record_audit

router = APIRouter(tags=["admin-system"])


@router.get("/audit-logs", response_model=Page[AuditLogOut])
def list_audit_logs(
    action: str | None = Query(None, max_length=100),
    actor_admin_id: uuid.UUID | None = None,
    entity_type: str | None = Query(None, max_length=64),
    entity_id: str | None = Query(None, max_length=64),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: CurrentAdmin = Depends(require_permission("audit.read")),
    db: Session = Depends(get_db),
) -> Page[AuditLogOut]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action))
    if actor_admin_id:
        stmt = stmt.where(AuditLog.actor_admin_id == actor_admin_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at < until)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(
        items=[AuditLogOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/settings", response_model=list[SystemSettingOut])
def list_settings(
    _: CurrentAdmin = Depends(require_permission("settings.read")),
    db: Session = Depends(get_db),
):
    return db.scalars(select(SystemSetting).order_by(SystemSetting.key)).all()


@router.get("/settings/{key}", response_model=SystemSettingOut)
def get_setting(
    key: str,
    _: CurrentAdmin = Depends(require_permission("settings.read")),
    db: Session = Depends(get_db),
):
    setting = db.get(SystemSetting, key)
    if setting is None:
        raise NotFoundError("Setting not found.")
    return setting


@router.put("/settings/{key}", response_model=SystemSettingOut)
def put_setting(
    body: SystemSettingUpdate,
    request: Request,
    key: str = Path(..., pattern=r"^[a-z][a-z0-9_.]{1,99}$"),
    admin: CurrentAdmin = Depends(require_permission("settings.write")),
    db: Session = Depends(get_db),
):
    setting = db.get(SystemSetting, key)
    before = setting.value if setting else None
    if setting is None:
        setting = SystemSetting(key=key, value=body.value)
        db.add(setting)
    else:
        setting.value = body.value
    if body.description is not None:
        setting.description = body.description
    setting.updated_by_admin_id = admin.admin.id
    record_audit(
        db,
        action="system_setting.updated",
        actor_admin_id=admin.admin.id,
        entity_type="system_setting",
        entity_id=key,
        summary={"before": before, "after": body.value},
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(setting)
    return setting
