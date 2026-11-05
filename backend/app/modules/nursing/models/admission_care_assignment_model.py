"""Admission-scoped primary clinical responsibility.

This is a persistence integration for existing Nursing/Admissions,
not a standalone Care Team subsystem.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AdmissionCareAssignment(db.Model):
    __tablename__ = "admission_care_assignments"

    __table_args__ = (
        db.CheckConstraint(
            "assignment_kind IN ('primary_doctor', 'primary_nurse')",
            name="ck_admission_care_assignment_kind",
        ),
        db.CheckConstraint(
            "ended_at IS NULL OR ended_at >= assigned_at",
            name="ck_admission_care_assignment_time",
        ),
        db.CheckConstraint(
            "ended_at IS NULL OR "
            "(ended_by_user_id IS NOT NULL AND end_reason IS NOT NULL)",
            name="ck_admission_care_assignment_end_evidence",
        ),
        db.Index(
            "uq_admission_care_assignment_active_kind",
            "clinic_id",
            "admission_id",
            "assignment_kind",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
            sqlite_where=text("ended_at IS NULL"),
        ),
        db.Index(
            "ix_admission_care_assignment_history",
            "clinic_id",
            "admission_id",
            "assigned_at",
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

    staff_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id"),
        nullable=False,
        index=True,
    )

    assignment_kind = db.Column(
        db.String(32),
        nullable=False,
    )

    assigned_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
    )

    assignment_reason = db.Column(
        db.String(500),
        nullable=False,
    )

    assigned_at = db.Column(
        db.DateTime,
        nullable=False,
        default=_utcnow,
    )

    ended_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    ended_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True,
    )

    end_reason = db.Column(
        db.String(500),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AdmissionCareAssignment {self.id} "
            f"admission={self.admission_id} "
            f"kind={self.assignment_kind}>"
        )