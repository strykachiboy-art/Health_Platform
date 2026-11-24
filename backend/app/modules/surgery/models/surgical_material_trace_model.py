"""CE1-S07 source-bound surgical trace evidence; never performs clinical stock actions."""
from __future__ import annotations

from datetime import datetime, timezone
from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SurgicalMaterialTrace(db.Model):
    __tablename__ = 'surgical_material_traces'
    __table_args__ = (
        db.CheckConstraint(
            "trace_kind IN ('specimen','implant','consumable','blood_product')",
            name='ck_surgical_material_trace_kind',
        ),
        db.CheckConstraint('quantity > 0', name='ck_surgical_material_quantity'),
        db.CheckConstraint('length(source_sha256) = 64', name='ck_surgical_material_source_digest'),
        db.CheckConstraint('length(evidence_sha256) = 64', name='ck_surgical_material_evidence_digest'),
        db.CheckConstraint(
            "(trace_kind = 'specimen' AND lab_order_id IS NOT NULL "
            "AND stock_movement_id IS NULL AND blood_product_order_id IS NULL AND quantity = 1) "
            "OR (trace_kind IN ('implant','consumable') AND lab_order_id IS NULL "
            "AND stock_movement_id IS NOT NULL AND blood_product_order_id IS NULL) "
            "OR (trace_kind = 'blood_product' AND lab_order_id IS NULL "
            "AND stock_movement_id IS NULL AND blood_product_order_id IS NOT NULL)",
            name='ck_surgical_material_authority_type',
        ),
        db.UniqueConstraint('lab_order_id', name='uq_surgical_material_lab_order'),
        db.UniqueConstraint('stock_movement_id', name='uq_surgical_material_stock_movement'),
        db.UniqueConstraint('blood_product_order_id', name='uq_surgical_material_blood_order'),
        db.Index('ix_surgical_material_clinic_journal', 'clinic_id', 'journal_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    journal_id = db.Column(db.Integer, db.ForeignKey('surgical_evidence_journals.id'), nullable=False, index=True)
    trace_kind = db.Column(db.String(24), nullable=False)
    lab_order_id = db.Column(db.Integer, db.ForeignKey('lab_orders.id'), nullable=True)
    stock_movement_id = db.Column(db.Integer, db.ForeignKey('stock_movements.id'), nullable=True)
    blood_product_order_id = db.Column(db.Integer, db.ForeignKey('blood_product_orders.id'), nullable=True)
    quantity = db.Column(db.Integer, nullable=False)
    identity_label = db.Column(db.String(150), nullable=False)
    record_note = db.Column(db.String(1200), nullable=False)
    recorded_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    source_sha256 = db.Column(db.String(64), nullable=False)
    evidence_sha256 = db.Column(db.String(64), nullable=False)

    journal = db.relationship('SurgicalEvidenceJournal')
    lab_order = db.relationship('LabOrder')
    stock_movement = db.relationship('StockMovement')
    blood_product_order = db.relationship('BloodProductOrder')
    recorded_by = db.relationship('Staff')
