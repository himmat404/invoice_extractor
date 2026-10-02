import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_workspace_context, request_id
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.models import DuplicateDetectionRule, WorkspaceRole
from app.schemas.quality import DuplicateRuleCreate, DuplicateRuleOut, DuplicateRuleUpdate
from app.services.audit import record_activity
from app.services.duplicates import active_rules
from app.services.entitlements import get_entitlements, require_feature
from app.services.rule_admin import create_rule, update_rule

router = APIRouter(prefix="/duplicate-rules", tags=["duplicate-rules"])


@router.get("", response_model=list[DuplicateRuleOut])
def list_rules(
    ctx: WorkspaceContext = Depends(get_workspace_context), db: Session = Depends(get_db)
):
    """Rules applied to this workspace: platform rules plus the workspace's own."""
    rules = active_rules(db, ctx.workspace_id)
    own_inactive = db.scalars(
        select(DuplicateDetectionRule).where(
            DuplicateDetectionRule.workspace_id == ctx.workspace_id,
            DuplicateDetectionRule.is_active.is_(False),
        )
    ).all()
    db.commit()
    return [*rules, *own_inactive]


def _require_entitled(db: Session, ctx: WorkspaceContext) -> None:
    ctx.require_role(WorkspaceRole.OWNER, WorkspaceRole.ADMIN)
    require_feature(get_entitlements(db, ctx.workspace_id), "duplicate_rule_overrides")


@router.post("", response_model=DuplicateRuleOut, status_code=status.HTTP_201_CREATED)
def create_workspace_rule(
    body: DuplicateRuleCreate,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
):
    _require_entitled(db, ctx)
    rule = create_rule(db, body, ctx.workspace_id)
    record_activity(
        db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user.id,
        action="duplicate_rule.created",
        entity_type="duplicate_rule",
        entity_id=rule.id,
        summary={"name": rule.name},
        request_id=request_id(request),
    )
    db.commit()
    return rule


@router.patch("/{rule_id}", response_model=DuplicateRuleOut)
def update_workspace_rule(
    rule_id: uuid.UUID,
    body: DuplicateRuleUpdate,
    request: Request,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
):
    _require_entitled(db, ctx)
    rule = db.get(DuplicateDetectionRule, rule_id)
    if rule is None or rule.workspace_id != ctx.workspace_id:
        raise NotFoundError("Rule not found.")
    update_rule(rule, body)
    record_activity(
        db,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user.id,
        action="duplicate_rule.updated",
        entity_type="duplicate_rule",
        entity_id=rule.id,
        summary={"fields": sorted(body.model_dump(exclude_unset=True))},
        request_id=request_id(request),
    )
    db.commit()
    db.refresh(rule)
    return rule
