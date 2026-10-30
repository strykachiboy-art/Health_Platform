from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProcedureOrder(db.Model):
    __tablename__ = "procedure_orders"

    __table_args__ = (
        db.CheckConstraint(
            "procedure_type IN ("
            "'diagnostic', 'therapeutic', 'surgical', "
            "'bedside', 'interventional', 'other')",
            name="ck_procedure_orders_type",
        ),
        db.CheckConstraint(
            "priority IN ('routine', 'urgent', 'stat')",
            name="ck_procedure_orders_priority",
        ),
        db.CheckConstraint(
            "status IN ("
            "'pending', 'active', 'in_progress', "
            "'on_hold', 'completed', 'cancelled')",
            name="ck_procedure_orders_status",
        ),
        db.CheckConstraint(
            "consent_required = true OR consent_verified_at IS NULL",
            name="ck_procedure_orders_consent_timestamp",
        ),
        db.CheckConstraint(
            "status != 'completed' OR ("
            "completed_at IS NOT NULL AND "
            "completed_by_staff_id IS NOT NULL AND "
            "performed_at IS NOT NULL AND "
            "procedure_note IS NOT NULL)",
            name="ck_procedure_orders_completed_evidence",
        ),
        db.CheckConstraint(
            "status != 'cancelled' OR ("
            "cancelled_at IS NOT NULL AND "
            "cancelled_by_staff_id IS NOT NULL AND "
            "cancellation_reason IS NOT NULL)",
            name="ck_procedure_orders_cancelled_evidence",
        ),
        db.Index(
            "ix_procedure_orders_clinic_patient_ordered",
            "clinic_id", "patient_id", "ordered_at", "id",
        ),
        db.Index(
            "ix_procedure_orders_admission_status_ordered",
            "admission_id", "status", "ordered_at", "id",
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
    consultation_id = db.Column(
        db.Integer, db.ForeignKey("consultations.id"), nullable=True, index=True
    )

    ordered_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True
    )
    completed_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=True, index=True
    )
    cancelled_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=True, index=True
    )
    consent_verified_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=True, index=True
    )

    procedure_code = db.Column(db.String(64), nullable=True, index=True)
    procedure_name = db.Column(db.String(255), nullable=False)
    procedure_type = db.Column(db.String(32), nullable=False, index=True)
    body_site = db.Column(db.String(255), nullable=True)
    laterality = db.Column(db.String(32), nullable=True)
    indication = db.Column(db.Text, nullable=False)
    prep_instructions = db.Column(db.Text, nullable=True)
    anesthesia_type = db.Column(db.String(128), nullable=True)

    consent_required = db.Column(db.Boolean, nullable=False, default=False)
    consent_verified_at = db.Column(
        db.DateTime(timezone=True), nullable=True, index=True
    )

    priority = db.Column(
        db.String(16), nullable=False, default="routine", index=True
    )
    status = db.Column(
        db.String(20), nullable=False, default="active", index=True
    )

    scheduled_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    ordered_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    performed_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

    outcome_summary = db.Column(db.Text, nullable=True)
    procedure_note = db.Column(db.Text, nullable=True)

    completed_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    cancelled_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    cancellation_reason = db.Column(db.Text, nullable=True)

    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    clinic = db.relationship("Clinic")
    patient = db.relationship("Patient")
    admission = db.relationship("Admission")
    consultation = db.relationship("Consultation")
    ordered_by = db.relationship("Staff", foreign_keys=[ordered_by_staff_id])
    completed_by = db.relationship("Staff", foreign_keys=[completed_by_staff_id])
    cancelled_by = db.relationship("Staff", foreign_keys=[cancelled_by_staff_id])
    consent_verified_by = db.relationship(
        "Staff", foreign_keys=[consent_verified_by_staff_id]
    )
