import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    WorkspaceContext,
    get_current_user,
    get_workspace_context,
    request_id,
)
from app.core.db import get_db
from app.core.errors import AuthenticationError, NotFoundError
from app.core.ratelimit import check_rate_limit
from app.core.security import hash_password, verify_password
from app.models import ActivityEvent, Membership, UserSession, WorkspaceRole
from app.models.base import utcnow
from app.schemas.common import Message, Page
from app.schemas.customer import (
    ActivityOut,
    ChangePasswordRequest,
    MeOut,
    SessionOut,
    UpdateProfileRequest,
    UpdateWorkspaceRequest,
    UserOut,
    WorkspaceMembershipOut,
    WorkspaceOut,
)
from app.services.audit import record_activity
from app.services.auth import revoke_user_sessions

router = APIRouter(tags=["account"])


@router.get("/me", response_model=MeOut)
def me(ctx: WorkspaceContext = Depends(get_workspace_context)) -> MeOut:
    return MeOut(
        user=UserOut.model_validate(ctx.user),
        workspace=WorkspaceOut.model_validate(ctx.workspace),
        role=ctx.membership.role,
    )


@router.patch("/me", response_model=UserOut)
def update_profile(
    body: UpdateProfileRequest,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(current.user, field, value)
    db.commit()
    return current.user


@router.post("/me/change-password", response_model=Message)
def change_password(
    body: ChangePasswordRequest,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Message:
    check_rate_limit(f"change-pw:{current.user.id}", limit=5, window_seconds=300)
    if not verify_password(current.user.password_hash, body.current_password):
        raise AuthenticationError("Your current password is incorrect.", code="invalid_credentials")
    current.user.password_hash = hash_password(body.new_password)
    revoke_user_sessions(db, current.user.id, except_id=current.session.id)
    db.commit()
    return Message(message="Password updated. Other sessions have been signed out.")


@router.get("/me/sessions", response_model=list[SessionOut])
def list_sessions(
    current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[SessionOut]:
    sessions = db.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == current.user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
        .order_by(UserSession.created_at.desc())
    ).all()
    return [
        SessionOut.model_validate(s).model_copy(update={"current": s.id == current.session.id})
        for s in sessions
    ]


@router.delete("/me/sessions/{session_id}", response_model=Message)
def revoke_session(
    session_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Message:
    session = db.scalar(
        select(UserSession).where(
            UserSession.id == session_id, UserSession.user_id == current.user.id
        )
    )
    if session is None:
        raise NotFoundError("Session not found.")
    session.revoked_at = utcnow()
    db.commit()
    return Message(message="Session signed out.")


@router.get("/workspaces", response_model=list[WorkspaceMembershipOut])
def list_workspaces(
    current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[WorkspaceMembershipOut]:
    memberships = db.scalars(
        select(Membership)
        .where(Membership.user_id == current.user.id)
        .order_by(Membership.created_at)
    ).all()
    return [
        WorkspaceMembershipOut(workspace=WorkspaceOut.model_validate(m.workspace), role=m.role)
        for m in memberships
    ]


@router.get("/workspace", response_model=WorkspaceOut)
def get_workspace(ctx: WorkspaceContext = Depends(get_workspace_context)):
    return ctx.workspace


@router.patch("/workspace", response_model=WorkspaceOut)
def update_workspace(
    body: UpdateWorkspaceRequest,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
):
    ctx.require_role(WorkspaceRole.OWNER, WorkspaceRole.ADMIN)
    changes = body.model_dump(exclude_unset=True)
    # Optional fields may be cleared with null; required ones may not.
    for required in ("name", "default_currency"):
        if changes.get(required, "") is None:
            del changes[required]
    for field, value in changes.items():
        setattr(ctx.workspace, field, value)
    record_activity(
        db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user.id,
        action="workspace.updated",
        entity_type="workspace",
        entity_id=ctx.workspace_id,
        summary={"fields": sorted(changes)},
        request_id=request_id(request),
    )
    db.commit()
    return ctx.workspace


@router.get("/workspace/activity", response_model=Page[ActivityOut])
def workspace_activity(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> Page[ActivityOut]:
    base = select(ActivityEvent).where(ActivityEvent.workspace_id == ctx.workspace_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = db.scalars(
        base.order_by(ActivityEvent.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[ActivityOut.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )
