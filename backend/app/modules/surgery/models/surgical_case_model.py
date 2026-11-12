"""CE1-S01: a non-executable, admission-bound surgical case foundation.

SurgicalCase references the existing ProcedureOrder as its sole request authority.
No theatre/consent/checklist/clinical execution transitions are implemented here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SurgicalCase(db.Model):
    __tablename__ = "surgical_cases"

    __table_args__ = (
        db.UniqueConstraint(
            "procedure_order_id",
            name="uq_surgical_cases_procedure_order",
        ),
        db.CheckConstraint(
            "status IN ('planned', 'on_hold', 'cancelled')",
            name="ck_surgical_cases_foundation_status",
        ),
        db.CheckConstraint(
            "version >= 1",
            name="ck_surgical_cases_version_positive",
        ),
        db.Index(
            "ix_surgical_cases_clinic_admission_status",
            "clinic_id", "admission_id", "status", "id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(
        db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True
    )
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patients.id"), nullable=False, index=True
    )
    admission_id = db.Column(
        db.Integer, db.ForeignKey("admissions.id"), nullable=False, index=True
    )
    procedure_order_id = db.Column(
        db.Integer, db.ForeignKey("procedure_orders.id"), nullable=False
    )
    planned_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True
    )
    status = db.Column(
        db.String(24), nullable=False, default="planned", index=True
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    clinic = db.relationship("Clinic")
    patient = db.relationship("Patient")
    admission = db.relationship("Admission")
    procedure_order = db.relationship("ProcedureOrder")
    planned_by = db.relationship("Staff", foreign_keys=[planned_by_staff_id])
