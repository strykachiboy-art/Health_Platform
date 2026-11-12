"""CE1-S08: PACU preparation and nursing review evidence only.

Review is NOT an admission to PACU, nursing responsibility transfer, operative
completion, nursing handoff ACKNOWLEDGED, Sign-Out or discharge authorization.
"""
from __future__ import annotations

from datetime import datetime, timezone
from app.extensions import db


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SurgicalPacuPreparation(db.Model):
    __tablename__ = 'surgical_pacu_preparations'
    __table_args__ = (
        db.UniqueConstraint('surgical_case_id', name='uq_surgical_pacu_preparation_case'),
        db.UniqueConstraint('journal_id', name='uq_surgical_pacu_preparation_journal'),
        db.CheckConstraint("status IN ('draft','prepared','reviewed')", name='ck_surgical_pacu_preparation_status'),
        db.CheckConstraint('version >= 1', name='ck_surgical_pacu_preparation_version'),
        db.CheckConstraint('outgoing_nurse_staff_id != receiving_nurse_staff_id', name='ck_surgical_pacu_distinct_nurses'),
        db.CheckConstraint('length(source_sha256) = 64 AND length(content_sha256) = 64',
                           name='ck_surgical_pacu_preparation_digests'),
        db.CheckConstraint("status = 'draft' OR (prepared_at IS NOT NULL AND prepared_by_staff_id IS NOT NULL "
                           "AND prepared_version IS NOT NULL AND prepared_sha256 IS NOT NULL "
                           "AND length(prepared_sha256) = 64)", name='ck_surgical_pacu_preparation_prepared'),
        db.CheckConstraint("status != 'reviewed' OR (reviewed_at IS NOT NULL AND reviewed_by_staff_id IS NOT NULL "
                           "AND reviewed_version IS NOT NULL AND review_sha256 IS NOT NULL "
                           "AND length(review_sha256) = 64)", name='ck_surgical_pacu_preparation_reviewed'),
        db.Index('ix_surgical_pacu_clinic_admission_status', 'clinic_id', 'admission_id', 'status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    admission_id = db.Column(db.Integer, db.ForeignKey('admissions.id'), nullable=False)
    surgical_case_id = db.Column(db.Integer, db.ForeignKey('surgical_cases.id'), nullable=False)
    journal_id = db.Column(db.Integer, db.ForeignKey('surgical_evidence_journals.id'), nullable=False)
    journal_seal_sha256 = db.Column(db.String(64), nullable=False)
    case_version = db.Column(db.Integer, nullable=False)
    booking_version = db.Column(db.Integer, nullable=False)
    outgoing_nurse_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    receiving_nurse_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    created_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    situation = db.Column(db.String(2000), nullable=False)
    background = db.Column(db.String(2000), nullable=False)
    assessment = db.Column(db.String(2000), nullable=False)
    recommendation = db.Column(db.String(2000), nullable=False)
    safety_concerns = db.Column(db.String(2000), nullable=False)
    pending_actions = db.Column(db.String(2000), nullable=False)
    airway_plan = db.Column(db.String(1200), nullable=False)
    analgesia_plan = db.Column(db.String(1200), nullable=False)
    escalation_plan = db.Column(db.String(1200), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='draft')
    version = db.Column(db.Integer, nullable=False, default=1)
    source_sha256 = db.Column(db.String(64), nullable=False)
    content_sha256 = db.Column(db.String(64), nullable=False)
    prepared_at = db.Column(db.DateTime, nullable=True)
    prepared_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=True)
    prepared_version = db.Column(db.Integer, nullable=True)
    prepared_sha256 = db.Column(db.String(64), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=True)
    reviewed_version = db.Column(db.Integer, nullable=True)
    review_sha256 = db.Column(db.String(64), nullable=True)
    journal = db.relationship('SurgicalEvidenceJournal')


class SurgicalPacuObservation(db.Model):
    __tablename__ = 'surgical_pacu_observations'
    __table_args__ = (
        db.UniqueConstraint('preparation_id', 'sequence', name='uq_surgical_pacu_observation_sequence'),
        db.CheckConstraint('sequence >= 1', name='ck_surgical_pacu_observation_sequence'),
        db.CheckConstraint('heart_rate_bpm BETWEEN 20 AND 250', name='ck_surgical_pacu_heart_rate'),
        db.CheckConstraint('systolic_bp BETWEEN 40 AND 300', name='ck_surgical_pacu_systolic'),
        db.CheckConstraint('diastolic_bp BETWEEN 20 AND 200 AND diastolic_bp < systolic_bp',
                           name='ck_surgical_pacu_diastolic'),
        db.CheckConstraint('spo2_percent BETWEEN 0 AND 100', name='ck_surgical_pacu_spo2'),
        db.CheckConstraint('respiratory_rate BETWEEN 1 AND 70', name='ck_surgical_pacu_respiration'),
        db.CheckConstraint('temperature_tenths_c BETWEEN 300 AND 450', name='ck_surgical_pacu_temperature'),
        db.CheckConstraint('pain_score BETWEEN 0 AND 10', name='ck_surgical_pacu_pain'),
        db.CheckConstraint("consciousness IN ('alert','voice','pain','unresponsive')", name='ck_surgical_pacu_consciousness'),
        db.CheckConstraint('length(evidence_sha256) = 64', name='ck_surgical_pacu_observation_digest'),
        db.Index('ix_surgical_pacu_observation_clinic_preparation', 'clinic_id', 'preparation_id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    preparation_id = db.Column(db.Integer, db.ForeignKey('surgical_pacu_preparations.id'), nullable=False, index=True)
    sequence = db.Column(db.Integer, nullable=False)
    observed_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    observed_at = db.Column(db.DateTime, nullable=False)
    heart_rate_bpm = db.Column(db.Integer, nullable=False)
    systolic_bp = db.Column(db.Integer, nullable=False)
    diastolic_bp = db.Column(db.Integer, nullable=False)
    spo2_percent = db.Column(db.Integer, nullable=False)
    respiratory_rate = db.Column(db.Integer, nullable=False)
    temperature_tenths_c = db.Column(db.Integer, nullable=False)
    pain_score = db.Column(db.Integer, nullable=False)
    consciousness = db.Column(db.String(16), nullable=False)
    airway_patent = db.Column(db.Boolean, nullable=False)
    bleeding_concern = db.Column(db.Boolean, nullable=False)
    escalation_communicated = db.Column(db.Boolean, nullable=False)
    note = db.Column(db.String(1200), nullable=False)
    evidence_sha256 = db.Column(db.String(64), nullable=False)
    preparation = db.relationship('SurgicalPacuPreparation')
