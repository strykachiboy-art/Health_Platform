from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BloodProductOrder(db.Model):
    __tablename__ = "blood_product_orders"

    __table_args__ = (
        db.CheckConstraint(
            "product_type IN ("
            "'packed_red_blood_cells', 'whole_blood', "
            "'platelets', 'fresh_frozen_plasma', "
            "'cryoprecipitate')",
            name="ck_blood_product_orders_product_type",
        ),
        db.CheckConstraint(
            "units_requested > 0",
            name="ck_blood_product_orders_units_requested",
        ),
        db.CheckConstraint(
            "units_issued >= 0 AND units_issued <= units_requested",
            name="ck_blood_product_orders_units_issued",
        ),
        db.CheckConstraint(
            "priority IN ('routine', 'urgent', 'stat')",
            name="ck_blood_product_orders_priority",
        ),
        db.CheckConstraint(
            "status IN ("
            "'pending', 'active', 'in_progress', "
            "'on_hold', 'completed', 'cancelled')",
            name="ck_blood_product_orders_status",
        ),
        db.CheckConstraint(
            "compatibility_status IN ("
            "'pending', 'compatible', 'emergency_release')",
            name="ck_blood_product_orders_compatibility_status",
        ),
        db.CheckConstraint(
            "compatibility_status != 'compatible' "
            "OR (compatibility_verified_at IS NOT NULL "
            "AND compatibility_verified_by_staff_id IS NOT NULL "
            "AND crossmatch_reference IS NOT NULL)",
            name="ck_blood_product_orders_compatible_evidence",
        ),
        db.CheckConstraint(
            "compatibility_status != 'emergency_release' "
            "OR (compatibility_verified_at IS NOT NULL "
            "AND compatibility_verified_by_staff_id IS NOT NULL "
            "AND emergency_release_reason IS NOT NULL)",
            name="ck_blood_product_orders_emergency_evidence",
        ),
        db.CheckConstraint(
            "status != 'completed' "
            "OR (completed_at IS NOT NULL "
            "AND fulfilled_by_staff_id IS NOT NULL "
            "AND units_issued > 0 "
            "AND fulfillment_note IS NOT NULL "
            "AND compatibility_status IN "
            "('compatible', 'emergency_release'))",
            name="ck_blood_product_orders_completed_evidence",
        ),
        db.CheckConstraint(
            "status != 'cancelled' "
            "OR (cancelled_at IS NOT NULL "
            "AND cancelled_by_staff_id IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_blood_product_orders_cancelled_evidence",
        ),
        db.Index(
            "ix_blood_product_orders_clinic_patient_ordered",
            "clinic_id",
            "patient_id",
            "ordered_at",
            "id",
        ),
        db.Index(
            "ix_blood_product_orders_admission_status_ordered",
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
    compatibility_verified_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )
    fulfilled_by_staff_id = db.Column(
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

    product_type = db.Column(
        db.String(40),
        nullable=False,
        index=True,
    )
    units_requested = db.Column(
        db.Integer,
        nullable=False,
    )
    units_issued = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    indication = db.Column(
        db.Text,
        nullable=False,
    )
    special_requirements = db.Column(
        db.Text,
        nullable=True,
    )

    patient_blood_type_at_order = db.Column(
        db.String(16),
        nullable=False,
    )
    patient_blood_type_verified = db.Column(
        db.String(16),
        nullable=True,
    )
    product_blood_type = db.Column(
        db.String(16),
        nullable=True,
    )

    compatibility_status = db.Column(
        db.String(24),
        nullable=False,
        default="pending",
        index=True,
    )
    crossmatch_reference = db.Column(
        db.String(128),
        nullable=True,
        index=True,
    )
    compatibility_notes = db.Column(
        db.Text,
        nullable=True,
    )
    compatibility_verified_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    emergency_release_reason = db.Column(
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

    fulfillment_note = db.Column(
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
    compatibility_verified_by = db.relationship(
        "Staff",
        foreign_keys=[compatibility_verified_by_staff_id],
    )
    fulfilled_by = db.relationship(
        "Staff",
        foreign_keys=[fulfilled_by_staff_id],
    )
    cancelled_by = db.relationship(
        "Staff",
        foreign_keys=[cancelled_by_staff_id],
    )
