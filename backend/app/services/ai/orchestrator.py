"""AI orchestration: primary/fallback routing, safe parsing and usage recording (spec 5.3, 9).

Customers never see which provider or model handled their invoice; that is recorded on the job
and in ``ai_calls`` for support, cost analysis and audit.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.core.crypto import decrypt_secret
from app.core.db import SessionLocal
from app.models import AICall, ModelConfiguration, ModelProvider, PromptVersion
from app.models.base import utcnow
from app.services.ai.prompts import active_prompt
from app.services.ai.providers import (
    ExtractionRequest,
    ProviderError,
    ProviderResponse,
    get_adapter,
)
from app.services.ai.schema import (
    CONFIDENCE_METHOD,
    EXTRACTION_SCHEMA_VERSION,
    PROVIDER_RESPONSE_SCHEMA,
    CanonicalInvoice,
    parse_model_output,
)

logger = logging.getLogger("invoiceflow.ai")

TRANSIENT_ERRORS = {"rate_limited", "timeout", "provider_unavailable"}
MILLION = Decimal(1_000_000)


class ModelSettings(BaseModel):
    """Per-model settings editable by admins."""

    timeout_seconds: float = Field(90, ge=5, le=600)
    temperature: float = Field(0.0, ge=0, le=2)
    max_output_tokens: int = Field(8192, ge=256, le=65536)


def model_settings(config: ModelConfiguration) -> ModelSettings:
    try:
        return ModelSettings.model_validate(config.settings or {})
    except ValidationError:
        return ModelSettings()


def estimate_cost(
    config: ModelConfiguration, input_tokens: int | None, output_tokens: int | None
) -> Decimal | None:
    """Cost estimate; ``None`` when prices or token counts are unknown (never guessed)."""
    if config.input_price_per_million is None or config.output_price_per_million is None:
        return None
    if input_tokens is None or output_tokens is None:
        return None
    cost = (
        Decimal(input_tokens) * config.input_price_per_million
        + Decimal(output_tokens) * config.output_price_per_million
    ) / MILLION
    return cost.quantize(Decimal("0.000001"))


@dataclass
class Attempt:
    config_id: uuid.UUID
    provider_code: str
    model_name: str
    position: int
    success: bool
    error_class: str | None
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    cost: Decimal | None


@dataclass
class RoutingResult:
    invoice: CanonicalInvoice | None
    attempts: list[Attempt] = field(default_factory=list)
    prompt_version_id: uuid.UUID | None = None
    no_models: bool = False

    @property
    def winner(self) -> Attempt | None:
        return next((a for a in self.attempts if a.success), None)


@dataclass
class CallContext:
    purpose: str = "extraction"
    job_id: uuid.UUID | None = None
    invoice_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    plan_code: str | None = None


def model_chain(db, only_config_id: uuid.UUID | None = None) -> list[ModelConfiguration]:
    stmt = select(ModelConfiguration).join(ModelProvider)
    if only_config_id is not None:
        stmt = stmt.where(ModelConfiguration.id == only_config_id)
    else:
        stmt = stmt.where(ModelConfiguration.is_active.is_(True), ModelProvider.is_active.is_(True))
    return list(
        db.scalars(
            stmt.order_by(ModelConfiguration.priority, ModelConfiguration.created_at)
        ).unique()
    )


def _api_key(config: ModelConfiguration) -> str | None:
    cred = config.credential
    if cred is None or not cred.is_active or not cred.encrypted_secret:
        return None
    return decrypt_secret(cred.encrypted_secret)


def _record(
    ctx: CallContext,
    config: ModelConfiguration,
    prompt: PromptVersion,
    attempt: Attempt,
    detail: str | None,
) -> None:
    with SessionLocal() as db:
        db.add(
            AICall(
                purpose=ctx.purpose,
                job_id=ctx.job_id,
                invoice_id=ctx.invoice_id,
                workspace_id=ctx.workspace_id,
                plan_code=ctx.plan_code,
                model_configuration_id=config.id,
                provider_code=attempt.provider_code,
                model_name=attempt.model_name,
                prompt_version_id=prompt.id,
                route_position=attempt.position,
                success=attempt.success,
                error_class=attempt.error_class,
                error_detail=(detail or "")[:1000] or None,
                input_tokens=attempt.input_tokens,
                output_tokens=attempt.output_tokens,
                estimated_cost=attempt.cost,
                cost_currency=config.price_currency,
                latency_ms=attempt.latency_ms,
            )
        )
        if attempt.success and config.credential_id:
            from app.models import ProviderCredential

            cred = db.get(ProviderCredential, config.credential_id)
            if cred:
                cred.last_used_at = utcnow()
        db.commit()


def run_extraction(
    document: bytes, mime_type: str, ctx: CallContext, only_config_id: uuid.UUID | None = None
) -> RoutingResult:
    """Try the configured models in order until one returns a usable result."""
    with SessionLocal() as db:
        prompt = active_prompt(db)
        chain = model_chain(db, only_config_id)
        db.commit()
    result = RoutingResult(invoice=None, prompt_version_id=prompt.id, no_models=not chain)

    for position, config in enumerate(chain):
        settings = model_settings(config)
        started = time.monotonic()
        response: ProviderResponse | None = None
        error: ProviderError | None = None
        try:
            api_key = _api_key(config)
            if api_key is None and config.provider.adapter != "fake":
                raise ProviderError("credential_missing", "no active credential")
            response = get_adapter(config.provider.adapter).extract(
                ExtractionRequest(
                    model_name=config.model_name,
                    api_key=api_key,
                    system_prompt=prompt.system_prompt,
                    user_prompt=prompt.user_prompt,
                    document=document,
                    mime_type=mime_type,
                    response_schema=PROVIDER_RESPONSE_SCHEMA,
                    timeout_seconds=settings.timeout_seconds,
                    temperature=settings.temperature,
                    max_output_tokens=settings.max_output_tokens,
                    base_url=config.provider.base_url,
                )
            )
            invoice = parse_model_output(response.data)
        except ProviderError as exc:
            error = exc
        except ValueError as exc:  # parse failure or undecryptable credential
            error = ProviderError(
                "malformed_response",
                str(exc)[:300],
                input_tokens=response.input_tokens if response else None,
                output_tokens=response.output_tokens if response else None,
            )
        except Exception as exc:  # noqa: BLE001 - adapter bug: treat as provider outage
            logger.exception("Adapter %s crashed", config.provider.adapter)
            error = ProviderError("provider_unavailable", f"{type(exc).__name__}")

        in_tok = response.input_tokens if response else (error.input_tokens if error else None)
        out_tok = response.output_tokens if response else (error.output_tokens if error else None)
        attempt = Attempt(
            config_id=config.id,
            provider_code=config.provider.code,
            model_name=config.model_name,
            position=position,
            success=error is None,
            error_class=error.error_class if error else None,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost=estimate_cost(config, in_tok, out_tok),
        )
        result.attempts.append(attempt)
        _record(ctx, config, prompt, attempt, error.detail if error else None)
        if error is None:
            result.invoice = invoice
            return result
        logger.warning("Model %s failed (%s)", config.model_name, error.error_class)
        if error.error_class not in (config.fallback_on or []):
            break
    return result


# --- job handler --------------------------------------------------------------------------------


def ai_handler(ctx):
    from app.services import jobs
    from app.services.subscriptions import current_subscription

    with SessionLocal() as db:
        sub = current_subscription(db, ctx.workspace_id)
        plan_code = sub.plan.code if sub else None
    result = run_extraction(
        ctx.data,
        ctx.content_type,
        CallContext(
            job_id=ctx.job_id,
            invoice_id=ctx.invoice_id,
            workspace_id=ctx.workspace_id,
            plan_code=plan_code,
        ),
    )
    if result.no_models:
        raise jobs.RetryableJobError("provider_unavailable", "no active model configuration")
    winner = result.winner
    if winner is None:
        classes = [a.error_class for a in result.attempts]
        summary = ", ".join(f"{a.model_name}:{a.error_class}" for a in result.attempts)
        if TRANSIENT_ERRORS & set(classes):
            raise jobs.RetryableJobError("provider_unavailable", f"all models failed: {summary}")
        raise jobs.PermanentJobError("extraction_failed", f"all models failed: {summary}")
    return jobs.ExtractionOutcome(
        data=result.invoice.model_dump(mode="json"),
        metadata={
            "provider_code": winner.provider_code,
            "model_name": winner.model_name,
            "model_configuration_id": winner.config_id,
            "prompt_version_id": result.prompt_version_id,
            "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
            "confidence_method": CONFIDENCE_METHOD,
        },
    )


def _register() -> None:
    from app.services.jobs import register_handler

    register_handler("ai", ai_handler)


_register()
