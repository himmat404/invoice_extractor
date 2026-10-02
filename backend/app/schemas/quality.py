import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.models import DuplicateRuleType
from app.schemas.common import ORMModel
from app.services.duplicates import MATCH_FIELDS

RuleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


def _fields(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    unknown = sorted(set(values) - set(MATCH_FIELDS))
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(unknown)}")
    return list(dict.fromkeys(values))


class DuplicateRuleOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID | None
    name: str
    rule_type: DuplicateRuleType
    fields: list[str]
    threshold: float | None
    confirmed: bool
    blocking: bool
    is_active: bool
    priority: int
    created_at: datetime


class DuplicateRuleCreate(BaseModel):
    name: RuleName
    rule_type: DuplicateRuleType
    fields: list[str] = Field(default_factory=list, max_length=6)
    threshold: float | None = Field(None, ge=0.5, le=1.0)
    confirmed: bool = False
    blocking: bool = False
    is_active: bool = True
    priority: int = Field(100, ge=0, le=10_000)

    _f = field_validator("fields")(_fields)

    @model_validator(mode="after")
    def _shape(self):
        if self.rule_type == DuplicateRuleType.MULTI_FIELD and len(self.fields) < 2:
            raise ValueError("Multi-field rules need at least two fields")
        if self.rule_type == DuplicateRuleType.SIMILARITY and self.threshold is None:
            self.threshold = 0.85
        return self


class DuplicateRuleUpdate(BaseModel):
    name: RuleName | None = None
    fields: list[str] | None = Field(None, max_length=6)
    threshold: float | None = Field(None, ge=0.5, le=1.0)
    confirmed: bool | None = None
    blocking: bool | None = None
    is_active: bool | None = None
    priority: int | None = Field(None, ge=0, le=10_000)

    _f = field_validator("fields")(_fields)


class ThresholdOut(ORMModel):
    id: uuid.UUID
    version: int
    high_min: float
    medium_min: float
    review_fields: list[str]
    is_active: bool
    notes: str | None
    created_at: datetime


class ThresholdCreate(BaseModel):
    high_min: float = Field(ge=0, le=1)
    medium_min: float = Field(ge=0, le=1)
    review_fields: list[Annotated[str, StringConstraints(max_length=100)]] = Field(max_length=40)
    notes: Annotated[str, StringConstraints(max_length=300)] | None = None

    @model_validator(mode="after")
    def _order(self):
        if self.medium_min > self.high_min:
            raise ValueError("medium_min must not exceed high_min")
        return self
