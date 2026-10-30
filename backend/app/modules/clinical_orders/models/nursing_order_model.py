from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NursingOrder(db.Model):
    __tablename__ = "nursing_orders"

    __table_args__ = (
        db.CheckConstraint(
            "category IN ("
            "'monitoring', 'activity', 'positioning', "
            "'wound_care', 'intake_output', 'safety', "
            "'specimen', 'respiratory', 'catheter', "
            "'hygiene', 'communication', 'other')",
            name="ck_nursing_orders_category",
        ),
        db.CheckConstraint(
            "frequency IN ("
            "'once', 'q1h', 'q2h', 'q4h', 'q6h', "
            "'q8h', 'q12h', 'daily', 'prn', "
            "'continuous', 'custom')",
            name="ck_nursing_orders_frequency",
        ),
        db.CheckConstraint(
            "frequency != 'custom' OR custom_frequency IS NOT NULL",
            name="ck_nursing_orders_custom_frequency",
        ),
        db.CheckConstraint(
            "priority IN ('routine', 'urgent', 'stat')",
            name="ck_nursing_orders_priority",
        ),
        db.CheckConstraint(
            "status IN ("
            "'pending', 'active', 'in_progress', "
            "'on_hold', 'completed', 'cancelled')",
            name="ck_nursing_orders_status",
        ),
        db.CheckConstraint(
            "status != 'completed' "
            "OR (completed_at IS NOT NULL "
            "AND completed_by_staff_id IS NOT NULL "
            "AND completion_note IS NOT NULL)",
            name="ck_nursing_orders_completed_evidence",
        ),
        db.CheckConstraint(
            "status != 'cancelled' "
            "OR (cancelled_at IS NOT NULL "
            "AND cancelled_by_staff_id IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_nursing_orders_cancelled_evidence",
        ),
        db.Index(
            "ix_nursing_orders_clinic_patient_ordered",
            "clinic_id",
            "patient_id",
            "ordered_at",
            "id",
        ),
        db.Index(
            "ix_nursing_orders_admission_status_due",
            "admission_id",
            "status",
            "scheduled_at",
            "id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )
    patient_id = db.Column(
        db.Integer,
        db.ForeignKey("patients.id"),
        nullable=False,
        index=True,
    )
    admission_id = db.Column(
        db.Integer,
        db.ForeignKey("admissions.id"),
        nullable=False,
        index=True,
    )
    consultation_id = db.Column(
        db.Integer,
        db.ForeignKey("consultations.id"),
        nullable=True,
        index=True,
    )

    ordered_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )
    assigned_to_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )
    completed_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )
    cancelled_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    category = db.Column(
        db.String(32),
        nullable=False,
        index=True,
    )
    order_text = db.Column(
        db.Text,
        nullable=False,
    )
    instructions = db.Column(
        db.Text,
        nullable=True,
    )

    frequency = db.Column(
        db.String(24),
        nullable=False,
        index=True,
    )
    custom_frequency = db.Column(
        db.String(255),
        nullable=True,
    )

    priority = db.Column(
        db.String(16),
        nullable=False,
        default="routine",
        index=True,
    )
    status = db.Column(
        db.String(20),
        nullable=False,
        default="active",
        index=True,
    )

    scheduled_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    ordered_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )

    completion_note = db.Column(
        db.Text,
        nullable=True,
    )
    completed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    cancelled_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    cancellation_reason = db.Column(
        db.Text,
        nullable=True,
    )

    version = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
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

    ordered_by = db.relationship(
        "Staff",
        foreign_keys=[ordered_by_staff_id],
    )
    assigned_to = db.relationship(
        "Staff",
        foreign_keys=[assigned_to_staff_id],
    )
    completed_by = db.relationship(
        "Staff",
        foreign_keys=[completed_by_staff_id],
    )
    cancelled_by = db.relationship(
        "Staff",
        foreign_keys=[cancelled_by_staff_id],
    )

    executions = db.relationship(
        "NursingOrderExecution",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="NursingOrderExecution.performed_at",
    )


class NursingOrderExecution(db.Model):
    __tablename__ = "nursing_order_executions"

    __table_args__ = (
        db.Index(
            "ix_nursing_order_executions_order_performed",
            "nursing_order_id",
            "performed_at",
            "id",
        ),
        db.Index(
            "ix_nursing_order_executions_admission_performed",
            "admission_id",
            "performed_at",
            "id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )
    nursing_order_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "nursing_orders.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )
    patient_id = db.Column(
        db.Integer,
        db.ForeignKey("patients.id"),
        nullable=False,
        index=True,
    )
    admission_id = db.Column(
        db.Integer,
        db.ForeignKey("admissions.id"),
        nullable=False,
        index=True,
    )
    performed_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    performed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )
    result_note = db.Column(
        db.Text,
        nullable=False,
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    order = db.relationship(
        "NursingOrder",
        back_populates="executions",
    )
    clinic = db.relationship("Clinic")
    patient = db.relationship("Patient")
    admission = db.relationship("Admission")
    performed_by = db.relationship(
        "Staff",
        foreign_keys=[performed_by_staff_id],
    )
