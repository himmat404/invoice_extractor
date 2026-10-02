"""Helpers for recording admin audit logs and workspace activity (§21.4).

Callers must pass only safe summaries: never secrets, credentials, or document contents.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import ActivityEvent, AuditLog

_REDACT_KEYS = {"password", "password_hash", "token", "secret", "api_key", "key_value"}


def _safe(summary: dict[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {}
    return {k: ("[redacted]" if k.lower() in _REDACT_KEYS else v) for k, v in summary.items()}


def record_audit(
    db: Session,
    *,
    action: str,
    actor_admin_id: uuid.UUID | None,
    entity_type: str | None = None,
    entity_id: Any = None,
    workspace_id: uuid.UUID | None = None,
    summary: dict[str, Any] | None = None,
    outcome: str = "success",
    request_id: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        actor_admin_id=actor_admin_id,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        workspace_id=workspace_id,
        summary=_safe(summary),
        outcome=outcome,
        request_id=request_id,
        ip_address=ip_address,
    )
    db.add(entry)
    return entry


def record_activity(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    action: str,
    actor_user_id: uuid.UUID | None = None,
    actor_type: str = "user",
    entity_type: str | None = None,
    entity_id: Any = None,
    summary: dict[str, Any] | None = None,
    outcome: str = "success",
    request_id: str | None = None,
) -> ActivityEvent:
    event = ActivityEvent(
        workspace_id=workspace_id,
        action=action,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=_safe(summary),
        outcome=outcome,
        request_id=request_id,
    )
    db.add(event)
    return event
