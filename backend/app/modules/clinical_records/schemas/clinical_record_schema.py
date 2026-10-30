from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)


class _RequestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class _ResponseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
    )


class ClinicalRecordAmendmentCreateSchema(_RequestModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )

    new_content: dict[str, Any]

    @field_validator("new_content")
    @classmethod
    def validate_new_content(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        if not value:
            raise ValueError(
                "new_content must not be empty"
            )

        return value


class ClinicalRecordAmendmentResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)

    source_record_type: ClinicalRecordType
    source_record_id: int = Field(..., gt=0)

    previous_version: int = Field(..., ge=1)
    new_version: int = Field(..., ge=2)

    previous_amendment_id: int | None = Field(
        default=None,
        gt=0,
    )

    amended_by_staff_id: int = Field(..., gt=0)

    reason: str
    new_content: dict[str, Any]
    new_content_sha256: str = Field(
        ...,
        min_length=64,
        max_length=64,
    )

    amended_at: datetime
    created_at: datetime


class ClinicalRecordStateSchema(_ResponseModel):
    record_type: ClinicalRecordType
    source_record_id: int = Field(..., gt=0)
    status: ClinicalRecordStatus
    version: int = Field(..., ge=1)
    finalized_by_staff_id: int | None = Field(
        default=None,
        gt=0,
    )
    finalized_at: datetime | None = None
    latest_amendment_id: int | None = Field(
        default=None,
        gt=0,
    )
