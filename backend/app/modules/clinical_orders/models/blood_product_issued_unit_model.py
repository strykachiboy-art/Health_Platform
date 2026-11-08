"""Immutable emergency blood-bank *issue* provenance; not transfusion administration.

Each label can be issued at most once within one clinic. All issuance writes
happen in the parent order's transaction and are guarded by a DB uniqueness key.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BloodProductIssuedUnit(db.Model):
    __tablename__ = "blood_product_issued_units"
    __table_args__ = (
        db.UniqueConstraint(
            "clinic_id", "unit_identifier",
            name="uq_blood_product_issued_unit_clinic_identifier",
        ),
        db.CheckConstraint(
            "length(trim(unit_identifier)) > 0 AND "
            "unit_identifier = upper(unit_identifier)",
            name="ck_blood_product_issued_unit_normalized_identifier",
        ),
        db.Index(
            "ix_blood_product_issued_units_order_id", "order_id", "id",
        ),
        db.Index(
            "ix_blood_product_issued_units_clinic_patient", "clinic_id", "patient_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("blood_product_orders.id"),
        nullable=False,
    )
    unit_identifier = db.Column(db.String(128), nullable=False)
    product_blood_type = db.Column(db.String(16), nullable=False)
    issued_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    issued_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    order = db.relationship("BloodProductOrder", back_populates="issued_units")
