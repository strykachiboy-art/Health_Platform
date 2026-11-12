"""CE1-S06: non-executable, case-scoped intraoperative evidence journal.

This is retrospective/event documentation only; no incision, anesthesia induction,
eMAR administration, operation start/end, discharge, or Sign-Out authority.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SurgicalEvidenceJournal(db.Model):
    __tablename__ = "surgical_evidence_journals"
    __table_args__ = (
        db.UniqueConstraint("surgical_case_id", name="uq_surgical_evidence_journal_case"),
        db.CheckConstraint("status IN ('draft','sealed')", name="ck_surgical_evidence_journal_status"),
        db.CheckConstraint("version >= 1 AND procedure_order_version >= 1 AND booking_version >= 1",
                           name="ck_surgical_evidence_journal_versions"),
        db.CheckConstraint("length(source_sha256) = 64", name="ck_surgical_evidence_journal_source_digest"),
        db.CheckConstraint(
            "status != 'sealed' OR (sealed_at IS NOT NULL AND sealed_by_staff_id IS NOT NULL "
            "AND seal_summary IS NOT NULL AND seal_sha256 IS NOT NULL AND length(seal_sha256) = 64)",
            name="ck_surgical_evidence_journal_seal_evidence",
        ),
        db.Index("ix_surgical_evidence_journal_clinic_case", "clinic_id", "surgical_case_id"),
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
    sign_in_attestation_id = db.Column(db.Integer, db.ForeignKey("surgical_safety_checklist_attestations.id"), nullable=False)
    sign_in_sha256 = db.Column(db.String(64), nullable=False)
    time_out_attestation_id = db.Column(db.Integer, db.ForeignKey("surgical_safety_checklist_attestations.id"), nullable=False)
    time_out_sha256 = db.Column(db.String(64), nullable=False)
    anesthesia_assessment_id = db.Column(db.Integer, db.ForeignKey("anesthesia_preoperative_assessments.id"), nullable=False)
    anesthesia_sha256 = db.Column(db.String(64), nullable=False)
    surgeon_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    anesthetist_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    counting_nurse_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    created_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    source_sha256 = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="draft")
    version = db.Column(db.Integer, nullable=False, default=1)
    sealed_at = db.Column(db.DateTime, nullable=True)
    sealed_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=True)
    seal_summary = db.Column(db.String(4000), nullable=True)
    seal_sha256 = db.Column(db.String(64), nullable=True)

    case = db.relationship("SurgicalCase")
    booking = db.relationship("TheatreBooking")
    consent = db.relationship("SurgicalConsentEvidence")


class SurgicalEvidenceEvent(db.Model):
    __tablename__ = "surgical_evidence_events"
    __table_args__ = (
        db.UniqueConstraint("journal_id", "sequence", name="uq_surgical_evidence_event_sequence"),
        db.CheckConstraint("sequence >= 1", name="ck_surgical_evidence_event_sequence"),
        db.CheckConstraint(
            "category IN ('team_joined','team_left','safety_observation','equipment_issue','clinical_note')",
            name="ck_surgical_evidence_event_category",
        ),
        db.CheckConstraint("length(evidence_sha256) = 64", name="ck_surgical_evidence_event_digest"),
        db.Index("ix_surgical_evidence_event_clinic_journal", "clinic_id", "journal_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    journal_id = db.Column(db.Integer, db.ForeignKey("surgical_evidence_journals.id"), nullable=False, index=True)
    sequence = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(32), nullable=False)
    actor_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    related_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=True)
    observed_at = db.Column(db.DateTime, nullable=False)
    note = db.Column(db.String(2000), nullable=False)
    evidence_sha256 = db.Column(db.String(64), nullable=False)

    journal = db.relationship("SurgicalEvidenceJournal")


class SurgicalInstrumentCount(db.Model):
    __tablename__ = "surgical_instrument_counts"
    __table_args__ = (
        db.UniqueConstraint("journal_id", "item_kind", "phase", name="uq_surgical_instrument_count_phase"),
        db.CheckConstraint("item_kind IN ('instruments','sponges','sharps')", name="ck_surgical_instrument_count_kind"),
        db.CheckConstraint("phase IN ('baseline','final')", name="ck_surgical_instrument_count_phase"),
        db.CheckConstraint("observed_count >= 0", name="ck_surgical_instrument_count_nonnegative"),
        db.CheckConstraint("length(evidence_sha256) = 64", name="ck_surgical_instrument_count_digest"),
        db.Index("ix_surgical_instrument_count_clinic_journal", "clinic_id", "journal_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    journal_id = db.Column(db.Integer, db.ForeignKey("surgical_evidence_journals.id"), nullable=False, index=True)
    item_kind = db.Column(db.String(16), nullable=False)
    phase = db.Column(db.String(16), nullable=False)
    observed_count = db.Column(db.Integer, nullable=False)
    counted_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    observed_at = db.Column(db.DateTime, nullable=False)
    note = db.Column(db.String(1000), nullable=False)
    evidence_sha256 = db.Column(db.String(64), nullable=False)

    journal = db.relationship("SurgicalEvidenceJournal")
