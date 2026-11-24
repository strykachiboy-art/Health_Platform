"""CE1-S10 read-only links to existing clinical, billing and audit evidence.

Each append-only reference certifies a source document's checked state at link
creation. It does NOT issue orders, change source records, administer medicine,
perform surgery, finalize care, transfer a patient, or create charges.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SurgicalClinicalReference(db.Model):
    __tablename__ = "surgical_clinical_references"
    __table_args__ = (
        db.UniqueConstraint("report_id", "source_kind", "source_id", name="uq_surgical_clinical_reference_source"),
        db.CheckConstraint(
            "source_kind IN ('emar','pharmacy','lab','billing','audit')",
            name="ck_surgical_clinical_reference_kind",
        ),
        db.CheckConstraint("source_id > 0", name="ck_surgical_clinical_reference_source_positive"),
        db.CheckConstraint(
            "length(source_sha256) = 64 AND length(report_sha256) = 64 "
            "AND length(signatures_sha256) = 64 AND length(evidence_sha256) = 64",
            name="ck_surgical_clinical_reference_digests",
        ),
        db.Index("ix_surgical_clinical_reference_clinic_report", "clinic_id", "report_id"),
        db.Index("ix_surgical_clinical_reference_clinic_kind", "clinic_id", "source_kind", "source_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)
    surgical_case_id = db.Column(db.Integer, db.ForeignKey("surgical_cases.id"), nullable=False)
    report_id = db.Column(db.Integer, db.ForeignKey("surgical_operative_reports.id"), nullable=False, index=True)
    source_kind = db.Column(db.String(16), nullable=False)
    source_id = db.Column(db.Integer, nullable=False)
    source_sha256 = db.Column(db.String(64), nullable=False)
    report_sha256 = db.Column(db.String(64), nullable=False)
    signatures_sha256 = db.Column(db.String(64), nullable=False)
    record_note = db.Column(db.String(1200), nullable=False)
    recorded_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False, default=_now)
    evidence_sha256 = db.Column(db.String(64), nullable=False)

    report = db.relationship("SurgicalOperativeReport")
