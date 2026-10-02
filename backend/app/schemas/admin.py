import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, EmailStr, StringConstraints

from app.models import AccountStatus, WorkspaceRole
from app.schemas.common import NonEmpty200, ORMModel


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(max_length=128)]


class AdminRoleOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    permissions: list[str]


class AdminRoleCreate(BaseModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    description: Annotated[str, StringConstraints(max_length=300)] | None = None
    permissions: list[str]


class AdminRoleUpdate(BaseModel):
    description: Annotated[str, StringConstraints(max_length=300)] | None = None
    permissions: list[str] | None = None


class AdminUserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    status: AccountStatus
    role: AdminRoleOut
    last_login_at: datetime | None
    created_at: datetime


class AdminUserCreate(BaseModel):
    email: EmailStr
    full_name: NonEmpty200
    password: Annotated[str, StringConstraints(min_length=12, max_length=128)]
    role_id: uuid.UUID


class AdminUserUpdate(BaseModel):
    full_name: NonEmpty200 | None = None
    role_id: uuid.UUID | None = None
    status: AccountStatus | None = None


class AdminMeOut(BaseModel):
    admin: AdminUserOut
    permissions: list[str]


class CustomerSummaryOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    status: AccountStatus
    is_verified: bool
    last_login_at: datetime | None
    created_at: datetime


class CustomerWorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    status: AccountStatus
    role: WorkspaceRole


class CustomerDetailOut(CustomerSummaryOut):
    timezone: str
    locale: str
    currency: str
    workspaces: list[CustomerWorkspaceOut] = []


class StatusChangeRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class AuditLogOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    actor_admin_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: str | None
    workspace_id: uuid.UUID | None
    outcome: str
    summary: dict[str, Any]
    request_id: str | None
    ip_address: str | None


class SystemSettingOut(ORMModel):
    key: str
    value: Any
    description: str | None
    updated_at: datetime


class SystemSettingUpdate(BaseModel):
    value: Any
    description: Annotated[str, StringConstraints(max_length=300)] | None = None


class PermissionOut(BaseModel):
    name: str
    description: str
