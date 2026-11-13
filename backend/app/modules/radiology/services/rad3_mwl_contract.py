"""RAD3 source-compatible, inert RIS->MWL and acquisition candidate contract.

No network, no db writes, no clinic lookup, no imaging-order creation,
no durable publication, no signed report, no study association writer.
The caller MUST obtain this snapshot through authorized canonical RIS joins.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

_RAD1_ACCESSION = re.compile(r"RAD-[0-9A-F]{24}\Z")
_AET = re.compile(r"[A-Z0-9_]{2,16}\Z")
_ISSUER = re.compile(r"CSP-CLINIC-[1-9][0-9]*\Z")
_UID = re.compile(r"[0-9]+(?:\.[0-9]+)+\Z")
_MODALITIES = {"xray":"DX", "ct":"CT", "mri":"MR", "ultrasound":"US",
              "mammography":"MG", "fluoroscopy":"RF", "nuclear_medicine":"NM", "other":"OT"}


class ContractViolation(ValueError):
    """The clinical snapshot was not proven safe for an MWL mapping."""


@dataclass(frozen=True)
class CanonicalRISIntent:
    clinic_id: int
    imaging_order_id: int
    exam_id: int
    exam_version: int
    order_status: str
    exam_status: str
    issuer_namespace: str
    accession_number: str
    patient_id: str  # Must come from canonical patient record, not client / DICOM
    study_instance_uid: str  # Allocated once in a trusted RIS process
    scheduled_step_id: int
    scheduled_step_sequence: int
    scheduled_step_status: str
    scheduled_at: datetime
    modality: str
    station_aet: str


def _positive(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ContractViolation(label + " must be a positive integer")
    return value


def validate_intent(s: CanonicalRISIntent) -> None:
    if not isinstance(s, CanonicalRISIntent):
        raise ContractViolation("Trusted typed RIS snapshot required")
    for name in ("clinic_id", "imaging_order_id", "exam_id", "exam_version",
                 "scheduled_step_id", "scheduled_step_sequence"):
        _positive(getattr(s, name), name)
    if s.issuer_namespace != f"CSP-CLINIC-{s.clinic_id}" or not _ISSUER.fullmatch(s.issuer_namespace):
        raise ContractViolation("Issuer and clinic mismatch")
    if not _RAD1_ACCESSION.fullmatch(s.accession_number):
        raise ContractViolation("RAD1 issuer-scoped accession required")
    if not s.patient_id.startswith("RAD2_SYNTH_"):
        raise ContractViolation("RAD3 synthetic-only MWL gate: real patients prohibited")
    if s.order_status not in ("pending", "active", "in_progress", "on_hold"):
        raise ContractViolation("Canonical imaging order inactive")
    if s.exam_status != "scheduled" or s.scheduled_step_status != "intent":
        raise ContractViolation("Current scheduled step required")
    if s.scheduled_step_sequence != s.exam_version - 1:
        raise ContractViolation("Stale RIS step/exam version")
    if s.modality not in _MODALITIES:
        raise ContractViolation("Unsupported RIS modality")
    if not isinstance(s.station_aet, str) or not _AET.fullmatch(s.station_aet):
        raise ContractViolation("DICOM station AET invalid")
    if (not isinstance(s.patient_id, str) or not (3 <= len(s.patient_id) <= 64)
            or not s.patient_id.isascii() or not re.fullmatch(r"[A-Za-z0-9_\-]+", s.patient_id)):
        raise ContractViolation("Validated canonical patient identifier required")
    if not isinstance(s.study_instance_uid, str) or len(s.study_instance_uid) > 64 or not _UID.fullmatch(s.study_instance_uid):
        raise ContractViolation("Valid preallocated DICOM Study UID required")
    if not isinstance(s.scheduled_at, datetime) or s.scheduled_at.utcoffset() is None:
        raise ContractViolation("Timezone-aware scheduled timestamp required")


def dicom_accession(s: CanonicalRISIntent) -> str:
    """Synthetic 16-char test ID, NOT an approved real-world accession scheme.

    RAD1's canonical accession is 28 characters, exceeding DICOM SH's 16-char
    limit. Real publication requires an independent, durable, database-unique
    DICOM accession mapping with issuer semantics and concurrency evidence.
    This digest is only a reproducible synthetic integration-test placeholder.
    """
    validate_intent(s)
    digest=hashlib.sha256((s.issuer_namespace+':'+s.accession_number).encode('ascii')).hexdigest()
    result='R'+digest[:15].upper()
    assert len(result)==16
    return result


def worklist_payload(s: CanonicalRISIntent) -> dict:
    """Build REST *intent* only. Returning payload never means PACS published it."""
    validate_intent(s)
    stamp = s.scheduled_at.astimezone(timezone.utc)
    return {"Tags": {
        "PatientID": s.patient_id,
        "PatientName": "RAD2^SYNTHETIC" if s.patient_id.startswith("RAD2_SYNTH_") else "ANONYMOUS^TO_BE_RESOLVED",
        "AccessionNumber": dicom_accession(s),
        "StudyInstanceUID": s.study_instance_uid,
        "RequestedProcedureID": f"R{s.imaging_order_id}",
        "RequestedProcedureDescription": "RIS scheduled imaging (no diagnostic authority)",
        "ScheduledProcedureStepSequence": [{
            "Modality": _MODALITIES[s.modality],
            "ScheduledStationAETitle": s.station_aet,
            "ScheduledProcedureStepStartDate": stamp.strftime('%Y%m%d'),
            "ScheduledProcedureStepStartTime": stamp.strftime('%H%M%S'),
            "ScheduledProcedureStepID": f"S{s.scheduled_step_id}",
            "ScheduledProcedureStepDescription": "Synthetic RAD3 test procedure",
        }],
    }}


@dataclass(frozen=True)
class CandidateOutcome:
    match_status: str
    reason: str
    archive_verified: bool = False
    report_signed: bool = False
    chart_released: bool = False


def reconcile_candidate(s: CanonicalRISIntent, tags: dict[str, str]) -> CandidateOutcome:
    """Never associates the DICOM instance. Only advises whether it merits review."""
    try:
        validate_intent(s)
    except ContractViolation:
        return CandidateOutcome("quarantined", "untrusted_or_stale_ris_snapshot")
    if not isinstance(tags, dict):
        return CandidateOutcome("quarantined", "malformed_observation")
    expected = {
        "PatientID": s.patient_id,
        "AccessionNumber": dicom_accession(s),
        "StudyInstanceUID": s.study_instance_uid,
        "Modality": _MODALITIES[s.modality],
    }
    for key, value in expected.items():
        if not isinstance(tags.get(key), str) or tags[key] != value:
            return CandidateOutcome("quarantined", "missing_or_mismatched_" + key)
    if tags.get("SOPInstanceUID") is None or not isinstance(tags.get("SOPInstanceUID"), str):
        return CandidateOutcome("quarantined", "missing_sop_uid")
    if not _UID.fullmatch(tags["SOPInstanceUID"]):
        return CandidateOutcome("quarantined", "invalid_sop_uid")
    return CandidateOutcome("unverified", "candidate_match_requires_authorized_reconciliation")
