
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
    ClinicalOrderType,
)


class ClinicalOrderSummarySchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    order_type: ClinicalOrderType

    source_entity_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
    )
    source_entity_id: int = Field(
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
    admission_id: int | None = Field(
        default=None,
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
    ordered_at: datetime

    priority: ClinicalOrderPriority = (
        ClinicalOrderPriority.ROUTINE
    )
    status: ClinicalOrderLifecycleStatus

    source_status: str = Field(
        ...,
        min_length=1,
        max_length=64,
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )

    instructions: str | None = Field(
        default=None,
        max_length=4000,
    )

    scheduled_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None

    details: dict[str, Any] = Field(
        default_factory=dict,
    )

    @field_validator(
        "instructions",
        mode="before",
    )
    @classmethod
    def normalize_optional_instructions(
        cls,
        value,
    ):
        if value is None:
            return None

        if not isinstance(
            value,
            str,
        ):
            return value

        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_terminal_state(
        self,
    ):
        if (
            self.completed_at is not None
            and self.cancelled_at is not None
        ):
            raise ValueError(
                "completed_at and cancelled_at "
                "cannot both be set"
            )

        if (
            self.status
            == ClinicalOrderLifecycleStatus.COMPLETED
        ):
            if self.completed_at is None:
                raise ValueError(
                    "completed status requires completed_at"
                )

        elif (
            self.status
            == ClinicalOrderLifecycleStatus.CANCELLED
        ):
            if self.cancelled_at is None:
                raise ValueError(
                    "cancelled status requires cancelled_at"
                )

        else:
            if (
                self.completed_at is not None
                or self.cancelled_at is not None
            ):
                raise ValueError(
                    "non-terminal status cannot carry "
                    "terminal timestamps"
                )

        return self


class ClinicalOrderListResponseSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    items: list[
        ClinicalOrderSummarySchema
    ] = Field(
        default_factory=list,
    )

    total: int = Field(
        ...,
        ge=0,
    )

    @model_validator(mode="after")
    def validate_total(
        self,
    ):
        if self.total != len(
            self.items
        ):
            raise ValueError(
                "total must equal the number of items"
            )

        return self
