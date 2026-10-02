import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.models import (
    AccountStatus,
    AdminSession,
    AdminUser,
    Membership,
    User,
    UserSession,
    Workspace,
    WorkspaceRole,
)
from app.models.base import utcnow
from app.services.auth import resolve_admin_session, resolve_user_session
from app.services.permissions import has_permission

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_TOUCH_INTERVAL = timedelta(minutes=5)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def enforce_csrf(request: Request) -> None:
    """Cookie-authenticated unsafe requests must carry a custom header.

    Browsers cannot attach custom headers to cross-site requests without a CORS preflight, which
    our CORS policy only allows for the configured app origins.
    """
    if request.method in _SAFE_METHODS:
        return
    if not request.headers.get(get_settings().csrf_header_name):
        raise PermissionDeniedError("Missing CSRF header.", code="csrf_failed")


# --- customer ----------------------------------------------------------------------------------


@dataclass
class CurrentUser:
    user: User
    session: UserSession


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise AuthenticationError("Please sign in to continue.")
    session = resolve_user_session(db, token)
    if session is None:
        raise AuthenticationError("Your session has expired. Please sign in again.")
    user = session.user
    if user.status != AccountStatus.ACTIVE:
        raise PermissionDeniedError(
            "This account is not active. Please contact support.", code="account_inactive"
        )
    enforce_csrf(request)
    now = utcnow()
    if session.last_seen_at is None or now - session.last_seen_at > _TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return CurrentUser(user=user, session=session)


@dataclass
class WorkspaceContext:
    user: User
    workspace: Workspace
    membership: Membership

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.workspace.id

    def require_role(self, *roles: WorkspaceRole) -> None:
        if self.membership.role not in roles:
            raise PermissionDeniedError("You don't have permission to do this in this workspace.")


def get_workspace_context(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceContext:
    """Resolve the active workspace. Every tenant-scoped query must use ``ctx.workspace_id``."""
    stmt = (
        select(Membership)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(Membership.user_id == current.user.id)
    )
    requested = request.headers.get("X-Workspace-Id")
    if requested:
        try:
            stmt = stmt.where(Membership.workspace_id == uuid.UUID(requested))
        except ValueError as exc:
            raise PermissionDeniedError("Workspace not available.") from exc
    else:
        stmt = stmt.order_by(Membership.created_at)
    membership = db.scalars(stmt.limit(1)).first()
    if membership is None:
        raise PermissionDeniedError("Workspace not available.", code="workspace_unavailable")
    if membership.workspace.status != AccountStatus.ACTIVE:
        raise PermissionDeniedError(
            "This workspace is not active. Please contact support.", code="workspace_inactive"
        )
    return WorkspaceContext(
        user=current.user, workspace=membership.workspace, membership=membership
    )


# --- admin -------------------------------------------------------------------------------------


@dataclass
class CurrentAdmin:
    admin: AdminUser
    session: AdminSession

    @property
    def permissions(self) -> list[str]:
        return list(self.admin.role.permissions or [])


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> CurrentAdmin:
    token = request.cookies.get(get_settings().admin_session_cookie_name)
    if not token:
        raise AuthenticationError("Admin sign-in required.")
    session = resolve_admin_session(db, token)
    if session is None:
        raise AuthenticationError("Your admin session has expired. Please sign in again.")
    admin = session.admin_user
    if admin.status != AccountStatus.ACTIVE:
        raise PermissionDeniedError("This admin account is not active.", code="account_inactive")
    enforce_csrf(request)
    now = utcnow()
    if session.last_seen_at is None or now - session.last_seen_at > _TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return CurrentAdmin(admin=admin, session=session)


def require_permission(permission: str):
    def dependency(current: CurrentAdmin = Depends(get_current_admin)) -> CurrentAdmin:
        if not has_permission(current.permissions, permission):
            raise PermissionDeniedError("You don't have permission to perform this action.")
        return current

    return dependency
