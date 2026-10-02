from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.cookies import clear_session_cookie, set_session_cookie
from app.api.deps import (
    CurrentUser,
    client_ip,
    enforce_csrf,
    get_current_user,
    request_id,
)
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, AuthenticationError, ConflictError, PermissionDeniedError
from app.core.ratelimit import check_rate_limit
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.models import AccountStatus, Membership, TokenPurpose, User, Workspace, WorkspaceRole
from app.models.base import utcnow
from app.schemas.common import Message
from app.schemas.customer import (
    EmailRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenRequest,
    UserOut,
)
from app.services.audit import record_activity
from app.services.auth import (
    consume_user_token,
    create_user_session,
    issue_user_token,
    normalize_email,
    resolve_user_session,
    revoke_user_sessions,
)
from app.services.email import send_password_reset_email, send_verification_email

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(enforce_csrf)])


def _start_session(response: Response, request: Request, db: Session, user: User) -> None:
    s = get_settings()
    token = create_user_session(db, user.id, client_ip(request), request.headers.get("user-agent"))
    set_session_cookie(response, s.session_cookie_name, token, s.session_ttl_hours * 3600, "/")


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> User:
    check_rate_limit(f"register:{client_ip(request)}", limit=5, window_seconds=60)
    email = normalize_email(body.email)
    if db.scalar(select(User.id).where(User.email == email)):
        raise ConflictError(
            "An account with this email already exists. Try signing in.", code="email_taken"
        )
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name)
    workspace = Workspace(name=body.workspace_name or f"{body.full_name}'s workspace")
    db.add_all([user, workspace])
    db.flush()
    db.add(Membership(user_id=user.id, workspace_id=workspace.id, role=WorkspaceRole.OWNER))
    record_activity(
        db,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        action="account.registered",
        entity_type="user",
        entity_id=user.id,
        request_id=request_id(request),
    )
    token = issue_user_token(db, user.id, TokenPurpose.EMAIL_VERIFICATION)
    user.last_login_at = utcnow()
    _start_session(response, request, db, user)
    db.commit()
    send_verification_email(user.email, token)
    return user


@router.post("/login", response_model=UserOut)
def login(
    body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> User:
    email = normalize_email(body.email)
    check_rate_limit(f"login:ip:{client_ip(request)}", limit=20, window_seconds=60)
    check_rate_limit(f"login:email:{email}", limit=10, window_seconds=300)
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(DUMMY_PASSWORD_HASH, body.password)
        raise AuthenticationError("Incorrect email or password.", code="invalid_credentials")
    if not verify_password(user.password_hash, body.password):
        raise AuthenticationError("Incorrect email or password.", code="invalid_credentials")
    if user.status != AccountStatus.ACTIVE:
        raise PermissionDeniedError(
            "This account is not active. Please contact support.", code="account_inactive"
        )
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = utcnow()
    _start_session(response, request, db, user)
    db.commit()
    return user


@router.post("/logout", response_model=Message)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Message:
    s = get_settings()
    token = request.cookies.get(s.session_cookie_name)
    if token and (session := resolve_user_session(db, token)):
        session.revoked_at = utcnow()
        db.commit()
    clear_session_cookie(response, s.session_cookie_name, "/")
    return Message(message="Signed out.")


@router.post("/verify-email", response_model=Message)
def verify_email(body: TokenRequest, request: Request, db: Session = Depends(get_db)) -> Message:
    check_rate_limit(f"verify:{client_ip(request)}", limit=20, window_seconds=60)
    record = consume_user_token(db, body.token, TokenPurpose.EMAIL_VERIFICATION)
    if record is None:
        raise AppError(
            "This verification link is invalid or has expired. Request a new one.",
            code="invalid_token",
        )
    user = db.get(User, record.user_id)
    if user and user.email_verified_at is None:
        user.email_verified_at = utcnow()
    db.commit()
    return Message(message="Your email address is verified.")


@router.post("/resend-verification", response_model=Message)
def resend_verification(
    current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> Message:
    user = current.user
    check_rate_limit(f"resend:{user.id}", limit=3, window_seconds=600)
    if user.is_verified:
        return Message(message="Your email address is already verified.")
    token = issue_user_token(db, user.id, TokenPurpose.EMAIL_VERIFICATION)
    db.commit()
    send_verification_email(user.email, token)
    return Message(message="Verification email sent.")


@router.post("/forgot-password", response_model=Message, status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: EmailRequest, request: Request, db: Session = Depends(get_db)) -> Message:
    email = normalize_email(body.email)
    check_rate_limit(f"forgot:ip:{client_ip(request)}", limit=5, window_seconds=60)
    check_rate_limit(f"forgot:email:{email}", limit=3, window_seconds=900)
    user = db.scalar(select(User).where(User.email == email))
    if user is not None and user.status == AccountStatus.ACTIVE:
        token = issue_user_token(db, user.id, TokenPurpose.PASSWORD_RESET)
        db.commit()
        send_password_reset_email(user.email, token)
    # Same response whether or not the account exists, to avoid account enumeration.
    return Message(message="If an account exists for this email, we've sent a reset link.")


@router.post("/reset-password", response_model=Message)
def reset_password(
    body: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)
) -> Message:
    check_rate_limit(f"reset:{client_ip(request)}", limit=10, window_seconds=60)
    record = consume_user_token(db, body.token, TokenPurpose.PASSWORD_RESET)
    user = db.get(User, record.user_id) if record else None
    if user is None or user.status != AccountStatus.ACTIVE:
        raise AppError(
            "This reset link is invalid or has expired. Request a new one.", code="invalid_token"
        )
    user.password_hash = hash_password(body.password)
    # Reaching the inbox proves ownership of the email address.
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    revoke_user_sessions(db, user.id)
    db.commit()
    return Message(message="Your password has been reset. Please sign in.")
