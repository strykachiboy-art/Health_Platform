"""CE1-S06 append-only intraoperative documentation foundation.

Never authorizes incision, procedure performance, anesthesia induction, eMAR
administration, theatre transfer, Sign-Out, or ProcedureOrder completion.
S06 sealed = documentation reconciled; NOT an operation-completed assertion.
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
from app.modules.surgery.models.intraoperative_evidence_model import (
    SurgicalEvidenceJournal, SurgicalEvidenceEvent, SurgicalInstrumentCount,
)
from app.modules.surgery.services import anesthesia_service
from app.modules.surgery.services import surgical_checklist_service as checklist
from app.modules.surgery.services.surgical_case_service import _doctor, _positive
from app.modules.surgery.services.theatre_booking_service import _required_text

_EVENT_KINDS = frozenset({
    "team_joined", "team_left", "safety_observation", "equipment_issue", "clinical_note",
})
_COUNT_KINDS = frozenset({"instruments", "sponges", "sharps"})
_COUNT_PHASES = frozenset({"baseline", "final"})


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(data: dict) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("observed_at must include a timezone offset")
    parsed = value.astimezone(timezone.utc).replace(tzinfo=None)
    if parsed > _now():
        raise ValidationError("Future event observations are not allowed")
    return parsed


def _active_team_member(staff_id: int, clinic_id: int) -> Staff:
    person = db.session.execute(select(Staff).where(
        Staff.id == _positive(staff_id, "staff_id"),
        Staff.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if person is None:
        raise NotFoundError("Surgical team staff not found")
    if (person.status != StaffStatus.ACTIVE or person.user is None
            or not person.user.is_active or person.user.role not in (Role.NURSE, Role.DOCTOR)):
        raise ValidationError("Active clinical team staff required")
    return person


def _source_snapshot(row: SurgicalEvidenceJournal) -> dict:
    return {
        "clinic_id": row.clinic_id, "patient_id": row.patient_id,
        "surgical_case_id": row.surgical_case_id,
        "procedure_order_id": row.procedure_order_id,
        "procedure_order_version": row.procedure_order_version,
        "theatre_booking_id": row.theatre_booking_id,
        "booking_version": row.booking_version,
        "surgical_consent_id": row.surgical_consent_id,
        "sign_in_attestation_id": row.sign_in_attestation_id,
        "sign_in_sha256": row.sign_in_sha256,
        "time_out_attestation_id": row.time_out_attestation_id,
        "time_out_sha256": row.time_out_sha256,
        "anesthesia_assessment_id": row.anesthesia_assessment_id,
        "anesthesia_sha256": row.anesthesia_sha256,
        "surgeon_staff_id": row.surgeon_staff_id,
        "anesthetist_staff_id": row.anesthetist_staff_id,
        "counting_nurse_staff_id": row.counting_nurse_staff_id,
        "created_by_staff_id": row.created_by_staff_id,
        "created_at": row.created_at.isoformat(),
    }


def _event_snapshot(row: SurgicalEvidenceEvent) -> dict:
    return {
        "id": row.id, "clinic_id": row.clinic_id, "journal_id": row.journal_id,
        "sequence": row.sequence, "category": row.category,
        "actor_staff_id": row.actor_staff_id, "related_staff_id": row.related_staff_id,
        "observed_at": row.observed_at.isoformat(), "note": row.note,
    }


def _count_snapshot(row: SurgicalInstrumentCount) -> dict:
    return {
        "id": row.id, "clinic_id": row.clinic_id, "journal_id": row.journal_id,
        "item_kind": row.item_kind, "phase": row.phase,
        "observed_count": row.observed_count,
        "counted_by_staff_id": row.counted_by_staff_id,
        "observed_at": row.observed_at.isoformat(), "note": row.note,
    }


def _children(row: SurgicalEvidenceJournal):
    events = list(db.session.execute(select(SurgicalEvidenceEvent).where(
        SurgicalEvidenceEvent.journal_id == row.id,
        SurgicalEvidenceEvent.clinic_id == row.clinic_id,
    ).order_by(SurgicalEvidenceEvent.sequence)).scalars())
    counts = list(db.session.execute(select(SurgicalInstrumentCount).where(
        SurgicalInstrumentCount.journal_id == row.id,
        SurgicalInstrumentCount.clinic_id == row.clinic_id,
    ).order_by(SurgicalInstrumentCount.item_kind, SurgicalInstrumentCount.phase)).scalars())
    for ix, event in enumerate(events, 1):
        if event.sequence != ix or not hmac.compare_digest(event.evidence_sha256, _digest(_event_snapshot(event))):
            raise ConflictError("Surgical event evidence sequence or fingerprint mismatch")
    for count in counts:
        if not hmac.compare_digest(count.evidence_sha256, _digest(_count_snapshot(count))):
            raise ConflictError("Surgical instrument count evidence fingerprint mismatch")
    return events, counts


def _seal_snapshot(row: SurgicalEvidenceJournal, events, counts):
    return {
        "id": row.id, "source_sha256": row.source_sha256,
        "status": row.status, "version": row.version,
        "sealed_at": row.sealed_at.isoformat(),
        "sealed_by_staff_id": row.sealed_by_staff_id,
        "seal_summary": row.seal_summary,
        "events": [(x.id, x.evidence_sha256) for x in events],
        "counts": [(x.id, x.evidence_sha256) for x in counts],
    }


def _integrity(row: SurgicalEvidenceJournal):
    if not hmac.compare_digest(row.source_sha256, _digest(_source_snapshot(row))):
        raise ConflictError("Surgical journal source fingerprint mismatch")
    events, counts = _children(row)
    if row.status == "sealed":
        if (row.sealed_at is None or row.sealed_by_staff_id is None
                or row.seal_sha256 is None or not hmac.compare_digest(
                    row.seal_sha256, _digest(_seal_snapshot(row, events, counts))
                )):
            raise ConflictError("Sealed surgical journal fingerprint mismatch")
    return events, counts


def _current_sources(case_id: int, clinic_id: int, surgeon_id: int, anesthetist_id: int):
    # S05 validates patient/order, booking, verified consent and both privileges.
    case, order, booking, consent = anesthesia_service._current_context(
        case_id, clinic_id, surgeon_id, anesthetist_id,
    )
    review = anesthesia_service.assert_current_preanesthesia_review(
        surgical_case_id=case.id, clinic_id=clinic_id,
    )
    if (review.surgeon_staff_id != surgeon_id or review.anesthetist_staff_id != anesthetist_id):
        raise ConflictError("Anesthetist review team does not match journal team")
    sign_in = checklist.get_checklist_phase(
        surgical_case_id=case.id, clinic_id=clinic_id, phase="sign_in",
    )
    time_out = checklist.get_checklist_phase(
        surgical_case_id=case.id, clinic_id=clinic_id, phase="time_out",
    )
    for attestation in (sign_in, time_out):
        if (attestation.procedure_order_id != order.id
                or attestation.procedure_order_version != order.version
                or attestation.theatre_booking_id != booking.id
                or attestation.booking_version != booking.version
                or attestation.surgical_consent_id != consent.id
                or attestation.surgeon_staff_id != surgeon_id
                or attestation.anesthetist_staff_id != anesthetist_id
                or attestation.patient_id != case.patient_id):
            raise ConflictError("Checklist attestations do not match current surgical context")
    return case, order, booking, consent, review, sign_in, time_out


def _journal(journal_id: int, clinic_id: int, *, lock: bool = False) -> SurgicalEvidenceJournal:
    query = select(SurgicalEvidenceJournal).where(
        SurgicalEvidenceJournal.id == _positive(journal_id, "journal_id"),
        SurgicalEvidenceJournal.clinic_id == _positive(clinic_id, "clinic_id"),
    )
    if lock:
        query = query.with_for_update()
    row = db.session.execute(query).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Surgical evidence journal not found")
    return row


def _require_current(row: SurgicalEvidenceJournal) -> None:
    case, order, booking, consent, review, sign_in, time_out = _current_sources(
        row.surgical_case_id, row.clinic_id,
        row.surgeon_staff_id, row.anesthetist_staff_id,
    )
    if (
        row.patient_id != case.patient_id or row.procedure_order_id != order.id
        or row.procedure_order_version != order.version
        or row.theatre_booking_id != booking.id or row.booking_version != booking.version
        or row.surgical_consent_id != consent.id
        or row.sign_in_attestation_id != sign_in.id
        or row.sign_in_sha256 != sign_in.evidence_sha256
        or row.time_out_attestation_id != time_out.id
        or row.time_out_sha256 != time_out.evidence_sha256
        or row.anesthesia_assessment_id != review.id
        or row.anesthesia_sha256 != review.evidence_sha256
        or row.counting_nurse_staff_id != sign_in.attested_by_staff_id
    ):
        raise ConflictError("Surgical journal source order, consent, team or theatre changed")


def _editable(row: SurgicalEvidenceJournal, expected_version: int):
    _integrity(row)
    if row.status != "draft":
        raise ConflictError("Sealed surgical documentation cannot be modified")
    if row.version != _positive(expected_version, "expected_version"):
        raise ConflictError("Surgical evidence journal version changed")
    _require_current(row)


@transactional
def create_surgical_evidence_journal(
    *, clinic_id: int, surgical_case_id: int, actor_staff_id: int,
    surgeon_staff_id: int, anesthetist_staff_id: int,
) -> SurgicalEvidenceJournal:
    clinic_id = _positive(clinic_id, "clinic_id")
    surgeon = _doctor(actor_staff_id, clinic_id)
    if surgeon.id != _positive(surgeon_staff_id, "surgeon_staff_id"):
        raise ConflictError("Only assigned privileged surgeon may create journal")
    anesthetist_staff_id = _positive(anesthetist_staff_id, "anesthetist_staff_id")
    case, order, booking, consent, review, sign_in, time_out = _current_sources(
        _positive(surgical_case_id, "surgical_case_id"), clinic_id,
        surgeon.id, anesthetist_staff_id,
    )
    nurse = _active_team_member(sign_in.attested_by_staff_id, clinic_id)
    if nurse.user.role != Role.NURSE:
        raise ConflictError("Sign-In counting nurse must be active")
    if db.session.execute(select(SurgicalEvidenceJournal.id).where(
        SurgicalEvidenceJournal.surgical_case_id == case.id,
    )).scalar_one_or_none() is not None:
        raise ConflictError("Surgical evidence journal already exists")
    row = SurgicalEvidenceJournal(
        clinic_id=clinic_id, patient_id=case.patient_id, surgical_case_id=case.id,
        procedure_order_id=order.id, procedure_order_version=order.version,
        theatre_booking_id=booking.id, booking_version=booking.version,
        surgical_consent_id=consent.id,
        sign_in_attestation_id=sign_in.id, sign_in_sha256=sign_in.evidence_sha256,
        time_out_attestation_id=time_out.id, time_out_sha256=time_out.evidence_sha256,
        anesthesia_assessment_id=review.id, anesthesia_sha256=review.evidence_sha256,
        surgeon_staff_id=surgeon.id, anesthetist_staff_id=anesthetist_staff_id,
        counting_nurse_staff_id=nurse.id, created_by_staff_id=surgeon.id,
        created_at=_now(), status="draft", version=1, source_sha256="0" * 64,
    )
    row.source_sha256 = _digest(_source_snapshot(row))
    db.session.add(row)
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="SurgicalEvidenceJournal", entity_id=row.id,
        description="Created non-executable operative evidence journal",
        new_value={"case_id": case.id, "version": 1, "source_sha256": row.source_sha256},
        clinic_id=clinic_id, patient_id=case.patient_id, user_id=surgeon.user_id,
    )
    return row


@transactional
def append_surgical_evidence_event(
    *, journal_id: int, clinic_id: int, actor_staff_id: int, expected_version: int,
    category: str, observed_at: datetime, note: str,
    related_staff_id: int | None = None,
) -> SurgicalEvidenceEvent:
    clinic_id = _positive(clinic_id, "clinic_id")
    row = _journal(journal_id, clinic_id, lock=True)
    _editable(row, expected_version)
    actor = _active_team_member(actor_staff_id, clinic_id)
    if actor.id not in (row.surgeon_staff_id, row.anesthetist_staff_id, row.counting_nurse_staff_id):
        raise ConflictError("Only assigned surgical team may append evidence")
    if category not in _EVENT_KINDS:
        raise ValidationError("Unsupported surgical evidence category")
    if category in ("team_joined", "team_left") and related_staff_id is None:
        raise ValidationError("Team attendance event requires related_staff_id")
    if related_staff_id is not None:
        _active_team_member(related_staff_id, clinic_id)
    events, _ = _children(row)
    event = SurgicalEvidenceEvent(
        clinic_id=clinic_id, journal_id=row.id, sequence=len(events) + 1,
        category=category, actor_staff_id=actor.id,
        related_staff_id=related_staff_id,
        observed_at=_time(observed_at), note=_required_text(note, "note", 2000),
        evidence_sha256="0" * 64,
    )
    db.session.add(event)
    db.session.flush()
    event.evidence_sha256 = _digest(_event_snapshot(event))
    row.version += 1
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="SurgicalEvidenceEvent", entity_id=event.id,
        description="Appended factual surgical documentation; no clinical execution transition",
        new_value={"journal_id": row.id, "category": category, "sequence": event.sequence,
                   "evidence_sha256": event.evidence_sha256},
        clinic_id=clinic_id, patient_id=row.patient_id, user_id=actor.user_id,
    )
    return event


@transactional
def record_surgical_instrument_count(
    *, journal_id: int, clinic_id: int, actor_staff_id: int, expected_version: int,
    item_kind: str, phase: str, observed_count: int,
    observed_at: datetime, note: str,
) -> SurgicalInstrumentCount:
    clinic_id = _positive(clinic_id, "clinic_id")
    row = _journal(journal_id, clinic_id, lock=True)
    _editable(row, expected_version)
    nurse = _active_team_member(actor_staff_id, clinic_id)
    if nurse.id != row.counting_nurse_staff_id or nurse.user.role != Role.NURSE:
        raise ConflictError("Only assigned Sign-In nurse may attest instrument counts")
    if item_kind not in _COUNT_KINDS or phase not in _COUNT_PHASES:
        raise ValidationError("Unknown surgical count category or phase")
    if isinstance(observed_count, bool) or not isinstance(observed_count, int) or observed_count < 0:
        raise ValidationError("observed_count must be nonnegative integer")
    _, counts = _children(row)
    if any(x.item_kind == item_kind and x.phase == phase for x in counts):
        raise ConflictError("Instrument count already recorded for phase and kind")
    if phase == "final" and not any(
        x.item_kind == item_kind and x.phase == "baseline" for x in counts
    ):
        raise ConflictError("Baseline count required before final reconciliation")
    count = SurgicalInstrumentCount(
        clinic_id=clinic_id, journal_id=row.id, item_kind=item_kind, phase=phase,
        observed_count=observed_count, counted_by_staff_id=nurse.id,
        observed_at=_time(observed_at), note=_required_text(note, "note", 1000),
        evidence_sha256="0" * 64,
    )
    db.session.add(count)
    db.session.flush()
    count.evidence_sha256 = _digest(_count_snapshot(count))
    row.version += 1
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="SurgicalInstrumentCount", entity_id=count.id,
        description="Appended inventory count attestation; no procedure completion",
        new_value={"journal_id": row.id, "item_kind": item_kind, "phase": phase,
                   "observed_count": observed_count, "evidence_sha256": count.evidence_sha256},
        clinic_id=clinic_id, patient_id=row.patient_id, user_id=nurse.user_id,
    )
    return count


@transactional
def seal_surgical_evidence_journal(
    *, journal_id: int, clinic_id: int, actor_staff_id: int,
    expected_version: int, summary: str,
) -> SurgicalEvidenceJournal:
    clinic_id = _positive(clinic_id, "clinic_id")
    row = _journal(journal_id, clinic_id, lock=True)
    _editable(row, expected_version)
    surgeon = _doctor(actor_staff_id, clinic_id)
    if surgeon.id != row.surgeon_staff_id:
        raise ConflictError("Only assigned privileged surgeon may seal documentation")
    events, counts = _children(row)
    if not events:
        raise ConflictError("Documented operative evidence events required before sealing")
    by_key = {(x.item_kind, x.phase): x for x in counts}
    if len(counts) != 6 or any((k, p) not in by_key for k in _COUNT_KINDS for p in _COUNT_PHASES):
        raise ConflictError("All baseline and final surgical counts required")
    if any(by_key[(k, "baseline")].observed_count != by_key[(k, "final")].observed_count
           for k in _COUNT_KINDS):
        raise ConflictError("Unreconciled surgical instrument, sponge or sharp counts")
    row.status = "sealed"
    row.version += 1
    row.sealed_at = _now()
    row.sealed_by_staff_id = surgeon.id
    row.seal_summary = _required_text(summary, "summary", 4000)
    row.seal_sha256 = _digest(_seal_snapshot(row, events, counts))
    db.session.flush()
    create_audit_log(
        action=AuditAction.STATUS_CHANGE, entity_type="SurgicalEvidenceJournal",
        entity_id=row.id, description="Sealed documentation evidence only; no operative completion",
        old_value={"status": "draft", "version": expected_version},
        new_value={"status": "sealed", "version": row.version,
                   "seal_sha256": row.seal_sha256},
        clinic_id=clinic_id, patient_id=row.patient_id, user_id=surgeon.user_id,
    )
    return row


def get_surgical_evidence_journal(*, journal_id: int, clinic_id: int) -> SurgicalEvidenceJournal:
    row = _journal(journal_id, clinic_id)
    _integrity(row)
    return row
