import uuid
from datetime import datetime
from typing import Annotated, Any
from zoneinfo import available_timezones

from pydantic import AfterValidator, BaseModel, EmailStr, StringConstraints

from app.models import WorkspaceRole
from app.schemas.common import CountryCode, CurrencyCode, NonEmpty200, ORMModel, Password


def _valid_timezone(value: str) -> str:
    if value not in available_timezones():
        raise ValueError("Unknown timezone")
    return value


Timezone = Annotated[str, AfterValidator(_valid_timezone)]
Locale = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    full_name: NonEmpty200
    workspace_name: NonEmpty200 | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(max_length=128)]


class EmailRequest(BaseModel):
    email: EmailStr


class TokenRequest(BaseModel):
    token: Annotated[str, StringConstraints(min_length=10, max_length=200)]


class ResetPasswordRequest(TokenRequest):
    password: Password


class ChangePasswordRequest(BaseModel):
    current_password: Annotated[str, StringConstraints(max_length=128)]
    new_password: Password


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_verified: bool
    timezone: str
    locale: str
    currency: str
    created_at: datetime


class UpdateProfileRequest(BaseModel):
    full_name: NonEmpty200 | None = None
    timezone: Timezone | None = None
    locale: Locale | None = None
    currency: CurrencyCode | None = None


class SessionOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime | None
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    current: bool = False


class WorkspaceOut(ORMModel):
    id: uuid.UUID
    name: str
    legal_name: str | None
    tax_id: str | None
    address: str | None
    country: str | None
    default_currency: str
    created_at: datetime


class WorkspaceMembershipOut(BaseModel):
    workspace: WorkspaceOut
    role: WorkspaceRole


class UpdateWorkspaceRequest(BaseModel):
    name: NonEmpty200 | None = None
    legal_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = (
        None
    )
    tax_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = None
    address: Annotated[str, StringConstraints(max_length=1000)] | None = None
    country: CountryCode | None = None
    default_currency: CurrencyCode | None = None


class MeOut(BaseModel):
    user: UserOut
    workspace: WorkspaceOut
    role: WorkspaceRole


class ActivityOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    actor_type: str
    actor_user_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: str | None
    outcome: str
    summary: dict[str, Any]
