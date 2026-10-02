import io
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy import case, func, literal_column, select, update
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentAdmin,
    client_ip,
    request_id,
    require_any_permission,
    require_permission,
)
from app.core.crypto import encrypt_secret, secret_hint
from app.core.db import get_db
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import (
    DEFAULT_FALLBACK_ON,
    AICall,
    ModelConfiguration,
    ModelProvider,
    PromptVersion,
    ProviderCredential,
)
from app.models.base import utcnow
from app.schemas.ai import (
    AIUsageSummaryOut,
    CredentialCreate,
    CredentialOut,
    CredentialRotate,
    ModelConfigCreate,
    ModelConfigOut,
    ModelConfigUpdate,
    ModelOrderRequest,
    ModelTestOut,
    PromptCreate,
    PromptOut,
    ProviderCreate,
    ProviderOut,
    ProviderUpdate,
    TestAttemptOut,
    UsageBucket,
)
from app.services import files
from app.services.ai.orchestrator import CallContext, ModelSettings, run_extraction
from app.services.ai.prompts import EXTRACTION_PROMPT, active_prompt
from app.services.ai.providers import ADAPTER_NAMES
from app.services.ai.schema import EXTRACTION_SCHEMA_VERSION
from app.services.audit import record_audit

router = APIRouter(prefix="/ai", tags=["admin-ai"])

read = require_permission("ai.read")
manage = require_permission("ai.manage")
manage_credentials = require_permission("credentials.manage")


def _audit(db, request, admin, action, entity_type, entity_id, summary=None) -> None:
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


def _jsonable(d: dict) -> dict:
    return {k: str(v) if isinstance(v, Decimal | uuid.UUID) else v for k, v in d.items()}


# --- providers ----------------------------------------------------------------------------------


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(_: CurrentAdmin = Depends(read), db: Session = Depends(get_db)):
    return db.scalars(select(ModelProvider).order_by(ModelProvider.name)).all()


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
def create_provider(
    body: ProviderCreate,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    if body.adapter not in ADAPTER_NAMES:
        raise AppError("Unknown adapter.", code="unknown_adapter", details=list(ADAPTER_NAMES))
    if db.scalar(select(ModelProvider.id).where(ModelProvider.code == body.code)):
        raise ConflictError("A provider with this code already exists.")
    provider = ModelProvider(**body.model_dump())
    db.add(provider)
    db.flush()
    _audit(
        db, request, admin, "ai_provider.created", "model_provider", provider.id, body.model_dump()
    )
    db.commit()
    return provider


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    provider = db.get(ModelProvider, provider_id)
    if provider is None:
        raise NotFoundError("Provider not found.")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name", "") is None or changes.get("is_active", "") is None:
        raise AppError("name and is_active cannot be null.", code="invalid_value")
    before = {k: getattr(provider, k) for k in changes}
    for k, v in changes.items():
        setattr(provider, k, v)
    _audit(
        db,
        request,
        admin,
        "ai_provider.updated",
        "model_provider",
        provider.id,
        {"before": before, "after": changes},
    )
    db.commit()
    return provider


# --- credentials (secrets are write-only) -------------------------------------------------------


@router.get("/credentials", response_model=list[CredentialOut])
def list_credentials(_: CurrentAdmin = Depends(read), db: Session = Depends(get_db)):
    return db.scalars(select(ProviderCredential).order_by(ProviderCredential.created_at)).all()


@router.post("/credentials", response_model=CredentialOut, status_code=status.HTTP_201_CREATED)
def create_credential(
    body: CredentialCreate,
    request: Request,
    admin: CurrentAdmin = Depends(manage_credentials),
    db: Session = Depends(get_db),
):
    if db.get(ModelProvider, body.provider_id) is None:
        raise NotFoundError("Provider not found.")
    cred = ProviderCredential(
        provider_id=body.provider_id,
        label=body.label,
        encrypted_secret=encrypt_secret(body.api_key),
        secret_hint=secret_hint(body.api_key),
        created_by_admin_id=admin.admin.id,
    )
    db.add(cred)
    db.flush()
    _audit(
        db,
        request,
        admin,
        "ai_credential.created",
        "provider_credential",
        cred.id,
        {"label": body.label, "provider_id": str(body.provider_id), "hint": cred.secret_hint},
    )
    db.commit()
    return cred


@router.post("/credentials/{credential_id}/rotate", response_model=CredentialOut)
def rotate_credential(
    credential_id: uuid.UUID,
    body: CredentialRotate,
    request: Request,
    admin: CurrentAdmin = Depends(manage_credentials),
    db: Session = Depends(get_db),
):
    cred = db.get(ProviderCredential, credential_id)
    if cred is None or cred.revoked_at is not None:
        raise NotFoundError("Credential not found.")
    old_hint = cred.secret_hint
    cred.encrypted_secret = encrypt_secret(body.api_key)
    cred.secret_hint = secret_hint(body.api_key)
    cred.rotated_at = utcnow()
    cred.is_active = True
    _audit(
        db,
        request,
        admin,
        "ai_credential.rotated",
        "provider_credential",
        cred.id,
        {"old_hint": old_hint, "new_hint": cred.secret_hint},
    )
    db.commit()
    return cred


@router.delete("/credentials/{credential_id}", response_model=CredentialOut)
def revoke_credential(
    credential_id: uuid.UUID,
    request: Request,
    admin: CurrentAdmin = Depends(manage_credentials),
    db: Session = Depends(get_db),
):
    """Destroys the stored secret. The row is kept so history stays attributable."""
    cred = db.get(ProviderCredential, credential_id)
    if cred is None or cred.revoked_at is not None:
        raise NotFoundError("Credential not found.")
    cred.encrypted_secret = None
    cred.is_active = False
    cred.revoked_at = utcnow()
    _audit(
        db,
        request,
        admin,
        "ai_credential.revoked",
        "provider_credential",
        cred.id,
        {"hint": cred.secret_hint},
    )
    db.commit()
    return cred


# --- model configurations ---------------------------------------------------------------------


def _model_out(m: ModelConfiguration) -> ModelConfigOut:
    data = {f: getattr(m, f) for f in ModelConfigOut.model_fields if f != "provider_code"}
    return ModelConfigOut(**data, provider_code=m.provider.code)


def _validate_model(
    db: Session, provider_id: uuid.UUID, credential_id: uuid.UUID | None, settings: dict | None
) -> None:
    if credential_id is not None:
        cred = db.get(ProviderCredential, credential_id)
        if cred is None or cred.provider_id != provider_id:
            raise AppError("Credential doesn't belong to this provider.", code="invalid_credential")
    if settings is not None:
        try:
            ModelSettings.model_validate(settings)
        except ValueError as exc:
            raise AppError(
                "Invalid model settings.", code="invalid_settings", details=str(exc)[:500]
            ) from exc


@router.get("/models", response_model=list[ModelConfigOut])
def list_models(_: CurrentAdmin = Depends(read), db: Session = Depends(get_db)):
    models = db.scalars(
        select(ModelConfiguration).order_by(
            ModelConfiguration.priority, ModelConfiguration.created_at
        )
    ).unique()
    return [_model_out(m) for m in models]


@router.post("/models", response_model=ModelConfigOut, status_code=status.HTTP_201_CREATED)
def create_model(
    body: ModelConfigCreate,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    provider = db.get(ModelProvider, body.provider_id)
    if provider is None:
        raise NotFoundError("Provider not found.")
    _validate_model(db, body.provider_id, body.credential_id, body.settings)
    data = body.model_dump()
    data["fallback_on"] = data["fallback_on"] or list(DEFAULT_FALLBACK_ON)
    data["settings"] = ModelSettings.model_validate(body.settings).model_dump()
    model = ModelConfiguration(**data)
    db.add(model)
    db.flush()
    db.refresh(model)
    _audit(
        db,
        request,
        admin,
        "ai_model.created",
        "model_configuration",
        model.id,
        _jsonable(body.model_dump(exclude={"settings"})),
    )
    db.commit()
    return _model_out(model)


@router.patch("/models/{model_id}", response_model=ModelConfigOut)
def update_model(
    model_id: uuid.UUID,
    body: ModelConfigUpdate,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    model = db.get(ModelConfiguration, model_id)
    if model is None:
        raise NotFoundError("Model not found.")
    changes = body.model_dump(exclude_unset=True)
    for required in (
        "model_name",
        "display_name",
        "priority",
        "is_active",
        "settings",
        "fallback_on",
        "price_currency",
    ):
        if required in changes and changes[required] is None:
            del changes[required]
    _validate_model(
        db,
        model.provider_id,
        changes.get("credential_id", model.credential_id),
        changes.get("settings"),
    )
    if "settings" in changes:
        changes["settings"] = ModelSettings.model_validate(changes["settings"]).model_dump()
    before = {k: getattr(model, k) for k in changes}
    for k, v in changes.items():
        setattr(model, k, v)
    _audit(
        db,
        request,
        admin,
        "ai_model.updated",
        "model_configuration",
        model.id,
        {"before": _jsonable(before), "after": _jsonable(changes)},
    )
    db.commit()
    db.refresh(model)
    return _model_out(model)


@router.put("/models/order", response_model=list[ModelConfigOut])
def reorder_models(
    body: ModelOrderRequest,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    """Set routing order: the first id becomes the primary model, the rest fallbacks in order."""
    if len(set(body.model_ids)) != len(body.model_ids):
        raise AppError("Duplicate model ids.", code="invalid_order")
    models = {
        m.id: m
        for m in db.scalars(
            select(ModelConfiguration).where(ModelConfiguration.id.in_(body.model_ids))
        ).unique()
    }
    missing = [str(i) for i in body.model_ids if i not in models]
    if missing:
        raise NotFoundError("Model not found.", details=missing)
    for position, model_id in enumerate(body.model_ids):
        models[model_id].priority = position * 10
    _audit(
        db,
        request,
        admin,
        "ai_model.reordered",
        "model_configuration",
        None,
        {"order": [str(i) for i in body.model_ids]},
    )
    db.commit()
    return list_models(admin, db)


@router.post("/models/{model_id}/test", response_model=ModelTestOut)
def test_model(
    model_id: uuid.UUID,
    request: Request,
    file: UploadFile | None = File(None),
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    """Run one model against a sample or uploaded document, outside the customer workflow."""
    model = db.get(ModelConfiguration, model_id)
    if model is None:
        raise NotFoundError("Model not found.")
    if file is not None:
        data = file.file.read(18 * 1024 * 1024 + 1)
        ftype = files.detect_type(data)
        if ftype not in (files.PDF, files.PNG, files.JPEG) or len(data) > 18 * 1024 * 1024:
            raise AppError("Upload a PDF, PNG or JPEG under 18 MB.", code="invalid_test_file")
        mime = ftype.content_type
    else:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (400, 560), "white").save(buf, format="PNG")
        data, mime = buf.getvalue(), "image/png"
    db.commit()
    result = run_extraction(data, mime, CallContext(purpose="admin_test"), only_config_id=model.id)
    _audit(
        db,
        request,
        admin,
        "ai_model.tested",
        "model_configuration",
        model.id,
        {
            "success": result.winner is not None,
            "error": result.attempts[0].error_class if result.attempts else None,
        },
    )
    db.commit()
    return ModelTestOut(
        success=result.winner is not None,
        attempts=[
            TestAttemptOut(
                provider_code=a.provider_code,
                model_name=a.model_name,
                success=a.success,
                error_class=a.error_class,
                latency_ms=a.latency_ms,
                input_tokens=a.input_tokens,
                output_tokens=a.output_tokens,
                estimated_cost=a.cost,
            )
            for a in result.attempts
        ],
        result=result.invoice.model_dump(mode="json") if result.invoice else None,
    )


# --- prompts ------------------------------------------------------------------------------------


@router.get("/prompts", response_model=list[PromptOut])
def list_prompts(_: CurrentAdmin = Depends(read), db: Session = Depends(get_db)):
    active_prompt(db)
    db.commit()
    return db.scalars(
        select(PromptVersion)
        .where(PromptVersion.name == EXTRACTION_PROMPT)
        .order_by(PromptVersion.version.desc())
    ).all()


@router.post("/prompts", response_model=PromptOut, status_code=status.HTTP_201_CREATED)
def create_prompt(
    body: PromptCreate,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    """Create a new (inactive) prompt version. Activate it explicitly once tested."""
    active_prompt(db)
    latest = (
        db.scalar(
            select(func.max(PromptVersion.version)).where(PromptVersion.name == EXTRACTION_PROMPT)
        )
        or 0
    )
    prompt = PromptVersion(
        name=EXTRACTION_PROMPT,
        version=latest + 1,
        system_prompt=body.system_prompt,
        user_prompt=body.user_prompt,
        schema_version=EXTRACTION_SCHEMA_VERSION,
        notes=body.notes,
        is_active=False,
        created_by_admin_id=admin.admin.id,
    )
    db.add(prompt)
    db.flush()
    _audit(
        db,
        request,
        admin,
        "ai_prompt.created",
        "prompt_version",
        prompt.id,
        {"version": prompt.version, "notes": body.notes},
    )
    db.commit()
    return prompt


@router.post("/prompts/{prompt_id}/activate", response_model=PromptOut)
def activate_prompt(
    prompt_id: uuid.UUID,
    request: Request,
    admin: CurrentAdmin = Depends(manage),
    db: Session = Depends(get_db),
):
    prompt = db.get(PromptVersion, prompt_id)
    if prompt is None:
        raise NotFoundError("Prompt version not found.")
    previous = db.scalar(
        select(PromptVersion.version).where(
            PromptVersion.name == prompt.name, PromptVersion.is_active.is_(True)
        )
    )
    db.execute(
        update(PromptVersion).where(PromptVersion.name == prompt.name).values(is_active=False)
    )
    db.flush()
    prompt.is_active = True
    prompt.activated_at = utcnow()
    _audit(
        db,
        request,
        admin,
        "ai_prompt.activated",
        "prompt_version",
        prompt.id,
        {"from_version": previous, "to_version": prompt.version},
    )
    db.commit()
    db.refresh(prompt)
    return prompt


# --- usage and cost reporting (spec 5.8) --------------------------------------------------------


def _buckets(
    db: Session, key, where, limit: int | None = None, order_by_cost: bool = False
) -> list[UsageBucket]:
    """Aggregate calls grouped by ``key``; ``key=None`` aggregates everything into one row."""
    cost = func.coalesce(func.sum(AICall.estimated_cost), 0)
    stmt = select(
        (key if key is not None else literal_column("'all'")).label("k"),
        func.count(),
        func.sum(case((AICall.success, 1), else_=0)),
        func.avg(AICall.latency_ms),
        func.coalesce(func.sum(AICall.input_tokens), 0),
        func.coalesce(func.sum(AICall.output_tokens), 0),
        cost,
        func.sum(case((AICall.estimated_cost.is_(None), 1), else_=0)),
    ).where(*where)
    if key is not None:
        stmt = stmt.group_by(key).order_by(cost.desc() if order_by_cost else key)
    if limit:
        stmt = stmt.limit(limit)
    out = []
    for k, calls, ok, latency, tin, tout, c, no_cost in db.execute(stmt):
        out.append(
            UsageBucket(
                key=str(k) if k is not None else "unknown",
                calls=calls,
                successful_calls=ok or 0,
                success_rate=round((ok or 0) / calls, 4) if calls else None,
                avg_latency_ms=round(float(latency), 1) if latency is not None else None,
                input_tokens=int(tin),
                output_tokens=int(tout),
                estimated_cost=Decimal(c).quantize(Decimal("0.000001")),
                calls_without_cost=no_cost or 0,
            )
        )
    return out


@router.get("/usage/summary", response_model=AIUsageSummaryOut)
def usage_summary(
    since: datetime | None = None,
    until: datetime | None = None,
    purpose: str = Query("extraction", pattern="^(extraction|admin_test|all)$"),
    _: CurrentAdmin = Depends(require_any_permission("ai.read", "costs.read")),
    db: Session = Depends(get_db),
) -> AIUsageSummaryOut:
    until = until or utcnow()
    since = since or until - timedelta(days=30)
    where = [AICall.created_at >= since, AICall.created_at < until]
    if purpose != "all":
        where.append(AICall.purpose == purpose)
    total = _buckets(db, None, where)[0]
    processed = (
        db.scalar(
            select(func.count(func.distinct(AICall.job_id))).where(
                *where, AICall.success.is_(True), AICall.job_id.is_not(None)
            )
        )
        or 0
    )
    fallback_successes = (
        db.scalar(
            select(func.count())
            .select_from(AICall)
            .where(*where, AICall.success.is_(True), AICall.route_position > 0)
        )
        or 0
    )
    errors = dict(
        db.execute(
            select(AICall.error_class, func.count())
            .where(*where, AICall.success.is_(False))
            .group_by(AICall.error_class)
        ).all()
    )
    return AIUsageSummaryOut(
        since=since,
        until=until,
        totals=total,
        invoices_processed=processed,
        cost_per_processed_invoice=(total.estimated_cost / processed).quantize(Decimal("0.000001"))
        if processed
        else None,
        fallback_successes=fallback_successes,
        by_model=_buckets(db, AICall.model_name, where),
        by_provider=_buckets(db, AICall.provider_code, where),
        by_plan=_buckets(db, AICall.plan_code, where),
        by_day=_buckets(
            db, func.to_char(func.date_trunc("day", AICall.created_at), "YYYY-MM-DD"), where
        ),
        by_month=_buckets(
            db, func.to_char(func.date_trunc("month", AICall.created_at), "YYYY-MM"), where
        ),
        top_workspaces=_buckets(db, AICall.workspace_id, where, limit=20, order_by_cost=True),
        errors={str(k): v for k, v in errors.items()},
    )
