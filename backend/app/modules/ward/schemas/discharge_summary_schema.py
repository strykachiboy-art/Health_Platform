from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums.clinical_record_enums import ClinicalRecordStatus


class _RequestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class DischargeSummaryDocumentSchema(_RequestModel):
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


class DischargeSummaryAmendmentSchema(_RequestModel):
    expected_version: int = Field(..., ge=1)
    reason: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )
    document: DischargeSummaryDocumentSchema


class DischargeSummaryResponseSchema(BaseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    medication_reconciliation_id: int = Field(..., gt=0)
    discharged_by_staff_id: int = Field(..., gt=0)

    primary_diagnosis: str
    secondary_diagnoses: list[str]
    condition_at_discharge: str
    instructions: str
    follow_up_plan: str
    final_medication_list: list[dict]
    destination: str
    discharged_at: datetime

    record_status: ClinicalRecordStatus
    record_version: int = Field(..., ge=1)
    finalized_by_staff_id: int | None = Field(
        default=None,
        gt=0,
    )
    finalized_at: datetime | None = None
    latest_amendment_id: int | None = Field(
        default=None,
        gt=0,
    )
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")
