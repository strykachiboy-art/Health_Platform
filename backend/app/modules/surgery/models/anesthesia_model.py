"""CE1-S05 pre-anesthesia documentation, not anesthesia administration or clearance.

A reviewed assessment attests to a documented plan. It does not authorize
anesthetic induction, a medicine administration, or operative progression.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AnesthesiaPreoperativeAssessment(db.Model):
    __tablename__ = "anesthesia_preoperative_assessments"
    __table_args__ = (
        db.UniqueConstraint("surgical_case_id", name="uq_anesthesia_assessment_case"),
        db.CheckConstraint("asa_class BETWEEN 1 AND 6", name="ck_anesthesia_assessment_asa"),
        db.CheckConstraint("technique IN ('general','regional','local','sedation')",
                           name="ck_anesthesia_assessment_technique"),
        db.CheckConstraint("airway_assessment IN ('routine','anticipated_difficult','unknown')",
                           name="ck_anesthesia_assessment_airway"),
        db.CheckConstraint("allergy_status IN ('known','none_known','unknown')",
                           name="ck_anesthesia_assessment_allergy"),
        db.CheckConstraint("status IN ('draft','attested')",
                           name="ck_anesthesia_assessment_status"),
        db.CheckConstraint("version >= 1 AND procedure_order_version >= 1 AND booking_version >= 1",
                           name="ck_anesthesia_assessment_versions"),
        db.CheckConstraint(
            "status != 'attested' OR (attested_at IS NOT NULL AND "
            "attested_by_staff_id IS NOT NULL AND evidence_sha256 IS NOT NULL AND "
            "length(evidence_sha256) = 64)",
            name="ck_anesthesia_assessment_attestation_evidence",
        ),
        db.Index("ix_anesthesia_assessment_clinic_case", "clinic_id", "surgical_case_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    surgical_case_id = db.Column(db.Integer, db.ForeignKey("surgical_cases.id"), nullable=False)
    procedure_order_id = db.Column(db.Integer, db.ForeignKey("procedure_orders.id"), nullable=False)
    procedure_order_version = db.Column(db.Integer, nullable=False)
    theatre_booking_id = db.Column(db.Integer, db.ForeignKey("theatre_bookings.id"), nullable=False)
    booking_version = db.Column(db.Integer, nullable=False)
    surgical_consent_id = db.Column(db.Integer, db.ForeignKey("surgical_consent_evidence.id"), nullable=False)
    surgeon_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    anesthetist_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    asa_class = db.Column(db.Integer, nullable=False)
    technique = db.Column(db.String(16), nullable=False)
    airway_assessment = db.Column(db.String(32), nullable=False)
    airway_plan = db.Column(db.String(1000), nullable=False)
    allergy_status = db.Column(db.String(16), nullable=False)
    allergy_notes = db.Column(db.String(1000), nullable=True)
    fasting_confirmed = db.Column(db.Boolean, nullable=False)
    fasting_notes = db.Column(db.String(1000), nullable=False)
    risk_and_comorbidity_notes = db.Column(db.String(4000), nullable=False)
    plan_summary = db.Column(db.String(4000), nullable=False)
    monitoring_plan = db.Column(db.String(2000), nullable=False)
    contingency_plan = db.Column(db.String(2000), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="draft")
    version = db.Column(db.Integer, nullable=False, default=1)
    recorded_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    attested_at = db.Column(db.DateTime, nullable=True)
    attested_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=True)
    evidence_sha256 = db.Column(db.String(64), nullable=True)

    surgical_case = db.relationship("SurgicalCase")
    booking = db.relationship("TheatreBooking")
    consent = db.relationship("SurgicalConsentEvidence")
    anesthetist = db.relationship("Staff", foreign_keys=[anesthetist_staff_id])
    attested_by = db.relationship("Staff", foreign_keys=[attested_by_staff_id])


class AnesthesiaBaselineObservation(db.Model):
    __tablename__ = "anesthesia_baseline_observations"
    __table_args__ = (
        db.UniqueConstraint("assessment_id", "sequence", name="uq_anesthesia_baseline_sequence"),
        db.CheckConstraint("sequence >= 1", name="ck_anesthesia_baseline_sequence"),
        db.CheckConstraint("heart_rate_bpm BETWEEN 1 AND 300", name="ck_anesthesia_baseline_heart_rate"),
        db.CheckConstraint("systolic_bp BETWEEN 1 AND 350 AND diastolic_bp BETWEEN 1 AND 250 "
                           "AND systolic_bp >= diastolic_bp", name="ck_anesthesia_baseline_bp"),
        db.CheckConstraint("spo2_percent BETWEEN 0 AND 100", name="ck_anesthesia_baseline_spo2"),
        db.CheckConstraint("respiratory_rate BETWEEN 1 AND 100", name="ck_anesthesia_baseline_resp"),
        db.CheckConstraint("temperature_tenths_c BETWEEN 200 AND 450", name="ck_anesthesia_baseline_temp"),
        db.CheckConstraint("oxygen_delivery IN ('ambient_air','supplemental')",
                           name="ck_anesthesia_baseline_oxygen"),
        db.CheckConstraint("length(evidence_sha256) = 64", name="ck_anesthesia_baseline_digest"),
        db.Index("ix_anesthesia_baseline_clinic_assessment", "clinic_id", "assessment_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    assessment_id = db.Column(
        db.Integer, db.ForeignKey("anesthesia_preoperative_assessments.id"),
        nullable=False, index=True,
    )
    sequence = db.Column(db.Integer, nullable=False)
    observed_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    observed_at = db.Column(db.DateTime, nullable=False)
    heart_rate_bpm = db.Column(db.Integer, nullable=False)
    systolic_bp = db.Column(db.Integer, nullable=False)
    diastolic_bp = db.Column(db.Integer, nullable=False)
    spo2_percent = db.Column(db.Integer, nullable=False)
    respiratory_rate = db.Column(db.Integer, nullable=False)
    temperature_tenths_c = db.Column(db.Integer, nullable=False)
    oxygen_delivery = db.Column(db.String(16), nullable=False)
    observation_notes = db.Column(db.String(1000), nullable=False)
    evidence_sha256 = db.Column(db.String(64), nullable=False)

    assessment = db.relationship("AnesthesiaPreoperativeAssessment")
    observed_by = db.relationship("Staff", foreign_keys=[observed_by_staff_id])
