"""P23D.E S03.B: per-issued-unit post-release LAB evidence provenance.

This is an evidence-link record, not a compatibility clearance or proof of
transfusion administration. A record is created pending for every issued unit.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BloodProductLabFollowup(db.Model):
    __tablename__ = "blood_product_lab_followups"
    __table_args__ = (
        db.UniqueConstraint(
            "issued_unit_id", name="uq_blood_product_lab_followups_issued_unit"
        ),
        db.UniqueConstraint(
            "lab_order_item_id", name="uq_blood_product_lab_followups_lab_item"
        ),
        db.CheckConstraint(
            "status IN ('pending', 'evidence_recorded', 'discrepancy_escalated')",
            name="ck_blood_product_lab_followups_status",
        ),
        db.CheckConstraint(
            "status != 'pending' OR (lab_order_item_id IS NULL AND "
            "recorded_by_staff_id IS NULL AND recorded_at IS NULL AND "
            "evidence_note IS NULL AND escalation_note IS NULL)",
            name="ck_blood_product_lab_followups_pending_evidence",
        ),
        db.CheckConstraint(
            "status = 'pending' OR (lab_order_item_id IS NOT NULL AND "
            "recorded_by_staff_id IS NOT NULL AND recorded_at IS NOT NULL AND "
            "evidence_note IS NOT NULL)",
            name="ck_blood_product_lab_followups_recorded_evidence",
        ),
        db.CheckConstraint(
            "status != 'discrepancy_escalated' OR escalation_note IS NOT NULL",
            name="ck_blood_product_lab_followups_escalation_evidence",
        ),
        db.Index(
            "ix_blood_product_lab_followups_order_status", "order_id", "status"
        ),
        db.Index(
            "ix_blood_product_lab_followups_clinic_patient", "clinic_id", "patient_id"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey("clinics.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    admission_id = db.Column(db.Integer, db.ForeignKey("admissions.id"), nullable=False)
    order_id = db.Column(
        db.Integer, db.ForeignKey("blood_product_orders.id"), nullable=False
    )
    issued_unit_id = db.Column(
        db.Integer, db.ForeignKey("blood_product_issued_units.id"), nullable=False
    )
    status = db.Column(
        db.String(32), nullable=False, default="pending", server_default="pending"
    )
    lab_order_item_id = db.Column(
        db.Integer, db.ForeignKey("lab_order_items.id"), nullable=True
    )
    recorded_by_staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=True
    )
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    evidence_note = db.Column(db.Text, nullable=True)
    escalation_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow
    )

    order = db.relationship("BloodProductOrder", back_populates="lab_followups")
    issued_unit = db.relationship("BloodProductIssuedUnit")
    lab_order_item = db.relationship("LabOrderItem")
