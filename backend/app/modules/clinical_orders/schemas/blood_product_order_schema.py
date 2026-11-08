from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
)
from app.core.enums.patient_enums import BloodType


_PRODUCT_TYPES = {
    "packed_red_blood_cells",
    "whole_blood",
    "platelets",
    "fresh_frozen_plasma",
    "cryoprecipitate",
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


class BloodProductOrderCreateSchema(_RequestModel):
    admission_id: int = Field(..., gt=0)
    consultation_id: int | None = Field(default=None, gt=0)
    product_type: str = Field(..., min_length=1, max_length=40)
    units_requested: int = Field(..., gt=0, le=100)
    indication: str = Field(..., min_length=1, max_length=10000)
    special_requirements: str | None = Field(
        default=None,
        max_length=10000,
    )
    priority: ClinicalOrderPriority = ClinicalOrderPriority.ROUTINE
    scheduled_at: datetime | None = None
    # Doctor must explicitly request this exceptional, disabled-by-default path.
    emergency_release_requested: bool = False
    emergency_request_reason: str | None = Field(default=None, max_length=10000)
    emergency_risk_acknowledged: bool = False

    @field_validator("product_type")
    @classmethod
    def validate_product_type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in _PRODUCT_TYPES:
            raise ValueError("Invalid blood product type")
        return value


class BloodProductCompatibilitySchema(_RequestModel):
    emergency_release: bool = False
    crossmatch_reference: str | None = Field(
        default=None,
        max_length=128,
    )
    product_blood_type: BloodType | None = None
    notes: str | None = Field(default=None, max_length=10000)
    emergency_release_reason: str | None = Field(
        default=None,
        max_length=10000,
    )


class BloodProductIssuedUnitResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    order_id: int = Field(..., gt=0)
    unit_identifier: str = Field(..., min_length=1, max_length=128)
    product_blood_type: str
    issued_by_staff_id: int = Field(..., gt=0)
    issued_at: datetime


class BloodProductLabFollowupRecordSchema(_RequestModel):
    lab_order_item_id: int = Field(..., gt=0)
    outcome: str = Field(..., pattern="^(evidence_recorded|discrepancy_escalated)$")
    evidence_note: str = Field(..., min_length=1, max_length=10000)
    escalation_note: str | None = Field(default=None, max_length=10000)


class BloodProductLabFollowupResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    order_id: int = Field(..., gt=0)
    issued_unit_id: int = Field(..., gt=0)
    status: str
    lab_order_item_id: int | None = Field(default=None, gt=0)
    recorded_by_staff_id: int | None = Field(default=None, gt=0)
    recorded_at: datetime | None = None
    evidence_note: str | None = None
    escalation_note: str | None = None
    created_at: datetime | None = None


class BloodProductEmergencyReviewSchema(_RequestModel):
    outcome: str = Field(..., pattern="^(reviewed|escalated)$")
    note: str = Field(..., min_length=1, max_length=10000)


class BloodProductIssueUnitSchema(_RequestModel):
    unit_identifier: str = Field(..., min_length=1, max_length=128)
    product_blood_type: BloodType


class BloodProductFulfillSchema(_RequestModel):
    units_issued: int = Field(..., gt=0, le=100)
    issued_units: list[BloodProductIssueUnitSchema] | None = Field(default=None, max_length=100)
    fulfillment_note: str = Field(
        ...,
        min_length=1,
        max_length=10000,
    )


class BloodProductCancelSchema(_RequestModel):
    reason: str = Field(..., min_length=1, max_length=10000)


class BloodProductOrderResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    consultation_id: int | None = Field(default=None, gt=0)

    ordered_by_staff_id: int = Field(..., gt=0)
    compatibility_verified_by_staff_id: int | None = Field(
        default=None,
        gt=0,
    )
    fulfilled_by_staff_id: int | None = Field(default=None, gt=0)
    cancelled_by_staff_id: int | None = Field(default=None, gt=0)

    product_type: str
    units_requested: int = Field(..., gt=0)
    units_issued: int = Field(..., ge=0)

    indication: str
    special_requirements: str | None = None

    patient_blood_type_at_order: str
    patient_blood_type_verified: str | None = None
    product_blood_type: str | None = None

    compatibility_status: str
    crossmatch_reference: str | None = None
    compatibility_notes: str | None = None
    compatibility_verified_at: datetime | None = None
    emergency_release_reason: str | None = None
    emergency_release_requested: bool = False
    emergency_request_reason: str | None = None
    emergency_requested_by_staff_id: int | None = Field(default=None, gt=0)
    emergency_requested_at: datetime | None = None
    emergency_review_outcome: str | None = None
    emergency_review_note: str | None = None
    emergency_reviewed_by_staff_id: int | None = Field(default=None, gt=0)
    emergency_reviewed_at: datetime | None = None
    issued_units: list[BloodProductIssuedUnitResponseSchema] = Field(default_factory=list)
    lab_followups: list[BloodProductLabFollowupResponseSchema] = Field(default_factory=list)

    priority: ClinicalOrderPriority
    status: ClinicalOrderLifecycleStatus

    scheduled_at: datetime | None = None
    ordered_at: datetime
    fulfillment_note: str | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None

    version: int = Field(..., ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BloodProductOrderListResponseSchema(_ResponseModel):
    items: list[BloodProductOrderResponseSchema] = Field(
        default_factory=list
    )
    total: int = Field(..., ge=0)
