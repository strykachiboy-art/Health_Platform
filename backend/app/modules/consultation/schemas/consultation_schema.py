from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums.consultation_enums import ConsultationType


def _reject_blank(value: Optional[str]) -> Optional[str]:
    if value is not None and not value.strip():
        raise ValueError("Value cannot be blank")
    return value


class ConsultationStartSchema(BaseModel):
    patient_id: int = Field(..., gt=0)
    staff_id: int = Field(..., gt=0)
    appointment_id: Optional[int] = Field(None, gt=0)

    consultation_type: ConsultationType = Field(
        default=ConsultationType.GENERAL
    )

    template_id: Optional[int] = Field(None, gt=0)

    chief_complaint: Optional[str] = Field(
        None,
        max_length=2000,
    )

    symptoms: Optional[str] = Field(
        None,
        max_length=5000,
    )

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )

    _validate_chief_complaint = field_validator(
        "chief_complaint",
        mode="after",
    )(_reject_blank)

    _validate_symptoms = field_validator(
        "symptoms",
        mode="after",
    )(_reject_blank)


class ConsultationUpdateSchema(BaseModel):
    icd10_code: Optional[str] = Field(
        None,
        max_length=10,
    )

    chief_complaint: Optional[str] = Field(
        None,
        max_length=2000,
    )

    symptoms: Optional[str] = Field(
        None,
        max_length=5000,
    )

    diagnosis: Optional[str] = Field(
        None,
        max_length=5000,
    )

    treatment_plan: Optional[str] = Field(
        None,
        max_length=5000,
    )

    notes: Optional[str] = Field(
        None,
        max_length=10000,
    )

    voice_note_url: Optional[str] = Field(
        None,
        max_length=255,
    )

    transcribed_text: Optional[str] = Field(
        None,
        max_length=20000,
    )

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )

    _validate_icd10_code = field_validator(
        "icd10_code",
        mode="after",
    )(_reject_blank)

    _validate_chief_complaint = field_validator(
        "chief_complaint",
        mode="after",
    )(_reject_blank)

    _validate_symptoms = field_validator(
        "symptoms",
        mode="after",
    )(_reject_blank)

    _validate_diagnosis = field_validator(
        "diagnosis",
        mode="after",
    )(_reject_blank)

    _validate_treatment_plan = field_validator(
        "treatment_plan",
        mode="after",
    )(_reject_blank)

    _validate_notes = field_validator(
        "notes",
        mode="after",
    )(_reject_blank)

    _validate_voice_note_url = field_validator(
        "voice_note_url",
        mode="after",
    )(_reject_blank)

    _validate_transcribed_text = field_validator(
        "transcribed_text",
        mode="after",
    )(_reject_blank)


class ConsultationCompleteSchema(BaseModel):
    diagnosis: str = Field(
        ...,
        min_length=1,
        max_length=5000,
    )

    treatment_plan: Optional[str] = Field(
        None,
        max_length=5000,
    )

    notes: Optional[str] = Field(
        None,
        max_length=10000,
    )

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )

    @field_validator("diagnosis", mode="after")
    @classmethod
    def validate_diagnosis(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Diagnosis cannot be blank")
        return value

    _validate_treatment_plan = field_validator(
        "treatment_plan",
        mode="after",
    )(_reject_blank)

    _validate_notes = field_validator(
        "notes",
        mode="after",
    )(_reject_blank)


class ConsultationCancelSchema(BaseModel):
    reason: Optional[str] = Field(
        None,
        max_length=500,
    )

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )

    @field_validator("reason", mode="after")
    @classmethod
    def validate_reason(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError(
                "Cancellation reason cannot be blank"
            )
        return value


class ConsultationTemplateCreateSchema(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=150,
    )

    specialty: Optional[str] = Field(
        None,
        max_length=100,
    )

    structure: dict[str, Any] = Field(...)

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )

    @field_validator("name", mode="after")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Template name cannot be blank")
        return value

    @field_validator("specialty", mode="after")
    @classmethod
    def validate_specialty(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Specialty cannot be blank")
        return value

    @field_validator("structure", mode="after")
    @classmethod
    def validate_structure(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        if not value:
            raise ValueError(
                "Template structure cannot be empty"
            )
        return value


class PatientsSeenByStaffQuerySchema(BaseModel):
    page: int = Field(
        default=1,
        ge=1,
    )

    per_page: int = Field(
        default=50,
        ge=1,
        le=500,
    )

    model_config = ConfigDict(
        extra="forbid",
    )

class ConsultationFinalizeSchema(BaseModel):
    expected_version: int = Field(
        ...,
        ge=1,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class ConsultationDocumentSchema(BaseModel):
    icd10_code: Optional[str] = Field(
        ...,
        max_length=10,
    )

    chief_complaint: Optional[str] = Field(
        ...,
        max_length=2000,
    )

    symptoms: Optional[str] = Field(
        ...,
        max_length=5000,
    )

    diagnosis: Optional[str] = Field(
        ...,
        max_length=5000,
    )

    treatment_plan: Optional[str] = Field(
        ...,
        max_length=5000,
    )

    notes: Optional[str] = Field(
        ...,
        max_length=10000,
    )

    voice_note_url: Optional[str] = Field(
        ...,
        max_length=255,
    )

    transcribed_text: Optional[str] = Field(
        ...,
        max_length=20000,
    )

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    _validate_icd10_code = field_validator(
        "icd10_code",
        mode="after",
    )(_reject_blank)

    _validate_chief_complaint = field_validator(
        "chief_complaint",
        mode="after",
    )(_reject_blank)

    _validate_symptoms = field_validator(
        "symptoms",
        mode="after",
    )(_reject_blank)

    _validate_diagnosis = field_validator(
        "diagnosis",
        mode="after",
    )(_reject_blank)

    _validate_treatment_plan = field_validator(
        "treatment_plan",
        mode="after",
    )(_reject_blank)

    _validate_notes = field_validator(
        "notes",
        mode="after",
    )(_reject_blank)

    _validate_voice_note_url = field_validator(
        "voice_note_url",
        mode="after",
    )(_reject_blank)

    _validate_transcribed_text = field_validator(
        "transcribed_text",
        mode="after",
    )(_reject_blank)


class ConsultationAmendmentSchema(BaseModel):
    expected_version: int = Field(
        ...,
        ge=1,
    )

    reason: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )

    document: ConsultationDocumentSchema

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    @field_validator(
        "reason",
        mode="after",
    )
    @classmethod
    def validate_reason(
        cls,
        value: str,
    ) -> str:
        if not value.strip():
            raise ValueError(
                "Amendment reason cannot be blank"
            )

        return value
