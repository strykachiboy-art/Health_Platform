from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
)

from app.core.enums.clinical_safety_enums import (
    ClinicalAlertStatus,
    ClinicalRuleAction,
    ClinicalRuleSeverity,
)


class ClinicalAlertResponseSchema(BaseModel):
    id: StrictInt = Field(
        ...,
        gt=0,
    )

    clinic_id: StrictInt = Field(
        ...,
        gt=0,
    )

    patient_id: StrictInt = Field(
        ...,
        gt=0,
    )

    rule_id: StrictInt = Field(
        ...,
        gt=0,
    )

    rule_version: StrictInt = Field(
        ...,
        gt=0,
    )

    severity: ClinicalRuleSeverity
    action: ClinicalRuleAction
    status: ClinicalAlertStatus

    title: str
    message: str

    context: dict[str, Any]

    source_type: str
    source_id: StrictInt | None = Field(
        default=None,
        gt=0,
    )

    deduplication_key: str | None

    generated_at: datetime
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class ClinicalAlertListQuerySchema(BaseModel):
    page: StrictInt = Field(
        default=1,
        ge=1,
    )

    per_page: StrictInt = Field(
        default=50,
        ge=1,
        le=500,
    )

    patient_id: StrictInt | None = Field(
        default=None,
        gt=0,
    )

    rule_id: StrictInt | None = Field(
        default=None,
        gt=0,
    )

    severity: ClinicalRuleSeverity | None = None

    action: ClinicalRuleAction | None = None

    status: ClinicalAlertStatus | None = None

    source_type: str | None = Field(
        default=None,
        max_length=100,
    )

    source_id: StrictInt | None = Field(
        default=None,
        gt=0,
    )

    @field_validator(
        "page", "per_page", "patient_id", "rule_id", "source_id",
        mode="before",
    )
    @classmethod
    def parse_http_query_integer(cls, value):
        # Flask request.args contains strings; preserve StrictInt for the
        # resulting model and for non-string callers (notably bool/float).
        if not isinstance(value, str):
            return value
        if (
            not value
            or len(value) > 18
            or not value.isascii()
            or not value.isdecimal()
        ):
            raise ValueError(
                "Query integer must contain only ASCII decimal digits"
            )
        return int(value)

    model_config = ConfigDict(
        extra="forbid",
    )