import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from app.models import (
    BillingInterval,
    CreditBucket,
    LedgerEntryType,
    SubscriptionSource,
    SubscriptionStatus,
)
from app.schemas.common import CurrencyCode, ORMModel
from app.services.entitlements import PlanEntitlements

Price = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
PlanCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,63}$")]
Name100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


# --- customer-facing ---------------------------------------------------------------------------


class PublicPlanOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    currency: str
    monthly_price: Decimal
    annual_price: Decimal
    included_credits: int
    features: list[str]
    entitlements: PlanEntitlements


class CreditPackageOut(ORMModel):
    id: uuid.UUID
    name: str
    credits: int
    price: Decimal
    currency: str


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    status: SubscriptionStatus
    billing_interval: BillingInterval
    current_period_start: datetime
    current_period_end: datetime
    next_billing_date: datetime | None
    cancel_at_period_end: bool
    price: Decimal
    currency: str
    plan: PublicPlanOut
    entitlements: PlanEntitlements


class UsageOut(BaseModel):
    period_start: datetime
    period_end: datetime
    included_credits: int
    period_credits_remaining: int
    extra_credits_remaining: int
    total_credits_remaining: int
    credits_consumed: int
    overage_consumed: int
    invoices_processed: int


class CustomerLedgerEntryOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    entry_type: LedgerEntryType
    bucket: CreditBucket
    amount: int
    balance_after: int
    description: str | None


# --- admin -------------------------------------------------------------------------------------


class AdminPlanOut(PublicPlanOut):
    is_active: bool
    is_public: bool
    is_default: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class PlanCreate(BaseModel):
    code: PlanCode
    name: Name100
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    currency: CurrencyCode = "USD"
    monthly_price: Price = Decimal("0")
    annual_price: Price = Decimal("0")
    included_credits: int = Field(0, ge=0, le=10_000_000)
    entitlements: dict[str, Any] = Field(default_factory=dict)
    features: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=list, max_length=30
    )
    is_active: bool = True
    is_public: bool = True
    is_default: bool = False
    sort_order: int = 0


class PlanUpdate(BaseModel):
    name: Name100 | None = None
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    currency: CurrencyCode | None = None
    monthly_price: Price | None = None
    annual_price: Price | None = None
    included_credits: int | None = Field(None, ge=0, le=10_000_000)
    entitlements: dict[str, Any] | None = None
    features: list[Annotated[str, StringConstraints(max_length=200)]] | None = Field(
        None, max_length=30
    )
    is_active: bool | None = None
    is_public: bool | None = None
    is_default: bool | None = None
    sort_order: int | None = None


class AdminCreditPackageOut(CreditPackageOut):
    is_active: bool
    sort_order: int


class CreditPackageCreate(BaseModel):
    name: Name100
    credits: int = Field(gt=0, le=10_000_000)
    price: Price
    currency: CurrencyCode = "USD"
    is_active: bool = True
    sort_order: int = 0


class CreditPackageUpdate(BaseModel):
    name: Name100 | None = None
    credits: int | None = Field(None, gt=0, le=10_000_000)
    price: Price | None = None
    currency: CurrencyCode | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class AdminSubscriptionOut(ORMModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    status: SubscriptionStatus
    billing_interval: BillingInterval
    source: SubscriptionSource
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    canceled_at: datetime | None
    ended_at: datetime | None
    price: Decimal
    currency: str
    created_at: datetime


class AdminLedgerEntryOut(CustomerLedgerEntryOut):
    subscription_id: uuid.UUID | None
    reference_type: str | None
    reference_id: str | None
    actor_admin_id: uuid.UUID | None


class AdminWorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime
    subscription: AdminSubscriptionOut | None
    plan_name: str | None
    entitlement_overrides: dict[str, Any]
    effective_entitlements: PlanEntitlements
    usage: UsageOut


class ChangePlanRequest(BaseModel):
    plan_id: uuid.UUID
    billing_interval: BillingInterval = BillingInterval.MONTH
    reason: Reason


class CreditAdjustRequest(BaseModel):
    amount: int = Field(gt=0, le=1_000_000)
    bucket: CreditBucket = CreditBucket.EXTRA
    reason: Reason


class OverridesRequest(BaseModel):
    overrides: dict[str, Any]
    reason: Reason
