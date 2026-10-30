from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MedicationAdministration(db.Model):
    """Electronic medication administration record."""

    __tablename__ = "medication_administrations"

    __table_args__ = (
        db.CheckConstraint(
            "dose_value IS NULL OR dose_value > 0",
            name="ck_med_admin_dose_positive",
        ),
        db.CheckConstraint(
            (
                "source_order_version IS NULL "
                "OR source_order_version > 0"
            ),
            name=(
                "ck_med_admin_source_order_version_positive"
            ),
        ),
        db.CheckConstraint(
            (
                "status NOT IN ('scheduled', 'due') "
                "OR scheduled_at IS NOT NULL"
            ),
            name=(
                "ck_med_admin_scheduled_requires_time"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'administered' "
                "OR (administered_at IS NOT NULL "
                "AND administered_by_staff_id IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_administered_requires_actor_time"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'held' "
                "OR (held_at IS NOT NULL "
                "AND held_by_staff_id IS NOT NULL "
                "AND hold_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_held_requires_evidence"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'refused' "
                "OR (refused_at IS NOT NULL "
                "AND refused_recorded_by_staff_id IS NOT NULL "
                "AND refusal_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_refused_requires_evidence"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'missed' "
                "OR (missed_at IS NOT NULL "
                "AND missed_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_missed_requires_evidence"
            ),
        ),
        db.CheckConstraint(
            (
                "status != 'cancelled' "
                "OR (cancelled_at IS NOT NULL "
                "AND cancel_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_cancelled_requires_evidence"
            ),
        ),
        db.UniqueConstraint(
            "admission_id",
            "prescription_item_id",
            "scheduled_at",
            name=(
                "uq_med_admin_"
                "admission_item_scheduled"
            ),
        ),
        db.UniqueConstraint(
            "clinic_id",
            "idempotency_key",
            name=(
                "uq_med_admin_clinic_idempotency_key"
            ),
        ),
        db.Index(
            "ix_med_admin_clinic_patient_scheduled",
            "clinic_id",
            "patient_id",
            "scheduled_at",
            "id",
        ),
        db.Index(
            "ix_med_admin_admission_status_scheduled",
            "admission_id",
            "status",
            "scheduled_at",
            "id",
        ),
        db.Index(
            "ix_med_admin_prescription_item_scheduled",
            "prescription_item_id",
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

    prescription_id = db.Column(
        db.Integer,
        db.ForeignKey("prescriptions.id"),
        nullable=False,
        index=True,
    )

    prescription_item_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "prescription_items.id"
        ),
        nullable=False,
        index=True,
    )

    scheduled_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    dose_value = db.Column(
        db.Numeric(
            12,
            4,
        ),
        nullable=True,
    )

    dose_unit = db.Column(
        db.String(50),
        nullable=True,
    )

    route_code = db.Column(
        db.String(100),
        nullable=True,
    )

    status = db.Column(
        db.Enum(
            MedicationAdministrationStatus,
            name=(
                "medication_administration_status"
            ),
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [
                member.value
                for member in enum_cls
            ],
        ),
        default=(
            MedicationAdministrationStatus
            .SCHEDULED
        ),
        nullable=False,
        index=True,
    )

    administered_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    administered_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
        index=True,
    )

    held_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    held_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
    )

    hold_reason = db.Column(
        db.String(500),
        nullable=True,
    )

    refused_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    refused_recorded_by_staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=True,
    )

    refusal_reason = db.Column(
        db.String(500),
        nullable=True,
    )

    missed_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    missed_reason = db.Column(
        db.String(500),
        nullable=True,
    )

    cancelled_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    cancel_reason = db.Column(
        db.String(500),
        nullable=True,
    )

    source_order_version = db.Column(
        db.Integer,
        nullable=True,
    )

    idempotency_key = db.Column(
        db.String(255),
        nullable=True,
    )

    notes = db.Column(
        db.Text,
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        index=True,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    clinic = db.relationship("Clinic")
    patient = db.relationship("Patient")
    admission = db.relationship("Admission")
    prescription = db.relationship("Prescription")
    prescription_item = db.relationship(
        "PrescriptionItem"
    )

    administered_by = db.relationship(
        "Staff",
        foreign_keys=[
            administered_by_staff_id
        ],
    )

    held_by = db.relationship(
        "Staff",
        foreign_keys=[
            held_by_staff_id
        ],
    )

    refusal_recorded_by = db.relationship(
        "Staff",
        foreign_keys=[
            refused_recorded_by_staff_id
        ],
    )

    def __repr__(self) -> str:
        status = (
            self.status.value
            if self.status is not None
            else "unknown"
        )

        return (
            "<MedicationAdministration "
            f"{self.id} "
            f"Admission {self.admission_id} "
            f"Item {self.prescription_item_id} "
            f"({status})>"
        )
