from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DietOrder(db.Model):
    __tablename__ = "diet_orders"

    __table_args__ = (
        db.CheckConstraint(
            "diet_type IN ("
            "'regular', 'clear_liquid', 'full_liquid', 'soft', "
            "'diabetic', 'renal', 'cardiac', 'low_sodium', "
            "'high_protein', 'npo', 'enteral', 'custom')",
            name="ck_diet_orders_type",
        ),
        db.CheckConstraint(
            "feeding_route IS NULL OR feeding_route IN "
            "('oral', 'enteral', 'parenteral')",
            name="ck_diet_orders_feeding_route",
        ),
        db.CheckConstraint(
            "texture IS NULL OR texture IN "
            "('regular', 'soft', 'minced', 'pureed', 'liquid')",
            name="ck_diet_orders_texture",
        ),
        db.CheckConstraint(
            "priority IN ('routine', 'urgent', 'stat')",
            name="ck_diet_orders_priority",
        ),
        db.CheckConstraint(
            "status IN ("
            "'pending', 'active', 'in_progress', "
            "'on_hold', 'completed', 'cancelled')",
            name="ck_diet_orders_status",
        ),
        db.CheckConstraint(
            "fluid_restriction_ml_per_day IS NULL "
            "OR fluid_restriction_ml_per_day > 0",
            name="ck_diet_orders_fluid_restriction_positive",
        ),
        db.CheckConstraint(
            "sodium_restriction_mg_per_day IS NULL "
            "OR sodium_restriction_mg_per_day > 0",
            name="ck_diet_orders_sodium_restriction_positive",
        ),
        db.CheckConstraint(
            "calorie_target_kcal_per_day IS NULL "
            "OR calorie_target_kcal_per_day > 0",
            name="ck_diet_orders_calorie_target_positive",
        ),
        db.CheckConstraint(
            "diet_type != 'npo' "
            "OR (feeding_route IS NULL AND texture IS NULL)",
            name="ck_diet_orders_npo_no_route_texture",
        ),
        db.CheckConstraint(
            "diet_type = 'npo' OR feeding_route IS NOT NULL",
            name="ck_diet_orders_non_npo_requires_route",
        ),
        db.CheckConstraint(
            "diet_type != 'custom' OR custom_description IS NOT NULL",
            name="ck_diet_orders_custom_description",
        ),
        db.CheckConstraint(
            "status != 'completed' "
            "OR (completed_at IS NOT NULL "
            "AND completed_by_staff_id IS NOT NULL "
            "AND completion_note IS NOT NULL)",
            name="ck_diet_orders_completed_evidence",
        ),
        db.CheckConstraint(
            "status != 'cancelled' "
            "OR (cancelled_at IS NOT NULL "
            "AND cancelled_by_staff_id IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_diet_orders_cancelled_evidence",
        ),
        db.Index(
            "ix_diet_orders_clinic_patient_ordered",
            "clinic_id",
            "patient_id",
            "ordered_at",
            "id",
        ),
        db.Index(
            "ix_diet_orders_admission_status_ordered",
            "admission_id",
            "status",
            "ordered_at",
            "id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

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

    diet_type = db.Column(
        db.String(32),
        nullable=False,
        index=True,
    )
    custom_description = db.Column(
        db.String(1000),
        nullable=True,
    )
    feeding_route = db.Column(
        db.String(24),
        nullable=True,
        index=True,
    )
    texture = db.Column(
        db.String(24),
        nullable=True,
    )

    fluid_restriction_ml_per_day = db.Column(
        db.Integer,
        nullable=True,
    )
    sodium_restriction_mg_per_day = db.Column(
        db.Integer,
        nullable=True,
    )
    calorie_target_kcal_per_day = db.Column(
        db.Integer,
        nullable=True,
    )

    instructions = db.Column(
        db.Text,
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

    start_at = db.Column(
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
    completed_by = db.relationship(
        "Staff",
        foreign_keys=[completed_by_staff_id],
    )
    cancelled_by = db.relationship(
        "Staff",
        foreign_keys=[cancelled_by_staff_id],
    )
