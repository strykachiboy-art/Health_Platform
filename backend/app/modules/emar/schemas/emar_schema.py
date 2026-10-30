from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


def _validate_aware_datetime(
    value: datetime | None,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None

    if (
        value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(
            f"{field_name} must include timezone information"
        )

    return value


class _RequestBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class MedicationAdministrationScheduleSchema(
    _RequestBase
):
    patient_id: int = Field(
        ...,
        gt=0,
    )
    admission_id: int = Field(
        ...,
        gt=0,
    )
    prescription_item_id: int = Field(
        ...,
        gt=0,
    )
    scheduled_at: datetime

    @field_validator(
        "scheduled_at"
    )
    @classmethod
    def validate_scheduled_at(
        cls,
        value: datetime,
    ) -> datetime:
        return _validate_aware_datetime(
            value,
            "scheduled_at",
        )


class MedicationAdministrationPrnSchema(
    _RequestBase
):
    patient_id: int = Field(
        ...,
        gt=0,
    )
    admission_id: int = Field(
        ...,
        gt=0,
    )
    prescription_item_id: int = Field(
        ...,
        gt=0,
    )
    administered_at: datetime | None = None
    notes: str | None = Field(
        default=None,
        max_length=4000,
    )

    @field_validator(
        "administered_at"
    )
    @classmethod
    def validate_administered_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _validate_aware_datetime(
            value,
            "administered_at",
        )


class MedicationAdministrationDueSchema(
    _RequestBase
):
    due_at: datetime | None = None

    @field_validator(
        "due_at"
    )
    @classmethod
    def validate_due_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _validate_aware_datetime(
            value,
            "due_at",
        )


class MedicationAdministrationAdministerSchema(
    _RequestBase
):
    administered_at: datetime | None = None
    notes: str | None = Field(
        default=None,
        max_length=4000,
    )

    @field_validator(
        "administered_at"
    )
    @classmethod
    def validate_administered_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _validate_aware_datetime(
            value,
            "administered_at",
        )


class MedicationAdministrationHoldSchema(
    _RequestBase
):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
    )
    held_at: datetime | None = None

    @field_validator(
        "held_at"
    )
    @classmethod
    def validate_held_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _validate_aware_datetime(
            value,
            "held_at",
        )


class MedicationAdministrationRefusalSchema(
    _RequestBase
):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
    )
    refused_at: datetime | None = None

    @field_validator(
        "refused_at"
    )
    @classmethod
    def validate_refused_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _validate_aware_datetime(
            value,
            "refused_at",
        )


class MedicationAdministrationMissedSchema(
    _RequestBase
):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
    )
    missed_at: datetime | None = None

    @field_validator(
        "missed_at"
    )
    @classmethod
    def validate_missed_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _validate_aware_datetime(
            value,
            "missed_at",
        )


class MedicationAdministrationCancelSchema(
    _RequestBase
):
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
    )
    cancelled_at: datetime | None = None

    @field_validator(
        "cancelled_at"
    )
    @classmethod
    def validate_cancelled_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _validate_aware_datetime(
            value,
            "cancelled_at",
        )


class MedicationAdministrationListQuerySchema(
    _RequestBase
):
    status: MedicationAdministrationStatus | None = None
    page: int = Field(
        default=DEFAULT_PAGE,
        ge=1,
    )
    per_page: int = Field(
        default=DEFAULT_PER_PAGE,
        ge=1,
        le=MAX_PER_PAGE,
    )


class MedicationAdministrationResponseSchema(
    BaseModel
):
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
    prescription_id: int = Field(
        ...,
        gt=0,
    )
    prescription_item_id: int = Field(
        ...,
        gt=0,
    )
    scheduled_at: datetime | None = None

    dose_value: Decimal | None = None
    dose_unit: str | None = None
    route_code: str | None = None

    status: MedicationAdministrationStatus

    administered_at: datetime | None = None
    administered_by_staff_id: int | None = Field(
        default=None,
        gt=0,
    )

    held_at: datetime | None = None
    held_by_staff_id: int | None = Field(
        default=None,
        gt=0,
    )
    hold_reason: str | None = None

    refused_at: datetime | None = None
    refused_recorded_by_staff_id: int | None = Field(
        default=None,
        gt=0,
    )
    refusal_reason: str | None = None

    missed_at: datetime | None = None
    missed_reason: str | None = None

    cancelled_at: datetime | None = None
    cancel_reason: str | None = None

    source_order_version: int | None = Field(
        default=None,
        gt=0,
    )
    idempotency_key: str | None = None

    notes: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
    )


class MedicationAdministrationListResponseSchema(
    BaseModel
):
    items: list[
        MedicationAdministrationResponseSchema
    ] = Field(
        default_factory=list,
    )
    total: int = Field(
        ...,
        ge=0,
    )
    page: int = Field(
        ...,
        ge=1,
    )
    per_page: int = Field(
        ...,
        ge=1,
        le=MAX_PER_PAGE,
    )

    model_config = ConfigDict(
        extra="forbid",
    )
