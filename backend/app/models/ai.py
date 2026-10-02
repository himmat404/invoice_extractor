import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPrimaryKey, utcnow

#: Error classes a model call can fail with (used for fallback rules).
AI_ERROR_CLASSES = (
    "auth_failed",
    "rate_limited",
    "timeout",
    "provider_unavailable",
    "invalid_request",
    "content_blocked",
    "malformed_response",
    "credential_missing",
)
DEFAULT_FALLBACK_ON = [
    "auth_failed",
    "rate_limited",
    "timeout",
    "provider_unavailable",
    "content_blocked",
    "malformed_response",
    "credential_missing",
]

TokenPrice = Numeric(12, 6)


class ModelProvider(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "model_providers"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    adapter: Mapped[str] = mapped_column(String(32))  # gemini | fake
    base_url: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProviderCredential(UUIDPrimaryKey, Timestamps, Base):
    """Provider API key, encrypted at rest. The plaintext is never returned by any API."""

    __tablename__ = "provider_credentials"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_providers.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    secret_hint: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    provider: Mapped[ModelProvider] = relationship(lazy="joined")


class ModelConfiguration(UUIDPrimaryKey, Timestamps, Base):
    """A provider model in the routing chain. Lowest ``priority`` is the primary model."""

    __tablename__ = "model_configurations"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_providers.id", ondelete="RESTRICT")
    )
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_credentials.id", ondelete="SET NULL")
    )
    model_name: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(100))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    fallback_on: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), default=lambda: list(DEFAULT_FALLBACK_ON)
    )
    input_price_per_million: Mapped[Decimal | None] = mapped_column(TokenPrice)
    output_price_per_million: Mapped[Decimal | None] = mapped_column(TokenPrice)
    price_currency: Mapped[str] = mapped_column(String(3), default="USD")

    provider: Mapped[ModelProvider] = relationship(lazy="joined")
    credential: Mapped[ProviderCredential | None] = relationship(lazy="joined")


class PromptVersion(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        Index("uq_prompt_versions_active", "name", unique=True, postgresql_where=text("is_active")),
    )

    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    system_prompt: Mapped[str] = mapped_column(Text)
    user_prompt: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AICall(UUIDPrimaryKey, Base):
    """One model invocation: usage, latency, outcome and estimated cost (spec 5.8).

    Provider, model and plan are snapshotted so reports stay correct after config changes.
    """

    __tablename__ = "ai_calls"
    __table_args__ = (
        Index("ix_ai_calls_created", "created_at"),
        Index("ix_ai_calls_model_created", "model_name", "created_at"),
        Index("ix_ai_calls_ws_created", "workspace_id", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    purpose: Mapped[str] = mapped_column(String(32), default="extraction")  # | admin_test
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_jobs.id", ondelete="SET NULL"), index=True
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    plan_code: Mapped[str | None] = mapped_column(String(64))
    model_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_configurations.id", ondelete="SET NULL")
    )
    provider_code: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(100))
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_versions.id", ondelete="SET NULL")
    )
    route_position: Mapped[int] = mapped_column(Integer, default=0)  # 0 = primary
    success: Mapped[bool] = mapped_column(Boolean)
    error_class: Mapped[str | None] = mapped_column(String(32))
    error_detail: Mapped[str | None] = mapped_column(String(1000))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    cost_currency: Mapped[str] = mapped_column(String(3), default="USD")
    latency_ms: Mapped[int] = mapped_column(Integer)
