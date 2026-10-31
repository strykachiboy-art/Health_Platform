from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.enums.ward_enums import AdmissionStatus


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


class PaginationSchema(BaseModel):
    page: int = Field(
        default=DEFAULT_PAGE,
        ge=1,
    )

    per_page: int = Field(
        default=DEFAULT_PER_PAGE,
        ge=1,
        le=MAX_PER_PAGE,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class PaginationResponseSchema(BaseModel):
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


class AdmissionCreateSchema(BaseModel):
    patient_id: int = Field(
        ...,
        gt=0,
    )

    bed_id: int = Field(
        ...,
        gt=0,
    )

    reason: str | None = Field(
        default=None,
        max_length=255,
    )

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        str_strip_whitespace=True,
    )

    @field_validator("reason")
    @classmethod
    def normalize_reason(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        return value or None


class AdmissionFromReservationSchema(BaseModel):
    reason: str | None = Field(
        default=None,
        max_length=255,
    )

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        str_strip_whitespace=True,
    )

    @field_validator("reason")
    @classmethod
    def normalize_reason(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        return value or None


class AdmissionDischargeSchema(BaseModel):
    reason: str | None = Field(
        default=None,
        max_length=255,
    )
    primary_diagnosis: str = Field(
        ...,
        min_length=1,
        max_length=2000,
    )
    secondary_diagnoses: list[str] = Field(
        default_factory=list,
        max_length=100,
    )
    condition_at_discharge: str = Field(
        ...,
        min_length=1,
        max_length=5000,
    )
    instructions: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )
    follow_up_plan: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )
    destination: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        str_strip_whitespace=True,
    )

    @field_validator("reason")
    @classmethod
    def normalize_reason(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        return value or None


class AdmissionTransferSchema(BaseModel):
    to_bed_id: int = Field(
        ...,
        gt=0,
    )

    reason: str | None = Field(
        default=None,
        max_length=255,
    )

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        str_strip_whitespace=True,
    )

    @field_validator("reason")
    @classmethod
    def normalize_reason(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        return value or None


class AdmissionListQuerySchema(
    PaginationSchema
):
    model_config = ConfigDict(
        extra="forbid",
    )


class AdmissionResponseSchema(BaseModel):
    id: int = Field(
        ...,
        gt=0,
    )

    patient_id: int = Field(
        ...,
        gt=0,
    )

    bed_id: int = Field(
        ...,
        gt=0,
    )

    admitted_by_id: int = Field(
        ...,
        gt=0,
    )

    reservation_id: int | None = Field(
        default=None,
        gt=0,
    )

    status: AdmissionStatus

    reason: str | None = Field(
        default=None,
        max_length=255,
    )

    admitted_at: datetime | None = None

    discharged_at: datetime | None = None

    created_at: datetime | None = None

    updated_at: datetime | None = None

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
    )


class AdmissionListResponseSchema(
    PaginationResponseSchema
):
    items: list[AdmissionResponseSchema] = Field(
        default_factory=list,
    )

    model_config = ConfigDict(
        extra="forbid",
    )