"""Plan entitlements, enforced on the backend (spec 5.5, 14).

Effective entitlements = plan entitlements, overlaid with admin-set workspace overrides.
Feature flags (later) can only narrow availability, never widen it beyond these.
"""

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.core.errors import AppError, PermissionDeniedError
from app.models import Plan, Workspace

EXPORT_FORMATS: dict[str, str] = {
    "csv": "CSV",
    "xlsx": "Excel (XLSX)",
    "json": "JSON",
    "pdf": "PDF summary",
    "tally": "Tally",
    "quickbooks": "QuickBooks",
    "zoho_books": "Zoho Books",
    "xero": "Xero",
}


class PlanEntitlements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_file_size_mb: int = Field(10, ge=1, le=200)
    max_files_per_batch: int = Field(1, ge=1, le=10_000)
    zip_upload: bool = False
    export_formats: list[str] = Field(default_factory=lambda: ["csv", "xlsx", "json"])
    history_retention_days: int | None = Field(90, ge=1)  # None = unlimited
    max_team_members: int = Field(1, ge=1)
    credit_purchases: bool = False
    overage_allowed: bool = False
    api_access: bool = False
    webhooks: bool = False
    max_webhook_endpoints: int = Field(0, ge=0)
    scheduled_exports: bool = False
    advanced_reports: bool = False
    supplier_directory: bool = True
    item_directory: bool = False
    duplicate_rule_overrides: bool = False

    @field_validator("export_formats")
    @classmethod
    def _known_formats(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - EXPORT_FORMATS.keys())
        if unknown:
            raise ValueError(f"Unknown export formats: {', '.join(unknown)}")
        return sorted(set(value), key=list(EXPORT_FORMATS).index)


BOOLEAN_FEATURES = {
    name for name, field in PlanEntitlements.model_fields.items() if field.annotation is bool
}


def validate_entitlements(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return PlanEntitlements.model_validate(data).model_dump()
    except ValidationError as exc:
        raise AppError(
            "Invalid entitlements.",
            code="invalid_entitlements",
            details=[{"loc": list(e["loc"]), "message": e["msg"]} for e in exc.errors()],
        ) from exc


def validate_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    """Overrides are a partial entitlements object; validate by merging onto defaults."""
    unknown = sorted(set(overrides) - PlanEntitlements.model_fields.keys())
    if unknown:
        raise AppError("Unknown entitlement keys.", code="invalid_entitlements", details=unknown)
    merged = validate_entitlements({**PlanEntitlements().model_dump(), **overrides})
    return {k: merged[k] for k in overrides}


def effective_entitlements(plan: Plan | None, workspace: Workspace) -> PlanEntitlements:
    base = dict(plan.entitlements or {}) if plan else {}
    try:
        return PlanEntitlements.model_validate({**base, **(workspace.entitlement_overrides or {})})
    except ValidationError:
        # Stored data predates a schema change: fall back to the plan alone, then defaults.
        try:
            return PlanEntitlements.model_validate(base)
        except ValidationError:
            return PlanEntitlements()


def get_entitlements(db: Session, workspace_id: uuid.UUID) -> PlanEntitlements:
    from app.services.subscriptions import current_subscription

    workspace = db.get(Workspace, workspace_id)
    sub = current_subscription(db, workspace_id)
    return effective_entitlements(sub.plan if sub else None, workspace)


def require_feature(entitlements: PlanEntitlements, feature: str) -> None:
    if feature not in BOOLEAN_FEATURES:
        raise ValueError(f"{feature!r} is not a boolean feature")
    if not getattr(entitlements, feature):
        raise PermissionDeniedError(
            "This feature isn't included in your current plan. Upgrade to use it.",
            code="plan_feature_unavailable",
            details={"feature": feature},
        )


def require_export_format(entitlements: PlanEntitlements, fmt: str) -> None:
    if fmt not in entitlements.export_formats:
        raise PermissionDeniedError(
            "This export format isn't included in your current plan.",
            code="plan_feature_unavailable",
            details={"export_format": fmt},
        )
