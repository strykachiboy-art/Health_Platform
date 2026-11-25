from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums.blood_bank_enums import (
    BloodBarcodeFailureCode,
    BloodUnitSourceType,
    BloodUnitStatus,
    DonationStatus,
    DonorDeferralStatus,
    TransfusionReactionSeverity,
    TransfusionReactionType,
    TransfusionStatus,
    TtiScreeningStatus,
)
from app.core.enums.emar_enums import BarcodeVerificationResult
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


def _validate_product_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _PRODUCT_TYPES:
        raise ValueError("Invalid blood product type")
    return normalized


def _validate_known_blood_type(value: BloodType) -> BloodType:
    if value == BloodType.UNKNOWN:
        raise ValueError("Blood unit blood_type cannot be unknown")
    return value


class BloodDonorCreateSchema(_RequestModel):
    donor_code: str = Field(..., min_length=1, max_length=64)
    full_name: str = Field(..., min_length=1, max_length=200)
    phone: str = Field(..., min_length=1, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    date_of_birth: date | None = None
    blood_type: BloodType = BloodType.UNKNOWN
    cooldown_days: int = Field(default=56, ge=1, le=365)
    consent_to_contact: bool = True
    notes: str | None = Field(default=None, max_length=5000)


class BloodDonorDeferralUpdateSchema(_RequestModel):
    deferral_status: DonorDeferralStatus
    deferral_reason: str | None = Field(default=None, max_length=5000)
    deferred_until: datetime | None = None
    consent_to_contact: bool | None = None


class BloodDonorResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    donor_code: str
    full_name: str
    phone: str
    email: str | None = None
    date_of_birth: date | None = None
    blood_type: str
    deferral_status: DonorDeferralStatus
    deferral_reason: str | None = None
    deferred_until: datetime | None = None
    last_donated_at: datetime | None = None
    cooldown_days: int = Field(..., ge=1)
    total_donations: int = Field(..., ge=0)
    consent_to_contact: bool
    notes: str | None = None
    registered_by_staff_id: int = Field(..., gt=0)
    version: int = Field(..., ge=1)
    created_at: datetime
    updated_at: datetime


class BloodDonorListResponseSchema(_ResponseModel):
    items: list[BloodDonorResponseSchema] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class BloodDonationComponentSpecSchema(_RequestModel):
    unit_barcode: str = Field(..., min_length=1, max_length=128)
    product_type: str = Field(..., min_length=1, max_length=40)
    volume_ml: int = Field(..., gt=0, le=2000)
    expires_at: datetime

    @field_validator("product_type")
    @classmethod
    def validate_product_type(cls, value: str) -> str:
        return _validate_product_type(value)


class BloodDonationCreateSchema(_RequestModel):
    donor_id: int = Field(..., gt=0)
    donation_number: str = Field(..., min_length=1, max_length=64)
    volume_ml: int = Field(..., gt=0, le=1000)
    collected_at: datetime | None = None
    hemoglobin_g_dl: Decimal | None = Field(default=None, gt=0, le=25)
    systolic_bp: int | None = Field(default=None, ge=40, le=300)
    diastolic_bp: int | None = Field(default=None, ge=20, le=200)
    pulse_bpm: int | None = Field(default=None, ge=25, le=250)
    intake_passed: bool = True
    intake_notes: str | None = Field(default=None, max_length=5000)
    replacement_for_order_id: int | None = Field(default=None, gt=0)
    replacement_for_admission_id: int | None = Field(default=None, gt=0)
    components: list[BloodDonationComponentSpecSchema] = Field(
        ..., min_length=1, max_length=10
    )


class BloodDonationScreenSchema(_RequestModel):
    tti_screening_status: TtiScreeningStatus
    tti_results: dict[str, Any] = Field(..., min_length=1)
    confirmed_blood_type: BloodType | None = None
    deferral_reason: str | None = Field(default=None, max_length=5000)


class BloodDonationResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    donor_id: int = Field(..., gt=0)
    donation_number: str
    collected_by_staff_id: int = Field(..., gt=0)
    collected_at: datetime
    volume_ml: int = Field(..., gt=0)
    hemoglobin_g_dl: Decimal | None = None
    systolic_bp: int | None = None
    diastolic_bp: int | None = None
    pulse_bpm: int | None = None
    intake_passed: bool
    intake_notes: str | None = None
    tti_screening_status: TtiScreeningStatus
    tti_results: dict[str, Any] | None = None
    screened_by_staff_id: int | None = Field(default=None, gt=0)
    screened_at: datetime | None = None
    status: DonationStatus
    replacement_for_order_id: int | None = Field(default=None, gt=0)
    replacement_for_admission_id: int | None = Field(default=None, gt=0)
    version: int = Field(..., ge=1)
    created_at: datetime
    updated_at: datetime


class ExternalBloodUnitReceiveSchema(_RequestModel):
    unit_barcode: str = Field(..., min_length=1, max_length=128)
    din: str = Field(..., min_length=1, max_length=64)
    external_supplier_name: str = Field(..., min_length=1, max_length=200)
    external_reference: str | None = Field(default=None, max_length=128)
    product_type: str = Field(..., min_length=1, max_length=40)
    blood_type: BloodType
    volume_ml: int = Field(..., gt=0, le=2000)
    collected_at: datetime
    expires_at: datetime
    tti_screening_cleared: bool = True
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("product_type")
    @classmethod
    def validate_product_type(cls, value: str) -> str:
        return _validate_product_type(value)

    @field_validator("blood_type")
    @classmethod
    def validate_blood_type(cls, value: BloodType) -> BloodType:
        return _validate_known_blood_type(value)


class BloodUnitDiscardSchema(_RequestModel):
    reason: str = Field(..., min_length=1, max_length=5000)


class BloodUnitResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    unit_barcode: str
    din: str
    source_type: BloodUnitSourceType
    donation_id: int | None = Field(default=None, gt=0)
    external_supplier_name: str | None = None
    external_reference: str | None = None
    product_type: str
    blood_type: str
    volume_ml: int = Field(..., gt=0)
    collected_at: datetime
    expires_at: datetime
    tti_screening_status: TtiScreeningStatus
    status: BloodUnitStatus
    reserved_for_order_id: int | None = Field(default=None, gt=0)
    issued_to_order_id: int | None = Field(default=None, gt=0)
    received_or_created_by_staff_id: int = Field(..., gt=0)
    released_by_staff_id: int | None = Field(default=None, gt=0)
    released_at: datetime | None = None
    reserved_by_staff_id: int | None = Field(default=None, gt=0)
    reserved_at: datetime | None = None
    issued_by_staff_id: int | None = Field(default=None, gt=0)
    issued_at: datetime | None = None
    discarded_by_staff_id: int | None = Field(default=None, gt=0)
    discarded_at: datetime | None = None
    discard_reason: str | None = None
    notes: str | None = None
    version: int = Field(..., ge=1)
    created_at: datetime
    updated_at: datetime


class BloodUnitListResponseSchema(_ResponseModel):
    items: list[BloodUnitResponseSchema] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class BloodStockThresholdUpsertSchema(_RequestModel):
    product_type: str = Field(..., min_length=1, max_length=40)
    blood_type: BloodType
    minimum_units: int = Field(..., ge=0, le=1000)
    critical_units: int = Field(..., ge=0, le=1000)

    @field_validator("product_type")
    @classmethod
    def validate_product_type(cls, value: str) -> str:
        return _validate_product_type(value)

    @field_validator("blood_type")
    @classmethod
    def validate_blood_type(cls, value: BloodType) -> BloodType:
        return _validate_known_blood_type(value)


class BloodStockThresholdResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    product_type: str
    blood_type: str
    minimum_units: int = Field(..., ge=0)
    critical_units: int = Field(..., ge=0)
    updated_by_staff_id: int = Field(..., gt=0)
    updated_at: datetime


class BloodStockSummaryRowSchema(_ResponseModel):
    product_type: str
    blood_type: str
    available_units: int = Field(..., ge=0)
    reserved_units: int = Field(..., ge=0)
    quarantined_units: int = Field(..., ge=0)
    expiring_within_72h_units: int = Field(..., ge=0)
    minimum_units: int = Field(..., ge=0)
    critical_units: int = Field(..., ge=0)
    stock_level: str


class BloodStockSummaryResponseSchema(_ResponseModel):
    items: list[BloodStockSummaryRowSchema] = Field(default_factory=list)
    total_available: int = Field(..., ge=0)
    total_quarantined: int = Field(..., ge=0)


class EligibleDonorMatchSchema(_ResponseModel):
    donor_id: int = Field(..., gt=0)
    donor_code: str
    full_name: str
    phone: str
    blood_type: str
    last_donated_at: datetime | None = None
    total_donations: int = Field(..., ge=0)


class ShortageDonorMatchesResponseSchema(_ResponseModel):
    blood_product_order_id: int | None = Field(default=None, gt=0)
    product_type: str
    target_blood_type: str
    compatible_donor_blood_types: list[str] = Field(default_factory=list)
    available_compatible_units: int = Field(..., ge=0)
    shortage_detected: bool
    eligible_donors: list[EligibleDonorMatchSchema] = Field(
        default_factory=list
    )


class BloodTransfusionBarcodeVerifySchema(_RequestModel):
    blood_product_order_id: int | None = Field(default=None, gt=0)
    patient_barcode: str = Field(..., min_length=1, max_length=255)
    unit_barcode: str = Field(..., min_length=1, max_length=255)
    scan_timestamp: datetime
    scan_device: str = Field(..., min_length=1, max_length=255)
    operation_identity: str = Field(..., min_length=1, max_length=255)


class BloodTransfusionBarcodeEventResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int | None = Field(default=None, gt=0)
    admission_id: int | None = Field(default=None, gt=0)
    blood_product_order_id: int | None = Field(default=None, gt=0)
    blood_unit_id: int | None = Field(default=None, gt=0)
    actor_staff_id: int = Field(..., gt=0)
    scanned_patient_id: int | None = Field(default=None, gt=0)
    scanned_unit_id: int | None = Field(default=None, gt=0)
    patient_barcode: str
    unit_barcode: str
    scan_timestamp: datetime
    scan_device: str
    verification_result: BarcodeVerificationResult
    failure_code: BloodBarcodeFailureCode | None = None
    failure_reason: str | None = None
    patient_match: bool
    unit_match: bool
    blood_type_match: bool
    expiry_match: bool
    order_status_match: bool
    expected_patient_blood_type: str | None = None
    scanned_unit_blood_type: str | None = None
    source_order_version: int | None = None
    verification_policy_version: int = Field(..., ge=1)
    operation_identity: str
    created_at: datetime


class BloodTransfusionStartSchema(_RequestModel):
    blood_product_order_id: int = Field(..., gt=0)
    blood_unit_id: int = Field(..., gt=0)
    barcode_verification_event_id: int = Field(..., gt=0)
    pre_vitals: dict[str, Any] | None = None
    notes: str | None = Field(default=None, max_length=5000)


class BloodTransfusionCompleteSchema(_RequestModel):
    post_vitals: dict[str, Any] | None = None
    notes: str | None = Field(default=None, max_length=5000)


class BloodTransfusionReactionSchema(_RequestModel):
    reaction_type: TransfusionReactionType
    reaction_severity: TransfusionReactionSeverity
    reaction_notes: str = Field(..., min_length=1, max_length=5000)
    post_vitals: dict[str, Any] | None = None


class BloodTransfusionResponseSchema(_ResponseModel):
    id: int = Field(..., gt=0)
    clinic_id: int = Field(..., gt=0)
    patient_id: int = Field(..., gt=0)
    admission_id: int = Field(..., gt=0)
    blood_product_order_id: int = Field(..., gt=0)
    blood_unit_id: int = Field(..., gt=0)
    barcode_verification_event_id: int = Field(..., gt=0)
    administered_by_staff_id: int = Field(..., gt=0)
    started_at: datetime
    ended_at: datetime | None = None
    status: TransfusionStatus
    pre_vitals: dict[str, Any] | None = None
    post_vitals: dict[str, Any] | None = None
    reaction_reported: bool
    reaction_type: TransfusionReactionType
    reaction_severity: TransfusionReactionSeverity
    reaction_notes: str | None = None
    reaction_reported_at: datetime | None = None
    reaction_reported_by_staff_id: int | None = Field(default=None, gt=0)
    lookback_triggered: bool
    notes: str | None = None
    version: int = Field(..., ge=1)
    created_at: datetime
    updated_at: datetime


class BloodTransfusionListResponseSchema(_ResponseModel):
    items: list[BloodTransfusionResponseSchema] = Field(default_factory=list)
    total: int = Field(..., ge=0)
