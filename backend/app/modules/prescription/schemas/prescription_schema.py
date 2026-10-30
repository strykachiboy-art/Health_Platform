from __future__ import annotations

from datetime import datetime, timezone

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.enums.prescription_enums import (
    DrugInteractionSeverity,
    MedicationIntervalUnit,
    MedicationOrderPriority,
    MedicationOrderType,
)


# =====================================================================
# Pagination
# =====================================================================

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


class PaginationSchema(BaseModel):
    """
    Shared pagination request schema.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    page: int = Field(
        default=DEFAULT_PAGE,
        ge=1,
        description="1-based page number",
    )

    per_page: int = Field(
        default=DEFAULT_PER_PAGE,
        ge=1,
        le=MAX_PER_PAGE,
        description=f"Number of records per page (maximum {MAX_PER_PAGE})",
    )


class PaginationResponseSchema(BaseModel):
    """
    Shared pagination response schema.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    items: list
    total: int = Field(
        ge=0,
    )
    page: int = Field(
        ge=1,
    )
    per_page: int = Field(
        ge=1,
        le=MAX_PER_PAGE,
    )


# =====================================================================
# Prescription Item
# =====================================================================

class PrescriptionItemSchema(BaseModel):
    """
    Request schema for an individual medication order.

    Legacy human-readable fields remain supported while structured
    medication semantics are introduced for CDS/eMAR workflows.
    """

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
        validate_default=True,
    )

    drug_id: int = Field(
        ...,
        gt=0,
        description="ID of the prescribed drug",
    )

    # -------------------------------------------------------------
    # Legacy / display fields
    # -------------------------------------------------------------

    dosage: str | None = Field(
        default=None,
        max_length=255,
        description="Legacy human-readable medication dosage",
    )

    frequency: str | None = Field(
        default=None,
        max_length=255,
        description="Legacy human-readable medication frequency",
    )

    duration: str | None = Field(
        default=None,
        max_length=255,
        description="Legacy human-readable medication duration",
    )

    quantity: int | None = Field(
        default=None,
        gt=0,
        description="Quantity of medication to dispense",
    )

    instructions: str | None = Field(
        default=None,
        max_length=1000,
        description="Legacy/display patient instructions",
    )

    # -------------------------------------------------------------
    # Structured medication semantics
    # -------------------------------------------------------------

    dose_value: float | None = Field(
        default=None,
        gt=0,
        description="Structured single-dose numeric value",
    )

    dose_unit: str | None = Field(
        default=None,
        max_length=32,
        description="Structured dose unit/code",
    )

    route_code: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
        description="Normalized medication administration route code",
    )

    route_display: str | None = Field(
        default=None,
        max_length=128,
        description="Human-readable route display",
    )

    frequency_code: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        description="Structured frequency code",
    )

    frequency_interval: int | None = Field(
        default=None,
        gt=0,
        description="Structured repeating interval",
    )

    frequency_interval_unit: MedicationIntervalUnit | None = Field(
        default=None,
        description="Unit for the repeating frequency interval",
    )

    scheduled_times: list[str] = Field(
        default_factory=list,
        max_length=24,
        description="Scheduled local administration times in HH:MM format",
    )

    start_at: datetime | None = Field(
        default=None,
        description="Medication order start timestamp",
    )

    end_at: datetime | None = Field(
        default=None,
        description="Medication order end timestamp",
    )

    is_prn: bool = Field(
        default=False,
        description="Whether medication is ordered as-needed",
    )

    prn_reason: str | None = Field(
        default=None,
        max_length=500,
        description="Clinical indication/reason for PRN use",
    )

    max_single_dose: float | None = Field(
        default=None,
        gt=0,
        description="Maximum permitted single dose",
    )

    max_daily_dose: float | None = Field(
        default=None,
        gt=0,
        description="Maximum permitted dose in 24 hours",
    )

    administration_instructions: str | None = Field(
        default=None,
        max_length=1000,
        description="Structured administration instructions",
    )

    order_priority: MedicationOrderPriority = Field(
        default=MedicationOrderPriority.ROUTINE,
        description="Medication order priority",
    )

    order_type: MedicationOrderType = Field(
        default=MedicationOrderType.OUTPATIENT,
        description="Medication order care-setting type",
    )

    @field_validator(
        "dosage",
        "frequency",
        "duration",
        "instructions",
        "dose_unit",
        "route_code",
        "route_display",
        "frequency_code",
        "prn_reason",
        "administration_instructions",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value):
        if value is None:
            return None

        if not isinstance(value, str):
            return value

        value = value.strip()

        return value or None

    @field_validator(
        "start_at",
        "end_at",
    )
    @classmethod
    def normalize_structured_datetime(
        cls,
        value,
    ):
        if value is None:
            return None

        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "Structured medication timestamps must include timezone information"
            )

        return value.astimezone(
            timezone.utc
        )

    @field_validator(
        "scheduled_times",
    )
    @classmethod
    def validate_scheduled_times(
        cls,
        value,
    ):
        normalized = []
        seen = set()

        for raw in value:
            if not isinstance(raw, str):
                raise ValueError(
                    "scheduled_times entries must be strings"
                )

            candidate = raw.strip()

            parts = candidate.split(":")

            if (
                len(parts) != 2
                or len(parts[0]) != 2
                or len(parts[1]) != 2
                or not parts[0].isdigit()
                or not parts[1].isdigit()
            ):
                raise ValueError(
                    "scheduled_times entries must use HH:MM format"
                )

            hour = int(parts[0])
            minute = int(parts[1])

            if (
                hour < 0
                or hour > 23
                or minute < 0
                or minute > 59
            ):
                raise ValueError(
                    "scheduled_times contains an invalid clock time"
                )

            normalized_value = (
                f"{hour:02d}:{minute:02d}"
            )

            if normalized_value in seen:
                raise ValueError(
                    "scheduled_times cannot contain duplicates"
                )

            seen.add(
                normalized_value
            )

            normalized.append(
                normalized_value
            )

        return normalized

    @model_validator(mode="after")
    def validate_structured_medication_contract(
        self,
    ):
        if (
            (self.dose_value is None)
            != (self.dose_unit is None)
        ):
            raise ValueError(
                "dose_value and dose_unit must be supplied together"
            )

        if (
            (self.frequency_interval is None)
            != (self.frequency_interval_unit is None)
        ):
            raise ValueError(
                "frequency_interval and frequency_interval_unit "
                "must be supplied together"
            )

        if (
            self.route_display is not None
            and self.route_code is None
        ):
            raise ValueError(
                "route_display requires route_code"
            )

        if self.is_prn:
            if self.prn_reason is None:
                raise ValueError(
                    "PRN medication requires prn_reason"
                )

            if self.scheduled_times:
                raise ValueError(
                    "PRN medication cannot define fixed scheduled_times"
                )
        elif self.prn_reason is not None:
            raise ValueError(
                "prn_reason requires is_prn=true"
            )

        if (
            self.max_single_dose is not None
            or self.max_daily_dose is not None
        ):
            if self.dose_unit is None:
                raise ValueError(
                    "Maximum dose limits require structured dose_value/dose_unit"
                )

        if (
            self.max_single_dose is not None
            and self.max_daily_dose is not None
            and self.max_daily_dose < self.max_single_dose
        ):
            raise ValueError(
                "max_daily_dose cannot be less than max_single_dose"
            )

        if (
            self.start_at is not None
            and self.end_at is not None
            and self.end_at < self.start_at
        ):
            raise ValueError(
                "end_at cannot be earlier than start_at"
            )

        return self


# =====================================================================
# Prescription Creation
# =====================================================================

class PrescriptionCreateSchema(BaseModel):
    """
    Request schema for creating a prescription.

    Clinic and prescriber identity are intentionally excluded from
    client-controlled payloads. The route must derive those values
    from the authenticated user/session context.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    patient_id: int = Field(
        ...,
        gt=0,
        description="Patient receiving the prescription",
    )

    consultation_id: int | None = Field(
        default=None,
        gt=0,
        description="Optional consultation associated with the prescription",
    )

    items: list[PrescriptionItemSchema] = Field(
        ...,
        min_length=1,
        description="Prescription medication items",
    )

    expires_at: datetime | None = Field(
        default=None,
        description="Optional prescription expiration timestamp",
    )

    notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Additional prescription notes",
    )

    clinical_safety_acknowledged: bool = Field(
        default=False,
        strict=True,
        description=(
            "Explicit acknowledgement of acknowledgement-required "
            "clinical safety rules"
        ),
    )

    clinical_safety_justification: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Clinical justification when a safety rule requires one"
        ),
    )

    @field_validator("items")
    @classmethod
    def validate_unique_drugs(cls, value):
        drug_ids = [
            item.drug_id
            for item in value
        ]

        if len(drug_ids) != len(
            set(drug_ids)
        ):
            raise ValueError(
                "A drug cannot appear more than once in a prescription"
            )

        return value

    @field_validator(
        "clinical_safety_justification",
        mode="before",
    )
    @classmethod
    def normalize_clinical_safety_justification(
        cls,
        value,
    ):
        if value is None:
            return None

        if not isinstance(value, str):
            return value

        value = value.strip()

        return value or None

    @field_validator("expires_at")
    @classmethod
    def validate_expiration(cls, value):
        if value is None:
            return None

        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(
                "expires_at must include timezone information"
            )

        if value <= datetime.now(
            timezone.utc
        ):
            raise ValueError(
                "expires_at must be in the future"
            )

        return value.astimezone(
            timezone.utc
        )

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value):
        if value is None:
            return None

        if not isinstance(value, str):
            return value

        value = value.strip()

        return value or None


# =====================================================================
# Prescription Cancellation
# =====================================================================

class PrescriptionCancelSchema(BaseModel):
    """
    Request schema for cancelling a prescription.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    reason: str | None = Field(
        default=None,
        max_length=1000,
        description="Reason for cancelling the prescription",
    )

    @field_validator(
        "reason",
        mode="before",
    )
    @classmethod
    def normalize_reason(cls, value):
        if value is None:
            return None

        if not isinstance(value, str):
            return value

        value = value.strip()

        return value or None


# =====================================================================
# Prescription List Query
# =====================================================================

class PrescriptionListQuerySchema(PaginationSchema):
    """
    Query parameters for clinic-scoped patient prescription listings.
    """

    active_only: bool = Field(
        default=False,
        description="Return only active prescriptions",
    )


# =====================================================================
# Drug Interaction Creation
# =====================================================================

class DrugInteractionCreateSchema(BaseModel):
    """
    Request schema for creating a global drug interaction.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    drug_a_id: int = Field(
        ...,
        gt=0,
        description="First drug ID",
    )

    drug_b_id: int = Field(
        ...,
        gt=0,
        description="Second drug ID",
    )

    severity: DrugInteractionSeverity = Field(
        ...,
        description="Interaction severity",
    )

    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Description of the drug interaction",
    )

    @field_validator("drug_b_id")
    @classmethod
    def validate_different_drug(
        cls,
        value,
        info,
    ):
        drug_a_id = info.data.get(
            "drug_a_id"
        )

        if (
            drug_a_id is not None
            and value == drug_a_id
        ):
            raise ValueError(
                "A drug cannot interact with itself"
            )

        return value

    @field_validator(
        "description",
        mode="before",
    )
    @classmethod
    def normalize_description(cls, value):
        if value is None:
            return None

        if not isinstance(value, str):
            return value

        value = value.strip()

        return value or None


# =====================================================================
# Drug Interaction Check
# =====================================================================

class DrugInteractionCheckSchema(BaseModel):
    """
    Request schema for checking a list of drugs for known interactions.
    """

    model_config = ConfigDict(
        extra="forbid"
    )

    drug_ids: list[int] = Field(
        ...,
        min_length=2,
        description="Drug IDs to check for interactions",
    )

    @field_validator("drug_ids")
    @classmethod
    def validate_drug_ids(cls, value):
        if any(
            (
                not isinstance(drug_id, int)
                or isinstance(drug_id, bool)
                or drug_id <= 0
            )
            for drug_id in value
        ):
            raise ValueError(
                "All drug IDs must be greater than zero"
            )

        if len(value) != len(
            set(value)
        ):
            raise ValueError(
                "Duplicate drug IDs are not allowed"
            )

        return value