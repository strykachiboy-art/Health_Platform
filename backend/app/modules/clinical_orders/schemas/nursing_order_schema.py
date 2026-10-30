from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
)


_CATEGORIES = {
    "monitoring",
    "activity",
    "positioning",
    "wound_care",
    "intake_output",
    "safety",
    "specimen",
    "respiratory",
    "catheter",
    "hygiene",
    "communication",
    "other",
}

_FREQUENCIES = {
    "once",
    "q1h",
    "q2h",
    "q4h",
    "q6h",
    "q8h",
    "q12h",
    "daily",
    "prn",
    "continuous",
    "custom",
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


class NursingOrderCreateSchema(_RequestModel):
    admission_id: int = Field(..., gt=0)
    consultation_id: int | None = Field(
        default=None,
        gt=0,
    )
    category: str = Field(
        ...,
        min_length=1,
        max_length=32,
    )
    order_text: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )
    instructions: str | None = Field(
        default=None,
        max_length=10000,
    )
    frequency: str = Field(
        ...,
        min_length=1,
        max_length=24,
    )
    custom_frequency: str | None = Field(
        default=None,
        max_length=255,
    )
    assigned_to_staff_id: int | None = Field(
        default=None,
        gt=0,
    )
    priority: ClinicalOrderPriority = (
        ClinicalOrderPriority.ROUTINE
    )
    scheduled_at: datetime | None = None

    @model_validator(mode="after")
    def validate_order(self):
        self.category = (
            self.category
            .strip()
            .lower()
        )

        if self.category not in _CATEGORIES:
            raise ValueError(
                "Invalid category"
            )

        self.frequency = (
            self.frequency
            .strip()
            .lower()
        )

        if self.frequency not in _FREQUENCIES:
            raise ValueError(
                "Invalid frequency"
            )

        if self.frequency == "custom":
            if not self.custom_frequency:
                raise ValueError(
                    "custom_frequency is required "
                    "for custom frequency"
                )
        elif self.custom_frequency is not None:
            raise ValueError(
                "custom_frequency is only allowed "
                "for custom frequency"
            )

        return self


class NursingOrderExecuteSchema(_RequestModel):
    result_note: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )
    performed_at: datetime | None = None


class NursingOrderCompleteSchema(_RequestModel):
    completion_note: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )


class NursingOrderCancelSchema(_RequestModel):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )


class NursingOrderExecutionResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    nursing_order_id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    performed_by_staff_id: int = Field(..., gt=0)
    performed_at: datetime
    result_note: str
    created_at: datetime | None = None


class NursingOrderExecutionListResponseSchema(_ResponseModel):
    items: list[
        NursingOrderExecutionResponseSchema
    ] = Field(
        default_factory=list,
    )
    total: int = Field(..., ge=0)


class NursingOrderResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    consultation_id: int | None = Field(
        default=None,
        gt=0,
    )

    ordered_by_staff_id: int = Field(..., gt=0)
    assigned_to_staff_id: int | None = Field(
        default=None,
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

    category: str
    order_text: str
    instructions: str | None = None
    frequency: str
    custom_frequency: str | None = None

    priority: ClinicalOrderPriority
    status: ClinicalOrderLifecycleStatus

    scheduled_at: datetime | None = None
    ordered_at: datetime

    completion_note: str | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None

    version: int = Field(..., ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NursingOrderListResponseSchema(_ResponseModel):
    items: list[
        NursingOrderResponseSchema
    ] = Field(
        default_factory=list,
    )
    total: int = Field(..., ge=0)
