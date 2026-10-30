from __future__ import annotations

from datetime import datetime, timezone
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.enums.patient_enums import (
    AllergyClinicalStatus,
    AllergySeverity,
    AllergyType,
    AllergyVerificationStatus,
)


REQUEST_CONFIG = ConfigDict(
    extra="forbid",
    use_enum_values=True,
    validate_default=True,
)


def _normalize_optional_text(
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

    return (
        value
        if value
        else None
    )


def _normalize_required_text(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        return value

    return value.strip()


def _normalize_timezone_aware(
    value: datetime | None,
    *,
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

    return value.astimezone(
        timezone.utc
    )


class PatientAllergyCreateSchema(BaseModel):
    allergen_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )

    allergen_code: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )

    allergen_type: AllergyType = (
        AllergyType.OTHER
    )

    reaction: str | None = Field(
        default=None,
        max_length=500,
    )

    severity: AllergySeverity = (
        AllergySeverity.UNKNOWN
    )

    onset_at: datetime | None = None

    notes: str | None = Field(
        default=None,
        max_length=1000,
    )

    @field_validator(
        "allergen_name",
        mode="before",
    )
    @classmethod
    def normalize_allergen_name(
        cls,
        value,
    ):
        return _normalize_required_text(
            value
        )

    @field_validator(
        "allergen_code",
        "reaction",
        "notes",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(
        cls,
        value,
    ):
        return _normalize_optional_text(
            value
        )

    @field_validator(
        "onset_at",
    )
    @classmethod
    def validate_onset_at(
        cls,
        value,
    ):
        return _normalize_timezone_aware(
            value,
            field_name="onset_at",
        )

    model_config = REQUEST_CONFIG


class PatientAllergyUpdateSchema(BaseModel):
    allergen_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    allergen_code: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )

    allergen_type: AllergyType | None = None

    reaction: str | None = Field(
        default=None,
        max_length=500,
    )

    severity: AllergySeverity | None = None

    onset_at: datetime | None = None

    notes: str | None = Field(
        default=None,
        max_length=1000,
    )

    @field_validator(
        "allergen_name",
        mode="before",
    )
    @classmethod
    def normalize_allergen_name(
        cls,
        value,
    ):
        if value is None:
            return None

        return _normalize_required_text(
            value
        )

    @field_validator(
        "allergen_code",
        "reaction",
        "notes",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(
        cls,
        value,
    ):
        return _normalize_optional_text(
            value
        )

    @field_validator(
        "onset_at",
    )
    @classmethod
    def validate_onset_at(
        cls,
        value,
    ):
        return _normalize_timezone_aware(
            value,
            field_name="onset_at",
        )

    @model_validator(
        mode="after",
    )
    def validate_update(
        self,
    ) -> Self:
        if not self.model_fields_set:
            raise ValueError(
                "At least one allergy field must be supplied"
            )

        if (
            "allergen_name"
            in self.model_fields_set
            and self.allergen_name is None
        ):
            raise ValueError(
                "allergen_name cannot be null"
            )

        if (
            "allergen_type"
            in self.model_fields_set
            and self.allergen_type is None
        ):
            raise ValueError(
                "allergen_type cannot be null"
            )

        return self

    model_config = REQUEST_CONFIG


class PatientAllergyStatusTransitionSchema(
    BaseModel
):
    clinical_status: (
        AllergyClinicalStatus
        | None
    ) = None

    verification_status: (
        AllergyVerificationStatus
        | None
    ) = None

    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
    )

    @field_validator(
        "reason",
        mode="before",
    )
    @classmethod
    def normalize_reason(
        cls,
        value,
    ):
        return _normalize_required_text(
            value
        )

    @model_validator(
        mode="after",
    )
    def validate_transition(
        self,
    ) -> Self:
        if (
            self.clinical_status is None
            and self.verification_status is None
        ):
            raise ValueError(
                "At least one allergy status must change"
            )

        return self

    model_config = REQUEST_CONFIG


class PatientAllergyResponseSchema(
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

    allergen_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )

    allergen_code: str | None = None

    allergen_type: AllergyType

    reaction: str | None = None

    severity: AllergySeverity

    clinical_status: (
        AllergyClinicalStatus
    )

    verification_status: (
        AllergyVerificationStatus
    )

    onset_at: datetime | None = None

    recorded_at: datetime

    recorded_by_id: int | None = Field(
        default=None,
        gt=0,
    )

    notes: str | None = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        use_enum_values=True,
    )


class PatientAllergyListQuerySchema(
    BaseModel
):
    active_only: bool = False
    include_refuted: bool = True

    page: int = Field(
        default=1,
        ge=1,
    )

    per_page: int = Field(
        default=50,
        ge=1,
        le=500,
    )

    model_config = REQUEST_CONFIG


class PatientAllergyListResponseSchema(
    BaseModel
):
    items: list[
        PatientAllergyResponseSchema
    ]

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
    )

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )
