"""SB: strict HTTP DTOs for CE1's non-execution surgical documentation services.

There are deliberately no clinical execution, transfer, discharge, eMAR,
Sign-Out, or completion request fields. Extra fields always fail closed.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool


Positive = Annotated[int, Field(strict=True, gt=0)]
Version = Positive
SmallText = Annotated[str, Field(min_length=1, max_length=500)]
# Strict 256-KiB decoded-byte cap is also enforced *after* base64 decoding.
DocumentBase64 = Annotated[str, Field(min_length=4, max_length=349528)]


class SurgeryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanCaseBody(SurgeryBody):
    procedure_order_id: Positive


class CreateTheatreBody(SurgeryBody):
    code: Annotated[str, Field(min_length=1, max_length=32)]
    display_name: Annotated[str, Field(min_length=1, max_length=128)]


class ScheduleBody(SurgeryBody):
    surgical_case_id: Positive
    theatre_id: Positive
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class RescheduleBody(SurgeryBody):
    expected_version: Version
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class VersionReasonBody(SurgeryBody):
    expected_version: Version
    reason: SmallText


class PrivilegeBody(SurgeryBody):
    staff_id: Positive
    privilege_kind: Literal["surgeon", "anesthetist"]
    procedure_code: Annotated[str, Field(min_length=2, max_length=64)]
    license_reference: Annotated[str, Field(min_length=1, max_length=128)]
    credential_document_ref: Annotated[str, Field(min_length=1, max_length=255)]
    credential_document_b64: DocumentBase64
    verification_source: Annotated[str, Field(min_length=1, max_length=255)]
    expires_at: AwareDatetime


class ConsentRecordBody(SurgeryBody):
    signer_kind: Literal["patient", "guardian", "authorized_representative"]
    signer_name: Annotated[str, Field(min_length=1, max_length=255)]
    signer_authority: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    signed_document_ref: Annotated[str, Field(min_length=1, max_length=255)]
    signed_document_b64: DocumentBase64
    signed_at: AwareDatetime
    expires_at: AwareDatetime


class ConsentVerifyBody(SurgeryBody):
    expected_version: Version
    reviewed_document_b64: DocumentBase64


class ChecklistBody(SurgeryBody):
    surgeon_staff_id: Positive
    anesthetist_staff_id: Positive
    answers: dict[str, StrictBool]


class PreanesthesiaBody(SurgeryBody):
    surgeon_staff_id: Positive
    asa_class: Annotated[int, Field(strict=True, ge=1, le=6)]
    technique: Literal["general", "regional", "local", "sedation"]
    airway_assessment: Literal["routine", "anticipated_difficult", "unknown"]
    airway_plan: Annotated[str, Field(min_length=1, max_length=1000)]
    allergy_status: Literal["known", "none_known", "unknown"]
    allergy_notes: Annotated[str, Field(max_length=1000)] | None = None
    fasting_confirmed: StrictBool
    fasting_notes: Annotated[str, Field(min_length=1, max_length=1000)]
    risk_and_comorbidity_notes: Annotated[str, Field(min_length=1, max_length=4000)]
    plan_summary: Annotated[str, Field(min_length=1, max_length=4000)]
    monitoring_plan: Annotated[str, Field(min_length=1, max_length=2000)]
    contingency_plan: Annotated[str, Field(min_length=1, max_length=2000)]


class BaselineBody(SurgeryBody):
    expected_version: Version
    observed_at: AwareDatetime
    heart_rate_bpm: Annotated[int, Field(strict=True, ge=1, le=300)]
    systolic_bp: Annotated[int, Field(strict=True, ge=1, le=350)]
    diastolic_bp: Annotated[int, Field(strict=True, ge=1, le=250)]
    spo2_percent: Annotated[int, Field(strict=True, ge=0, le=100)]
    respiratory_rate: Annotated[int, Field(strict=True, ge=1, le=100)]
    temperature_tenths_c: Annotated[int, Field(strict=True, ge=200, le=450)]
    oxygen_delivery: Literal["ambient_air", "supplemental"]
    observation_notes: Annotated[str, Field(min_length=1, max_length=1000)]


class ReviewBody(SurgeryBody):
    expected_version: Version


# The narrative fields are all required by the CE1 service. There is no
# procedure-completed boolean or permission to advance an operative lifecycle.
class ReportNarrativeBody(SurgeryBody):
    complication_status: Literal["none_reported", "documented"]
    estimated_blood_loss_ml: Annotated[int, Field(strict=True, ge=0, le=100000)]
    preoperative_diagnosis: Annotated[str, Field(min_length=1, max_length=1200)]
    postoperative_diagnosis: Annotated[str, Field(min_length=1, max_length=1200)]
    procedure_narrative: Annotated[str, Field(min_length=1, max_length=6000)]
    operative_findings: Annotated[str, Field(min_length=1, max_length=4000)]
    operative_course: Annotated[str, Field(min_length=1, max_length=6000)]
    complication_notes: Annotated[str, Field(min_length=1, max_length=2000)]
    specimen_notes: Annotated[str, Field(min_length=1, max_length=2000)]
    implant_notes: Annotated[str, Field(min_length=1, max_length=2000)]
    postoperative_plan: Annotated[str, Field(min_length=1, max_length=3000)]


class CreateReportBody(ReportNarrativeBody):
    journal_id: Positive


class ReviseReportBody(ReportNarrativeBody):
    expected_version: Version


class SignReportBody(SurgeryBody):
    expected_version: Version
    signer_kind: Literal["surgeon", "anesthetist"]
    attestation_note: Annotated[str, Field(min_length=1, max_length=800)]
