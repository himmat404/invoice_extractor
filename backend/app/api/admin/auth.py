from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.cookies import clear_session_cookie, set_session_cookie
from app.api.deps import CurrentAdmin, client_ip, enforce_csrf, get_current_admin, request_id
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AuthenticationError
from app.core.ratelimit import check_rate_limit
from app.core.security import DUMMY_PASSWORD_HASH, verify_password
from app.models import AccountStatus, AdminUser
from app.models.base import utcnow
from app.schemas.admin import AdminLoginRequest, AdminMeOut, AdminUserOut
from app.schemas.common import Message
from app.services.audit import record_audit
from app.services.auth import create_admin_session, normalize_email, resolve_admin_session

router = APIRouter(prefix="/auth", tags=["admin-auth"], dependencies=[Depends(enforce_csrf)])

ADMIN_COOKIE_PATH = "/api/admin"


@router.post("/login", response_model=AdminMeOut)
def admin_login(
    body: AdminLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> AdminMeOut:
    s = get_settings()
    email = normalize_email(body.email)
    check_rate_limit(f"admin-login:ip:{client_ip(request)}", limit=10, window_seconds=60)
    check_rate_limit(f"admin-login:email:{email}", limit=5, window_seconds=300)
    admin = db.scalar(select(AdminUser).where(AdminUser.email == email))
    valid = (
        verify_password(admin.password_hash, body.password)
        if admin
        else verify_password(DUMMY_PASSWORD_HASH, body.password)
    )
    if admin is None or not valid or admin.status != AccountStatus.ACTIVE:
        record_audit(
            db,
            action="admin.login",
            actor_admin_id=admin.id if admin else None,
            outcome="failure",
            summary={"email": email},
            request_id=request_id(request),
            ip_address=client_ip(request),
        )
        db.commit()
        raise AuthenticationError("Incorrect email or password.", code="invalid_credentials")
    admin.last_login_at = utcnow()
    token = create_admin_session(
        db, admin.id, client_ip(request), request.headers.get("user-agent")
    )
    record_audit(
        db,
        action="admin.login",
        actor_admin_id=admin.id,
        request_id=request_id(request),
        ip_address=client_ip(request),
    )
    db.commit()
    set_session_cookie(
        response,
        s.admin_session_cookie_name,
        token,
        s.admin_session_ttl_hours * 3600,
        ADMIN_COOKIE_PATH,
    )
    return AdminMeOut(
        admin=AdminUserOut.model_validate(admin), permissions=list(admin.role.permissions)
    )


@router.post("/logout", response_model=Message)
def admin_logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Message:
    s = get_settings()
    token = request.cookies.get(s.admin_session_cookie_name)
    if token and (session := resolve_admin_session(db, token)):
        session.revoked_at = utcnow()
        db.commit()
    clear_session_cookie(response, s.admin_session_cookie_name, ADMIN_COOKIE_PATH)
    return Message(message="Signed out.")


@router.get("/me", response_model=AdminMeOut)
def admin_me(current: CurrentAdmin = Depends(get_current_admin)) -> AdminMeOut:
    return AdminMeOut(
        admin=AdminUserOut.model_validate(current.admin), permissions=current.permissions
    )
