import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import CurrentAdmin, client_ip, request_id, require_permission
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.models import ConfidenceThresholdVersion, DuplicateDetectionRule
from app.schemas.quality import (
    DuplicateRuleCreate,
    DuplicateRuleOut,
    DuplicateRuleUpdate,
    ThresholdCreate,
    ThresholdOut,
)
from app.services.audit import record_audit
from app.services.confidence import active_thresholds
from app.services.duplicates import ensure_default_rules
from app.services.rule_admin import create_rule, update_rule

router = APIRouter(tags=["admin-quality"])
read = require_permission("settings.read")
write = require_permission("settings.write")


def _audit(db, request, admin, action, entity_type, entity_id, summary):
    record_audit(
        db,
        action=action,
        actor_admin_id=admin.admin.id,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        request_id=request_id(request),
        ip_address=client_ip(request),
    )


@router.get("/duplicate-rules", response_model=list[DuplicateRuleOut])
def list_rules(_: CurrentAdmin = Depends(read), db: Session = Depends(get_db)):
    ensure_default_rules(db)
    db.commit()
    return db.scalars(
        select(DuplicateDetectionRule)
        .where(DuplicateDetectionRule.workspace_id.is_(None))
        .order_by(DuplicateDetectionRule.priority)
    ).all()


@router.post(
    "/duplicate-rules", response_model=DuplicateRuleOut, status_code=status.HTTP_201_CREATED
)
def create_global_rule(
    body: DuplicateRuleCreate,
    request: Request,
    admin: CurrentAdmin = Depends(write),
    db: Session = Depends(get_db),
):
    ensure_default_rules(db)
    rule = create_rule(db, body, None)
    _audit(
        db,
        request,
        admin,
        "duplicate_rule.created",
        "duplicate_rule",
        rule.id,
        body.model_dump(mode="json"),
    )
    db.commit()
    return rule


@router.patch("/duplicate-rules/{rule_id}", response_model=DuplicateRuleOut)
def update_global_rule(
    rule_id: uuid.UUID,
    body: DuplicateRuleUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(write),
    db: Session = Depends(get_db),
):
    rule = db.get(DuplicateDetectionRule, rule_id)
    if rule is None or rule.workspace_id is not None:
        raise NotFoundError("Rule not found.")
    summary = update_rule(rule, body)
    _audit(db, request, admin, "duplicate_rule.updated", "duplicate_rule", rule.id, summary)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/confidence-thresholds", response_model=list[ThresholdOut])
def list_thresholds(_: CurrentAdmin = Depends(read), db: Session = Depends(get_db)):
    active_thresholds(db)
    db.commit()
    return db.scalars(
        select(ConfidenceThresholdVersion).order_by(ConfidenceThresholdVersion.version.desc())
    ).all()


@router.post(
    "/confidence-thresholds", response_model=ThresholdOut, status_code=status.HTTP_201_CREATED
)
def create_thresholds(
    body: ThresholdCreate,
    request: Request,
    admin: CurrentAdmin = Depends(write),
    db: Session = Depends(get_db),
):
    """Add a new threshold version and make it active. Earlier versions are kept for history;
    already-processed invoices keep the version they were evaluated with."""
    previous = active_thresholds(db)
    db.execute(update(ConfidenceThresholdVersion).values(is_active=False))
    db.flush()
    new = ConfidenceThresholdVersion(
        version=previous.version + 1,
        is_active=True,
        created_by_admin_id=admin.admin.id,
        **body.model_dump(),
    )
    db.add(new)
    db.flush()
    _audit(
        db,
        request,
        admin,
        "confidence_thresholds.updated",
        "confidence_thresholds",
        new.id,
        {"from_version": previous.version, "to_version": new.version, **body.model_dump()},
    )
    db.commit()
    return new
