"""CE1-S05 internal-only pre-anesthesia assessment and baseline observation.

This is *documentation*, never induction, anesthesia clearance, medication
administration, operative release, monitoring in theatre, or case completion.
No public Surgery route is added.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.surgery.models.anesthesia_model import (
    AnesthesiaBaselineObservation, AnesthesiaPreoperativeAssessment,
)
from app.modules.surgery.services.surgical_authorization_service import (
    _case_and_order, assert_surgical_consent_and_privileges,
)
from app.modules.surgery.services.surgical_case_service import _doctor, _positive
from app.modules.surgery.services.surgical_checklist_service import _active_booking
from app.modules.surgery.services.theatre_booking_service import _required_text

_TECHNIQUES = frozenset({"general", "regional", "local", "sedation"})
_AIRWAY = frozenset({"routine", "anticipated_difficult", "unknown"})
_ALLERGIES = frozenset({"known", "none_known", "unknown"})
_OXYGEN = frozenset({"ambient_air", "supplemental"})


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _enum(value, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(f"Invalid {label}")
    return value


def _int(value, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValidationError(f"Invalid {label}")
    return value


def _date(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("observed_at requires a timezone offset")
    result = value.astimezone(timezone.utc).replace(tzinfo=None)
    if result > _now():
        raise ValidationError("Future baseline observations are not permitted")
    return result


def _digest(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _current_context(case_id: int, clinic_id: int, surgeon_id: int, anesthetist_id: int):
    case, order = _case_and_order(case_id, clinic_id, lock=True)
    booking = _active_booking(case_id=case.id, clinic_id=clinic_id)
    consent = assert_surgical_consent_and_privileges(
        surgical_case_id=case.id, clinic_id=clinic_id,
        surgeon_staff_id=surgeon_id, anesthetist_staff_id=anesthetist_id,
    )
    return case, order, booking, consent


def _assessment(assessment_id: int, clinic_id: int, *, lock: bool = False):
    query = select(AnesthesiaPreoperativeAssessment).where(
        AnesthesiaPreoperativeAssessment.id == _positive(assessment_id, "assessment_id"),
        AnesthesiaPreoperativeAssessment.clinic_id == _positive(clinic_id, "clinic_id"),
    )
    if lock:
        query = query.with_for_update()
    row = db.session.execute(query).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Pre-anesthesia assessment not found")
    return row


def _validate_current(row: AnesthesiaPreoperativeAssessment):
    case, order, booking, consent = _current_context(
        row.surgical_case_id, row.clinic_id,
        row.surgeon_staff_id, row.anesthetist_staff_id,
    )
    if (
        row.patient_id != case.patient_id
        or row.procedure_order_id != order.id
        or row.procedure_order_version != order.version
        or row.theatre_booking_id != booking.id
        or row.booking_version != booking.version
        or row.surgical_consent_id != consent.id
    ):
        raise ConflictError("Anesthesia plan source consent, booking or order changed")
    return case, order, booking, consent


def _observations(row: AnesthesiaPreoperativeAssessment):
    return list(db.session.execute(
        select(AnesthesiaBaselineObservation)
        .where(AnesthesiaBaselineObservation.assessment_id == row.id,
               AnesthesiaBaselineObservation.clinic_id == row.clinic_id)
        .order_by(AnesthesiaBaselineObservation.sequence)
    ).scalars())


def _baseline_snapshot(obs: AnesthesiaBaselineObservation) -> dict:
    return {
        "id": obs.id, "clinic_id": obs.clinic_id,
        "assessment_id": obs.assessment_id, "sequence": obs.sequence,
        "observed_by_staff_id": obs.observed_by_staff_id,
        "observed_at": obs.observed_at.isoformat(),
        "heart_rate_bpm": obs.heart_rate_bpm,
        "systolic_bp": obs.systolic_bp, "diastolic_bp": obs.diastolic_bp,
        "spo2_percent": obs.spo2_percent, "respiratory_rate": obs.respiratory_rate,
        "temperature_tenths_c": obs.temperature_tenths_c,
        "oxygen_delivery": obs.oxygen_delivery,
        "observation_notes": obs.observation_notes,
    }


def _validated_observations(row: AnesthesiaPreoperativeAssessment):
    found = _observations(row)
    for sequence, obs in enumerate(found, start=1):
        if obs.sequence != sequence:
            raise ConflictError("Anesthesia baseline sequence mismatch")
        if not hmac.compare_digest(obs.evidence_sha256, _digest(_baseline_snapshot(obs))):
            raise ConflictError("Anesthesia baseline fingerprint mismatch")
    return found


def _assessment_snapshot(row: AnesthesiaPreoperativeAssessment, observations) -> dict:
    return {
        "id": row.id, "clinic_id": row.clinic_id, "patient_id": row.patient_id,
        "surgical_case_id": row.surgical_case_id,
        "procedure_order_id": row.procedure_order_id,
        "procedure_order_version": row.procedure_order_version,
        "theatre_booking_id": row.theatre_booking_id,
        "booking_version": row.booking_version,
        "surgical_consent_id": row.surgical_consent_id,
        "surgeon_staff_id": row.surgeon_staff_id,
        "anesthetist_staff_id": row.anesthetist_staff_id,
        "asa_class": row.asa_class, "technique": row.technique,
        "airway_assessment": row.airway_assessment, "airway_plan": row.airway_plan,
        "allergy_status": row.allergy_status, "allergy_notes": row.allergy_notes,
        "fasting_confirmed": row.fasting_confirmed, "fasting_notes": row.fasting_notes,
        "risk_and_comorbidity_notes": row.risk_and_comorbidity_notes,
        "plan_summary": row.plan_summary, "monitoring_plan": row.monitoring_plan,
        "contingency_plan": row.contingency_plan,
        "status": row.status, "version": row.version,
        "recorded_at": row.recorded_at.isoformat(),
        "attested_at": row.attested_at.isoformat() if row.attested_at else None,
        "attested_by_staff_id": row.attested_by_staff_id,
        "observations": [(o.id, o.evidence_sha256) for o in observations],
    }


def _integrity(row: AnesthesiaPreoperativeAssessment):
    observations = _validated_observations(row)
    if row.status == "attested":
        if not row.evidence_sha256 or not hmac.compare_digest(
            row.evidence_sha256, _digest(_assessment_snapshot(row, observations))
        ):
            raise ConflictError("Pre-anesthesia attestation fingerprint mismatch")
    return observations


@transactional
def record_preanesthesia_assessment(
    *, clinic_id: int, surgical_case_id: int,
    surgeon_staff_id: int, anesthetist_staff_id: int, actor_staff_id: int,
    asa_class: int, technique: str, airway_assessment: str, airway_plan: str,
    allergy_status: str, allergy_notes: str | None,
    fasting_confirmed: bool, fasting_notes: str,
    risk_and_comorbidity_notes: str, plan_summary: str,
    monitoring_plan: str, contingency_plan: str,
) -> AnesthesiaPreoperativeAssessment:
    clinic_id = _positive(clinic_id, "clinic_id")
    actor = _doctor(actor_staff_id, clinic_id)
    if actor.id != _positive(anesthetist_staff_id, "anesthetist_staff_id"):
        raise ConflictError("Only assigned anesthetist may document assessment")
    surgeon_staff_id = _positive(surgeon_staff_id, "surgeon_staff_id")
    case, order, booking, consent = _current_context(
        _positive(surgical_case_id, "surgical_case_id"), clinic_id,
        surgeon_staff_id, actor.id,
    )
    if db.session.execute(select(AnesthesiaPreoperativeAssessment.id).where(
        AnesthesiaPreoperativeAssessment.surgical_case_id == case.id
    )).scalar_one_or_none() is not None:
        raise ConflictError("Pre-anesthesia assessment already exists for surgical case")
    asa_class = _int(asa_class, 1, 6, "asa_class")
    technique = _enum(technique, _TECHNIQUES, "technique")
    airway_assessment = _enum(airway_assessment, _AIRWAY, "airway_assessment")
    allergy_status = _enum(allergy_status, _ALLERGIES, "allergy_status")
    if not isinstance(fasting_confirmed, bool):
        raise ValidationError("fasting_confirmed must be a boolean")
    allergy_notes = (
        _required_text(allergy_notes, "allergy_notes", 1000)
        if allergy_status == "known" else
        (allergy_notes.strip() or None if isinstance(allergy_notes, str) else None)
    )
    row = AnesthesiaPreoperativeAssessment(
        clinic_id=clinic_id, patient_id=case.patient_id, surgical_case_id=case.id,
        procedure_order_id=order.id, procedure_order_version=order.version,
        theatre_booking_id=booking.id, booking_version=booking.version,
        surgical_consent_id=consent.id,
        surgeon_staff_id=surgeon_staff_id, anesthetist_staff_id=actor.id,
        asa_class=asa_class, technique=technique,
        airway_assessment=airway_assessment,
        airway_plan=_required_text(airway_plan, "airway_plan", 1000),
        allergy_status=allergy_status, allergy_notes=allergy_notes,
        fasting_confirmed=fasting_confirmed,
        fasting_notes=_required_text(fasting_notes, "fasting_notes", 1000),
        risk_and_comorbidity_notes=_required_text(
            risk_and_comorbidity_notes, "risk_and_comorbidity_notes", 4000
        ),
        plan_summary=_required_text(plan_summary, "plan_summary", 4000),
        monitoring_plan=_required_text(monitoring_plan, "monitoring_plan", 2000),
        contingency_plan=_required_text(contingency_plan, "contingency_plan", 2000),
        status="draft", version=1, recorded_at=_now(),
    )
    db.session.add(row)
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="AnesthesiaPreoperativeAssessment",
        entity_id=row.id, description="Recorded anesthetist preoperative plan; not clearance",
        new_value={"case_id": case.id, "status": "draft", "version": 1},
        clinic_id=clinic_id, patient_id=case.patient_id, user_id=actor.user_id,
    )
    return row


@transactional
def add_anesthesia_baseline(
    *, assessment_id: int, clinic_id: int, actor_staff_id: int,
    expected_version: int, observed_at: datetime,
    heart_rate_bpm: int, systolic_bp: int, diastolic_bp: int,
    spo2_percent: int, respiratory_rate: int, temperature_tenths_c: int,
    oxygen_delivery: str, observation_notes: str,
) -> AnesthesiaBaselineObservation:
    clinic_id = _positive(clinic_id, "clinic_id")
    actor = _doctor(actor_staff_id, clinic_id)
    row = _assessment(assessment_id, clinic_id, lock=True)
    if actor.id != row.anesthetist_staff_id:
        raise ConflictError("Only assigned anesthetist may append baseline")
    if row.status != "draft":
        raise ConflictError("Attested anesthesia assessment cannot be modified")
    if row.version != _positive(expected_version, "expected_version"):
        raise ConflictError("Anesthesia assessment version changed")
    _validate_current(row)
    prior = _validated_observations(row)
    timestamp = _date(observed_at)
    hr = _int(heart_rate_bpm, 1, 300, "heart_rate_bpm")
    sys_bp = _int(systolic_bp, 1, 350, "systolic_bp")
    dia_bp = _int(diastolic_bp, 1, 250, "diastolic_bp")
    if sys_bp < dia_bp:
        raise ValidationError("systolic_bp must be at least diastolic_bp")
    o = AnesthesiaBaselineObservation(
        clinic_id=clinic_id, assessment_id=row.id, sequence=len(prior) + 1,
        observed_by_staff_id=actor.id, observed_at=timestamp,
        heart_rate_bpm=hr, systolic_bp=sys_bp, diastolic_bp=dia_bp,
        spo2_percent=_int(spo2_percent, 0, 100, "spo2_percent"),
        respiratory_rate=_int(respiratory_rate, 1, 100, "respiratory_rate"),
        temperature_tenths_c=_int(temperature_tenths_c, 200, 450, "temperature_tenths_c"),
        oxygen_delivery=_enum(oxygen_delivery, _OXYGEN, "oxygen_delivery"),
        observation_notes=_required_text(observation_notes, "observation_notes", 1000),
        evidence_sha256="0" * 64,
    )
    db.session.add(o)
    db.session.flush()
    o.evidence_sha256 = _digest(_baseline_snapshot(o))
    row.version += 1
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="AnesthesiaBaselineObservation",
        entity_id=o.id, description="Recorded preoperative baseline observation, not intraoperative monitoring",
        new_value={"assessment_id": row.id, "sequence": o.sequence,
                   "evidence_sha256": o.evidence_sha256, "assessment_version": row.version},
        clinic_id=clinic_id, patient_id=row.patient_id, user_id=actor.user_id,
    )
    return o


@transactional
def attest_preanesthesia_review(
    *, assessment_id: int, clinic_id: int, actor_staff_id: int,
    expected_version: int,
) -> AnesthesiaPreoperativeAssessment:
    clinic_id = _positive(clinic_id, "clinic_id")
    actor = _doctor(actor_staff_id, clinic_id)
    row = _assessment(assessment_id, clinic_id, lock=True)
    if actor.id != row.anesthetist_staff_id:
        raise ConflictError("Assigned anesthetist must attest own assessment")
    if row.status != "draft":
        raise ConflictError("Pre-anesthesia review already attested")
    if row.version != _positive(expected_version, "expected_version"):
        raise ConflictError("Anesthesia assessment version changed")
    _validate_current(row)
    if row.airway_assessment == "unknown":
        raise ConflictError("Airway assessment must be documented before review")
    if row.allergy_status == "unknown":
        raise ConflictError("Allergy status must be resolved before review")
    if row.fasting_confirmed is not True:
        raise ConflictError("Fasting status must be confirmed before review")
    observations = _validated_observations(row)
    if not observations:
        raise ConflictError("Recent preoperative baseline required before review")
    now = _now()
    if observations[-1].observed_at < now - timedelta(hours=24):
        raise ConflictError("Preoperative baseline observation is stale")
    row.status = "attested"
    row.version += 1
    row.attested_at = now
    row.attested_by_staff_id = actor.id
    row.evidence_sha256 = _digest(_assessment_snapshot(row, observations))
    db.session.flush()
    create_audit_log(
        action=AuditAction.STATUS_CHANGE, entity_type="AnesthesiaPreoperativeAssessment",
        entity_id=row.id, description="Attested preoperative documentation only; NOT anesthesia clearance",
        old_value={"status": "draft", "version": expected_version},
        new_value={"status": "attested", "version": row.version,
                   "evidence_sha256": row.evidence_sha256},
        clinic_id=clinic_id, patient_id=row.patient_id, user_id=actor.user_id,
    )
    return row


def get_preanesthesia_assessment(*, assessment_id: int, clinic_id: int):
    row = _assessment(assessment_id, clinic_id)
    _integrity(row)
    return row


def assert_current_preanesthesia_review(*, surgical_case_id: int, clinic_id: int):
    """Internal evidence check only; does NOT authorize induction/operation."""
    row = db.session.execute(select(AnesthesiaPreoperativeAssessment).where(
        AnesthesiaPreoperativeAssessment.surgical_case_id == _positive(
            surgical_case_id, "surgical_case_id"),
        AnesthesiaPreoperativeAssessment.clinic_id == _positive(clinic_id, "clinic_id"),
    )).scalar_one_or_none()
    if row is None or row.status != "attested":
        raise ConflictError("Current anesthetist-attested preoperative review required")
    _integrity(row)
    _validate_current(row)
    if not row.fasting_confirmed or row.allergy_status == "unknown" or row.airway_assessment == "unknown":
        raise ConflictError("Preoperative documentation no longer meets review requirements")
    observations = _validated_observations(row)
    if not observations or observations[-1].observed_at < _now() - timedelta(hours=24):
        raise ConflictError("Current preoperative baseline required")
    return row
