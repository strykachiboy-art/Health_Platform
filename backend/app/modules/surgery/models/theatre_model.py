"""CE1-S02: operating room and non-executable surgical booking authority.

A booking reserves a theatre time slot only. It never authorizes an operation,
performs consent verification, or advances a SurgicalCase execution state.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OperatingTheatre(db.Model):
    __tablename__ = "operating_theatres"
    __table_args__ = (
        db.UniqueConstraint("clinic_id", "code", name="uq_operating_theatres_clinic_code"),
        db.CheckConstraint("length(code) > 0", name="ck_operating_theatres_code_nonempty"),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    code = db.Column(db.String(32), nullable=False)
    display_name = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    clinic = db.relationship("Clinic")


class TheatreBooking(db.Model):
    __tablename__ = "theatre_bookings"
    __table_args__ = (
        db.CheckConstraint("starts_at < ends_at", name="ck_theatre_bookings_interval"),
        db.CheckConstraint(
            "status IN ('scheduled', 'cancelled')", name="ck_theatre_bookings_status"
        ),
        db.CheckConstraint("version >= 1", name="ck_theatre_bookings_version"),
        db.CheckConstraint(
            "status != 'cancelled' OR (cancelled_at IS NOT NULL AND "
            "cancelled_by_staff_id IS NOT NULL AND cancellation_reason IS NOT NULL)",
            name="ck_theatre_bookings_cancelled_evidence",
        ),
        db.Index(
            "uq_theatre_bookings_active_case", "surgical_case_id", unique=True,
            postgresql_where=text("status = 'scheduled'"),
            sqlite_where=text("status = 'scheduled'"),
        ),
        db.Index(
            "ix_theatre_bookings_room_schedule",
            "theatre_id", "status", "starts_at", "ends_at",
        ),
        db.Index("ix_theatre_bookings_clinic_case", "clinic_id", "surgical_case_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False, index=True)
    surgical_case_id = db.Column(
        db.Integer, db.ForeignKey("surgical_cases.id"), nullable=False, index=True
    )
    theatre_id = db.Column(
        db.Integer, db.ForeignKey("operating_theatres.id"), nullable=False, index=True
    )
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="scheduled")
    scheduled_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False
    )
    cancelled_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=True
    )
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancellation_reason = db.Column(db.String(500), nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    clinic = db.relationship("Clinic")
    surgical_case = db.relationship("SurgicalCase")
    theatre = db.relationship("OperatingTheatre")
    scheduled_by = db.relationship("Staff", foreign_keys=[scheduled_by_staff_id])
    cancelled_by = db.relationship("Staff", foreign_keys=[cancelled_by_staff_id])
