import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.models import AI_ERROR_CLASSES
from app.schemas.common import CurrencyCode, ORMModel

Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,63}$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:/-]{1,100}$")]
Secret = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=500)]
TokenPrice = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=6)]


class ProviderOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    adapter: str
    base_url: str | None
    is_active: bool
    created_at: datetime


class ProviderCreate(BaseModel):
    code: Code
    name: Label
    adapter: str
    base_url: Annotated[str, StringConstraints(pattern=r"^https://", max_length=300)] | None = None
    is_active: bool = True


class ProviderUpdate(BaseModel):
    name: Label | None = None
    base_url: Annotated[str, StringConstraints(pattern=r"^https://", max_length=300)] | None = None
    is_active: bool | None = None


class CredentialOut(ORMModel):
    """Metadata only. The secret itself is never returned."""

    id: uuid.UUID
    provider_id: uuid.UUID
    label: str
    secret_hint: str | None
    is_active: bool
    created_at: datetime
    rotated_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class CredentialCreate(BaseModel):
    provider_id: uuid.UUID
    label: Label
    api_key: Secret


class CredentialRotate(BaseModel):
    api_key: Secret


def _check_classes(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    unknown = sorted(set(values) - set(AI_ERROR_CLASSES))
    if unknown:
        raise ValueError(f"Unknown error classes: {', '.join(unknown)}")
    return sorted(set(values))


class ModelConfigOut(ORMModel):
    id: uuid.UUID
    provider_id: uuid.UUID
    provider_code: str
    credential_id: uuid.UUID | None
    model_name: str
    display_name: str
    priority: int
    is_active: bool
    settings: dict[str, Any]
    fallback_on: list[str]
    input_price_per_million: Decimal | None
    output_price_per_million: Decimal | None
    price_currency: str
    created_at: datetime
    updated_at: datetime


class ModelConfigCreate(BaseModel):
    provider_id: uuid.UUID
    credential_id: uuid.UUID | None = None
    model_name: ModelName
    display_name: Label
    priority: int = Field(100, ge=0, le=10_000)
    is_active: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    fallback_on: list[str] | None = None
    input_price_per_million: TokenPrice | None = None
    output_price_per_million: TokenPrice | None = None
    price_currency: CurrencyCode = "USD"

    _classes = field_validator("fallback_on")(_check_classes)


class ModelConfigUpdate(BaseModel):
    credential_id: uuid.UUID | None = None
    model_name: ModelName | None = None
    display_name: Label | None = None
    priority: int | None = Field(None, ge=0, le=10_000)
    is_active: bool | None = None
    settings: dict[str, Any] | None = None
    fallback_on: list[str] | None = None
    input_price_per_million: TokenPrice | None = None
    output_price_per_million: TokenPrice | None = None
    price_currency: CurrencyCode | None = None

    _classes = field_validator("fallback_on")(_check_classes)


class ModelOrderRequest(BaseModel):
    model_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class PromptOut(ORMModel):
    id: uuid.UUID
    name: str
    version: int
    system_prompt: str
    user_prompt: str
    schema_version: str
    is_active: bool
    notes: str | None
    created_at: datetime
    activated_at: datetime | None


class PromptCreate(BaseModel):
    system_prompt: Annotated[str, StringConstraints(min_length=20, max_length=20_000)]
    user_prompt: Annotated[str, StringConstraints(min_length=5, max_length=5_000)]
    notes: Annotated[str, StringConstraints(max_length=500)] | None = None


class TestAttemptOut(BaseModel):
    provider_code: str
    model_name: str
    success: bool
    error_class: str | None
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: Decimal | None


class ModelTestOut(BaseModel):
    success: bool
    attempts: list[TestAttemptOut]
    result: dict[str, Any] | None


class UsageBucket(BaseModel):
    key: str
    calls: int
    successful_calls: int
    success_rate: float | None
    avg_latency_ms: float | None
    input_tokens: int
    output_tokens: int
    estimated_cost: Decimal
    calls_without_cost: int


class AIUsageSummaryOut(BaseModel):
    since: datetime
    until: datetime
    totals: UsageBucket
    invoices_processed: int
    cost_per_processed_invoice: Decimal | None
    fallback_successes: int
    by_model: list[UsageBucket]
    by_provider: list[UsageBucket]
    by_plan: list[UsageBucket]
    by_day: list[UsageBucket]
    by_month: list[UsageBucket]
    top_workspaces: list[UsageBucket]
    errors: dict[str, int]
