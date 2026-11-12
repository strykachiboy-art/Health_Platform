"""CE1-S04: append-only surgical safety checklist phase attestations.

This is evidence of a *staff attestation*, not proof that an operation took place.
S04 never changes SurgicalCase or ProcedureOrder execution status.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SurgicalSafetyChecklistAttestation(db.Model):
    __tablename__ = "surgical_safety_checklist_attestations"
    __table_args__ = (
        db.UniqueConstraint(
            "surgical_case_id", "phase", name="uq_surgical_checklist_case_phase"
        ),
        db.CheckConstraint(
            "phase IN ('sign_in', 'time_out', 'sign_out')",
            name="ck_surgical_checklist_phase",
        ),
        db.CheckConstraint(
            "length(evidence_sha256) = 64",
            name="ck_surgical_checklist_evidence_digest",
        ),
        db.CheckConstraint(
            "procedure_order_version >= 1 AND booking_version >= 1",
            name="ck_surgical_checklist_source_versions",
        ),
        db.CheckConstraint(
            "surgeon_staff_id != anesthetist_staff_id",
            name="ck_surgical_checklist_distinct_professionals",
        ),
        db.Index(
            "ix_surgical_checklist_clinic_case_phase",
            "clinic_id", "surgical_case_id", "phase",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)
    surgical_case_id = db.Column(db.Integer, db.ForeignKey("surgical_cases.id"), nullable=False)
    procedure_order_id = db.Column(db.Integer, db.ForeignKey("procedure_orders.id"), nullable=False)
    procedure_order_version = db.Column(db.Integer, nullable=False)
    theatre_booking_id = db.Column(db.Integer, db.ForeignKey("theatre_bookings.id"), nullable=False)
    booking_version = db.Column(db.Integer, nullable=False)
    surgical_consent_id = db.Column(db.Integer, db.ForeignKey("surgical_consent_evidence.id"), nullable=False)
    surgeon_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    anesthetist_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    attested_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    phase = db.Column(db.String(16), nullable=False)
    answers = db.Column(db.JSON, nullable=False)
    evidence_sha256 = db.Column(db.String(64), nullable=False)
    attested_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    surgical_case = db.relationship("SurgicalCase")
    theatre_booking = db.relationship("TheatreBooking")
    consent = db.relationship("SurgicalConsentEvidence")
    attested_by = db.relationship("Staff", foreign_keys=[attested_by_staff_id])
