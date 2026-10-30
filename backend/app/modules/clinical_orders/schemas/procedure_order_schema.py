from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
)


_PROCEDURE_TYPES = {
    "diagnostic",
    "therapeutic",
    "surgical",
    "bedside",
    "interventional",
    "other",
}


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


class ProcedureOrderCreateSchema(_RequestModel):
    admission_id: int = Field(..., gt=0)
    consultation_id: int | None = Field(default=None, gt=0)
    procedure_code: str | None = Field(default=None, max_length=64)
    procedure_name: str = Field(..., min_length=1, max_length=255)
    procedure_type: str = Field(..., min_length=1, max_length=32)
    body_site: str | None = Field(default=None, max_length=255)
    laterality: str | None = Field(default=None, max_length=32)
    indication: str = Field(..., min_length=1, max_length=10000)
    prep_instructions: str | None = Field(default=None, max_length=10000)
    anesthesia_type: str | None = Field(default=None, max_length=128)
    consent_required: bool = False
    priority: ClinicalOrderPriority = ClinicalOrderPriority.ROUTINE
    scheduled_at: datetime | None = None

    @field_validator("procedure_type")
    @classmethod
    def validate_procedure_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in _PROCEDURE_TYPES:
            raise ValueError("Invalid procedure type")
        return value


class ProcedureOrderCancelSchema(_RequestModel):
    reason: str = Field(..., min_length=1, max_length=10000)


class ProcedureOrderCompleteSchema(_RequestModel):
    procedure_note: str = Field(..., min_length=1, max_length=20000)
    outcome_summary: str | None = Field(default=None, max_length=10000)
    performed_at: datetime | None = None


class ProcedureOrderResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    consultation_id: int | None = Field(default=None, gt=0)

    ordered_by_staff_id: int = Field(..., gt=0)
    completed_by_staff_id: int | None = Field(default=None, gt=0)
    cancelled_by_staff_id: int | None = Field(default=None, gt=0)
    consent_verified_by_staff_id: int | None = Field(default=None, gt=0)

    procedure_code: str | None = None
    procedure_name: str
    procedure_type: str
    body_site: str | None = None
    laterality: str | None = None
    indication: str
    prep_instructions: str | None = None
    anesthesia_type: str | None = None

    consent_required: bool
    consent_verified_at: datetime | None = None

    priority: ClinicalOrderPriority
    status: ClinicalOrderLifecycleStatus

    scheduled_at: datetime | None = None
    ordered_at: datetime
    performed_at: datetime | None = None

    outcome_summary: str | None = None
    procedure_note: str | None = None

    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None

    version: int = Field(..., ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProcedureOrderListResponseSchema(_ResponseModel):
    items: list[ProcedureOrderResponseSchema] = Field(default_factory=list)
    total: int = Field(..., ge=0)
