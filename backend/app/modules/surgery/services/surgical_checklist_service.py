"""CE1-S04 internal-only WHO-inspired checklist attestations.

Only Sign-In and Time-Out may be attested in S04. Sign-Out is structurally
reserved but fail-closed until a later authoritative operative-event record
exists. Attestation does NOT release/start/complete surgery. No public routes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.staff.models.staff_model import Staff
from app.modules.surgery.models.surgical_checklist_model import SurgicalSafetyChecklistAttestation
from app.modules.surgery.models.theatre_model import OperatingTheatre, TheatreBooking
from app.modules.surgery.services.surgical_authorization_service import (
    _case_and_order, assert_surgical_consent_and_privileges,
)
from app.modules.surgery.services.surgical_case_service import _doctor, _positive

# Essential confirmation gates. Items are intentionally strict in S04; special
# circumstances and documented N/A handling require a later policy review.
REQUIRED_ANSWERS = {
    "sign_in": frozenset({
        "patient_identity_confirmed", "procedure_site_confirmed",
        "signed_consent_evidence_confirmed", "anesthesia_safety_reviewed",
        "monitoring_equipment_ready",
    }),
    "time_out": frozenset({
        "whole_team_introduced", "patient_procedure_site_reconfirmed",
        "critical_events_discussed", "infection_prevention_reviewed",
        "required_imaging_reviewed",
    }),
    "sign_out": frozenset({
        "procedure_record_confirmed", "instrument_sponge_sharp_counts_reconciled",
        "specimens_labeled_and_handed_off", "equipment_issues_recorded",
        "recovery_and_handoff_plan_confirmed",
    }),
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _actor(*, actor_staff_id: int, clinic_id: int, phase: str) -> Staff:
    if phase == "time_out":
        return _doctor(actor_staff_id, clinic_id)
    actor = db.session.execute(select(Staff).where(
        Staff.id == _positive(actor_staff_id, "actor_staff_id"),
        Staff.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if actor is None:
        raise NotFoundError("Checklist attesting staff not found")
    if (actor.status != StaffStatus.ACTIVE or actor.user is None
            or not actor.user.is_active or actor.user.role != Role.NURSE):
        raise ValidationError("Active clinic nurse required for this checklist phase")
    return actor


def _answers(phase: str, answers: dict) -> dict:
    expected = REQUIRED_ANSWERS[phase]
    if not isinstance(answers, dict) or set(answers) != expected:
        raise ValidationError("Exact required checklist answers must be supplied")
    if any(answers[key] is not True for key in expected):
        raise ConflictError("All required checklist confirmations must be affirmative")
    return {key: True for key in sorted(expected)}


def _active_booking(*, case_id: int, clinic_id: int) -> TheatreBooking:
    statement = (
        select(TheatreBooking)
        .join(OperatingTheatre, OperatingTheatre.id == TheatreBooking.theatre_id)
        .where(
            TheatreBooking.surgical_case_id == case_id,
            TheatreBooking.clinic_id == clinic_id,
            TheatreBooking.status == "scheduled",
            OperatingTheatre.clinic_id == clinic_id,
            OperatingTheatre.is_active.is_(True),
        )
    )
    bookings = db.session.execute(statement).scalars().all()
    if len(bookings) != 1:
        raise ConflictError("Exactly one active theatre booking is required")
    return bookings[0]


def _previous(*, case_id: int, phase: str):
    return db.session.execute(select(SurgicalSafetyChecklistAttestation).where(
        SurgicalSafetyChecklistAttestation.surgical_case_id == case_id,
        SurgicalSafetyChecklistAttestation.phase == phase,
    )).scalar_one_or_none()


def _snapshot_digest(values: dict) -> str:
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_attestation_digest(row: SurgicalSafetyChecklistAttestation) -> None:
    source = {
        "clinic_id": row.clinic_id, "patient_id": row.patient_id,
        "admission_id": row.admission_id, "surgical_case_id": row.surgical_case_id,
        "procedure_order_id": row.procedure_order_id,
        "procedure_order_version": row.procedure_order_version,
        "theatre_booking_id": row.theatre_booking_id,
        "booking_version": row.booking_version,
        "surgical_consent_id": row.surgical_consent_id,
        "surgeon_staff_id": row.surgeon_staff_id,
        "anesthetist_staff_id": row.anesthetist_staff_id,
        "attested_by_staff_id": row.attested_by_staff_id,
        "phase": row.phase, "answers": row.answers,
        "attested_at": row.attested_at.isoformat(),
    }
    expected = _snapshot_digest(source)
    if not hmac.compare_digest(row.evidence_sha256, expected):
        raise ConflictError("Surgical checklist evidence fingerprint mismatch")


@transactional
def attest_checklist_phase(
    *, clinic_id: int, surgical_case_id: int, actor_staff_id: int,
    surgeon_staff_id: int, anesthetist_staff_id: int,
    phase: str, answers: dict,
) -> SurgicalSafetyChecklistAttestation:
    """Append a single phase attestation without advancing operative state.

    Sign-Out is unavailable until a later surgical-execution evidence domain
    exists; it is never inferred from time-out or from ProcedureOrder flags.
    """
    clinic_id = _positive(clinic_id, "clinic_id")
    case_id = _positive(surgical_case_id, "surgical_case_id")
    if phase not in REQUIRED_ANSWERS:
        raise ValidationError("Unknown surgical checklist phase")
    if phase == "sign_out":
        raise ConflictError("Sign-Out requires future authoritative operative completion evidence")
    confirmed = _answers(phase, answers)
    actor = _actor(actor_staff_id=actor_staff_id, clinic_id=clinic_id, phase=phase)

    # Lock case first, serializing phase creation in PostgreSQL.
    case, order = _case_and_order(case_id, clinic_id, lock=True)
    booking = _active_booking(case_id=case.id, clinic_id=clinic_id)
    consent = assert_surgical_consent_and_privileges(
        surgical_case_id=case.id, clinic_id=clinic_id,
        surgeon_staff_id=_positive(surgeon_staff_id, "surgeon_staff_id"),
        anesthetist_staff_id=_positive(anesthetist_staff_id, "anesthetist_staff_id"),
    )
    if phase == "time_out" and actor.id != surgeon_staff_id:
        raise ConflictError("Time-Out must be attested by the privileged surgeon")
    if _previous(case_id=case.id, phase=phase) is not None:
        raise ConflictError("Checklist phase already attested")
    if phase == "time_out":
        sign_in = _previous(case_id=case.id, phase="sign_in")
        if sign_in is None:
            raise ConflictError("Sign-In attestation required before Time-Out")
        _validate_attestation_digest(sign_in)
        if (sign_in.procedure_order_version != order.version
                or sign_in.theatre_booking_id != booking.id
                or sign_in.booking_version != booking.version
                or sign_in.surgical_consent_id != consent.id
                or sign_in.surgeon_staff_id != surgeon_staff_id
                or sign_in.anesthetist_staff_id != anesthetist_staff_id):
            raise ConflictError("Surgical team, booking or consent changed after Sign-In")

    stamp = _now()
    evidence = {
        "clinic_id": clinic_id, "patient_id": case.patient_id,
        "admission_id": case.admission_id, "surgical_case_id": case.id,
        "procedure_order_id": order.id, "procedure_order_version": order.version,
        "theatre_booking_id": booking.id, "booking_version": booking.version,
        "surgical_consent_id": consent.id,
        "surgeon_staff_id": surgeon_staff_id,
        "anesthetist_staff_id": anesthetist_staff_id,
        "attested_by_staff_id": actor.id, "phase": phase,
        "answers": confirmed, "attested_at": stamp.isoformat(),
    }
    row = SurgicalSafetyChecklistAttestation(
        clinic_id=clinic_id, patient_id=case.patient_id,
        admission_id=case.admission_id, surgical_case_id=case.id,
        procedure_order_id=order.id, procedure_order_version=order.version,
        theatre_booking_id=booking.id, booking_version=booking.version,
        surgical_consent_id=consent.id,
        surgeon_staff_id=surgeon_staff_id,
        anesthetist_staff_id=anesthetist_staff_id,
        attested_by_staff_id=actor.id, phase=phase,
        answers=confirmed, evidence_sha256=_snapshot_digest(evidence),
        attested_at=stamp,
    )
    db.session.add(row)
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="SurgicalSafetyChecklistAttestation",
        entity_id=row.id,
        description=f"Recorded {phase} checklist attestation; no operative release",
        new_value={
            "surgical_case_id": case.id, "phase": phase,
            "attested_by_staff_id": actor.id, "evidence_sha256": row.evidence_sha256,
            "procedure_order_version": order.version,
            "booking_version": booking.version,
        },
        clinic_id=clinic_id, patient_id=case.patient_id, user_id=actor.user_id,
    )
    return row


def get_checklist_phase(*, surgical_case_id: int, clinic_id: int, phase: str):
    clinic_id = _positive(clinic_id, "clinic_id")
    if phase not in REQUIRED_ANSWERS:
        raise ValidationError("Unknown surgical checklist phase")
    result = db.session.execute(select(SurgicalSafetyChecklistAttestation).where(
        SurgicalSafetyChecklistAttestation.surgical_case_id == _positive(
            surgical_case_id, "surgical_case_id"),
        SurgicalSafetyChecklistAttestation.clinic_id == clinic_id,
        SurgicalSafetyChecklistAttestation.phase == phase,
    )).scalar_one_or_none()
    if result is None:
        raise NotFoundError("Surgical checklist phase not found")
    _validate_attestation_digest(result)
    return result
