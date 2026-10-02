"""Customer and admin authentication: sessions and single-use tokens."""

import uuid
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import generate_token, hash_token
from app.models import AdminSession, TokenPurpose, UserSession, UserToken
from app.models.base import utcnow


def normalize_email(email: str) -> str:
    return email.strip().lower()


# --- customer sessions -------------------------------------------------------------------------


def create_user_session(
    db: Session, user_id: uuid.UUID, ip: str | None, user_agent: str | None
) -> str:
    token = generate_token()
    db.add(
        UserSession(
            user_id=user_id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours),
            ip_address=ip,
            user_agent=(user_agent or "")[:512] or None,
            last_seen_at=utcnow(),
        )
    )
    return token


def resolve_user_session(db: Session, token: str) -> UserSession | None:
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow():
        return None
    return session


def revoke_user_sessions(
    db: Session, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None
) -> None:
    stmt = update(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if except_id is not None:
        stmt = stmt.where(UserSession.id != except_id)
    db.execute(stmt.values(revoked_at=utcnow()))


# --- admin sessions ----------------------------------------------------------------------------


def create_admin_session(
    db: Session, admin_user_id: uuid.UUID, ip: str | None, user_agent: str | None
) -> str:
    token = generate_token()
    db.add(
        AdminSession(
            admin_user_id=admin_user_id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(hours=get_settings().admin_session_ttl_hours),
            ip_address=ip,
            user_agent=(user_agent or "")[:512] or None,
            last_seen_at=utcnow(),
        )
    )
    return token


def resolve_admin_session(db: Session, token: str) -> AdminSession | None:
    session = db.scalar(select(AdminSession).where(AdminSession.token_hash == hash_token(token)))
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow():
        return None
    return session


# --- single-use tokens -------------------------------------------------------------------------


def issue_user_token(db: Session, user_id: uuid.UUID, purpose: TokenPurpose) -> str:
    s = get_settings()
    ttl = (
        timedelta(hours=s.email_verification_ttl_hours)
        if purpose == TokenPurpose.EMAIL_VERIFICATION
        else timedelta(minutes=s.password_reset_ttl_minutes)
    )
    # Invalidate earlier unused tokens of the same purpose.
    db.execute(
        update(UserToken)
        .where(
            UserToken.user_id == user_id,
            UserToken.purpose == purpose,
            UserToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )
    token = generate_token()
    db.add(
        UserToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=hash_token(token),
            expires_at=utcnow() + ttl,
        )
    )
    return token


def consume_user_token(db: Session, token: str, purpose: TokenPurpose) -> UserToken | None:
    record = db.scalar(
        select(UserToken)
        .where(UserToken.token_hash == hash_token(token), UserToken.purpose == purpose)
        .with_for_update()
    )
    if record is None or record.used_at is not None or record.expires_at <= utcnow():
        return None
    record.used_at = utcnow()
    return record
