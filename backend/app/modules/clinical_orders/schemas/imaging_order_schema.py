
from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
)


_MODALITIES = {
    "xray",
    "ct",
    "mri",
    "ultrasound",
    "mammography",
    "fluoroscopy",
    "nuclear_medicine",
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


class ImagingOrderCreateSchema(_RequestModel):
    admission_id: int = Field(
        ...,
        gt=0,
    )

    consultation_id: int | None = Field(
        default=None,
        gt=0,
    )

    study_code: str | None = Field(
        default=None,
        max_length=64,
    )

    study_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )

    modality: str = Field(
        ...,
        min_length=1,
        max_length=32,
    )

    body_site: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )

    laterality: str | None = Field(
        default=None,
        max_length=32,
    )

    contrast_required: bool = False

    contrast_type: str | None = Field(
        default=None,
        max_length=128,
    )

    indication: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )

    instructions: str | None = Field(
        default=None,
        max_length=10000,
    )

    priority: ClinicalOrderPriority = (
        ClinicalOrderPriority.ROUTINE
    )

    scheduled_at: datetime | None = None

    @field_validator("modality")
    @classmethod
    def validate_modality(
        cls,
        value: str,
    ) -> str:
        value = value.strip().lower()

        if value not in _MODALITIES:
            raise ValueError(
                "Invalid imaging modality"
            )

        return value


class ImagingOrderCancelSchema(_RequestModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )


class ImagingOrderCompleteSchema(_RequestModel):
    report_text: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )

    result_summary: str | None = Field(
        default=None,
        max_length=10000,
    )

    performed_at: datetime | None = None


class ImagingOrderResponseSchema(_ResponseModel):
    id: int = Field(
        ...,
        gt=0,
    )

    clinic_id: int = Field(
        ...,
        gt=0,
    )

    patient_id: int = Field(
        ...,
        gt=0,
    )

    admission_id: int = Field(
        ...,
        gt=0,
    )

    consultation_id: int | None = Field(
        default=None,
        gt=0,
    )

    ordered_by_staff_id: int = Field(
        ...,
        gt=0,
    )

    completed_by_staff_id: int | None = Field(
        default=None,
        gt=0,
    )

    cancelled_by_staff_id: int | None = Field(
        default=None,
        gt=0,
    )

    study_code: str | None = None
    study_name: str
    modality: str
    body_site: str
    laterality: str | None = None
    contrast_required: bool
    contrast_type: str | None = None
    indication: str
    instructions: str | None = None

    priority: ClinicalOrderPriority
    status: ClinicalOrderLifecycleStatus

    scheduled_at: datetime | None = None
    ordered_at: datetime
    performed_at: datetime | None = None

    result_summary: str | None = None
    report_text: str | None = None

    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None

    version: int = Field(
        ...,
        ge=1,
    )

    created_at: datetime | None = None
    updated_at: datetime | None = None


class ImagingOrderListResponseSchema(_ResponseModel):
    items: list[
        ImagingOrderResponseSchema
    ] = Field(
        default_factory=list,
    )

    total: int = Field(
        ...,
        ge=0,
    )
