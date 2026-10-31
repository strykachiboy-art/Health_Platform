from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
)
from app.core.enums.medication_reconciliation_enums import (
    MedicationReconciliationDecision,
    MedicationReconciliationEvent,
)


class _RequestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class MedicationReconciliationItemInputSchema(
    _RequestModel
):
    source_prescription_item_id: int | None = Field(
        default=None,
        gt=0,
    )
    medication_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )
    decision: MedicationReconciliationDecision
    replacement_medication_name: str | None = Field(
        default=None,
        max_length=255,
    )
    dose_text: str | None = Field(
        default=None,
        max_length=255,
    )
    route_text: str | None = Field(
        default=None,
        max_length=128,
    )
    frequency_text: str | None = Field(
        default=None,
        max_length=255,
    )
    instructions: str | None = Field(
        default=None,
        max_length=1000,
    )
    rationale: str | None = Field(
        default=None,
        max_length=1000,
    )


class MedicationReconciliationCreateSchema(
    _RequestModel
):
    admission_id: int = Field(
        ...,
        gt=0,
    )
    event_type: MedicationReconciliationEvent
    ward_transfer_id: int | None = Field(
        default=None,
        gt=0,
    )
    no_medications_reported: bool = False
    notes: str | None = Field(
        default=None,
        max_length=10000,
    )
    items: list[
        MedicationReconciliationItemInputSchema
    ] = Field(
        default_factory=list,
        max_length=500,
    )


class MedicationReconciliationUpdateSchema(
    _RequestModel
):
    no_medications_reported: bool
    notes: str | None = Field(
        default=None,
        max_length=10000,
    )
    items: list[
        MedicationReconciliationItemInputSchema
    ] = Field(
        default_factory=list,
        max_length=500,
    )


class MedicationReconciliationFinalizeSchema(
    _RequestModel
):
    expected_version: int = Field(
        ...,
        ge=1,
    )


class MedicationReconciliationDocumentSchema(
    MedicationReconciliationUpdateSchema
):
    pass


class MedicationReconciliationAmendmentSchema(
    _RequestModel
):
    expected_version: int = Field(
        ...,
        ge=1,
    )
    reason: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )
    document: MedicationReconciliationDocumentSchema


class MedicationReconciliationItemResponseSchema(
    BaseModel
):
    source_prescription_item_id: int | None = None
    medication_name: str
    decision: MedicationReconciliationDecision
    replacement_medication_name: str | None = None
    dose_text: str | None = None
    route_text: str | None = None
    frequency_text: str | None = None
    instructions: str | None = None
    rationale: str | None = None

    model_config = ConfigDict(
        extra="forbid",
    )


class MedicationReconciliationResponseSchema(
    BaseModel
):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    ward_transfer_id: int | None = Field(
        default=None,
        gt=0,
    )
    event_type: MedicationReconciliationEvent
    event_identity: str
    created_by_staff_id: int = Field(..., gt=0)
    no_medications_reported: bool
    notes: str | None = None
    items: list[
        MedicationReconciliationItemResponseSchema
    ]
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

    model_config = ConfigDict(
        extra="forbid",
    )
