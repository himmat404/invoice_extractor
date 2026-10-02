"""Shared create/update logic for duplicate rules (global and workspace-scoped)."""

import uuid

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import DuplicateDetectionRule, DuplicateRuleType
from app.schemas.quality import DuplicateRuleCreate, DuplicateRuleUpdate


def create_rule(
    db: Session, body: DuplicateRuleCreate, workspace_id: uuid.UUID | None
) -> DuplicateDetectionRule:
    if workspace_id is not None and body.rule_type == DuplicateRuleType.EXACT_FILE:
        raise AppError("Exact-file matching is a platform rule.", code="invalid_rule_type")
    rule = DuplicateDetectionRule(workspace_id=workspace_id, **body.model_dump())
    db.add(rule)
    db.flush()
    return rule


def update_rule(rule: DuplicateDetectionRule, body: DuplicateRuleUpdate) -> dict:
    changes = {
        k: v
        for k, v in body.model_dump(exclude_unset=True).items()
        if v is not None or k == "threshold"
    }
    fields = changes.get("fields", rule.fields)
    if rule.rule_type == DuplicateRuleType.MULTI_FIELD and len(fields) < 2:
        raise AppError("Multi-field rules need at least two fields.", code="invalid_rule")
    if rule.rule_type == DuplicateRuleType.SIMILARITY and changes.get("threshold", 1) is None:
        raise AppError("Similarity rules need a threshold.", code="invalid_rule")
    before = {k: getattr(rule, k) for k in changes}
    for k, v in changes.items():
        setattr(rule, k, v)
    return {"before": before, "after": changes}
