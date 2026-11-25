"""Durable blood bank evidence, separate from the physical component record."""
from datetime import datetime, timezone
from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BloodSupplier(db.Model):
    __tablename__ = 'blood_suppliers'
    __table_args__ = (db.UniqueConstraint('clinic_id', 'supplier_code', name='uq_blood_supplier_clinic_code'),)
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    supplier_code = db.Column(db.String(80), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    approval_reference = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=False)
    created_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)


class BloodReleaseEvidence(db.Model):
    __tablename__ = 'blood_release_evidence'
    __table_args__ = (db.UniqueConstraint('clinic_id', 'blood_unit_id', 'unit_version', name='uq_blood_release_unit_version'),)
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    blood_unit_id = db.Column(db.Integer, db.ForeignKey('blood_units.id'), nullable=False, index=True)
    unit_version = db.Column(db.Integer, nullable=False)
    policy_version = db.Column(db.String(80), nullable=False)
    screening_results = db.Column(db.JSON, nullable=False)
    blood_group_reference = db.Column(db.String(255), nullable=True)
    release_reference = db.Column(db.String(255), nullable=False)
    label_check_reference = db.Column(db.String(255), nullable=False)
    storage_check_reference = db.Column(db.String(255), nullable=False)
    actor_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)


class BloodCompatibilityEvidence(db.Model):
    __tablename__ = 'blood_compatibility_evidence'
    __table_args__ = (db.UniqueConstraint('clinic_id', 'order_id', 'blood_unit_id', 'unit_version', name='uq_blood_compat_order_unit_version'),)
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('blood_product_orders.id'), nullable=False, index=True)
    blood_unit_id = db.Column(db.Integer, db.ForeignKey('blood_units.id'), nullable=False, index=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    admission_id = db.Column(db.Integer, db.ForeignKey('admissions.id'), nullable=False)
    actor_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    order_version = db.Column(db.Integer, nullable=False)
    unit_version = db.Column(db.Integer, nullable=False)
    release_evidence_id = db.Column(db.Integer, db.ForeignKey('blood_release_evidence.id'), nullable=False)
    policy_version = db.Column(db.String(80), nullable=False)
    recipient_group = db.Column(db.String(16), nullable=False)
    component_group = db.Column(db.String(16), nullable=False)
    evidence_reference = db.Column(db.String(255), nullable=False)
    special_requirements_reference = db.Column(db.String(255), nullable=False)
    emergency = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)


class BloodReactionEvidence(db.Model):
    __tablename__ = 'blood_reaction_evidence'
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    transfusion_id = db.Column(db.Integer, db.ForeignKey('blood_transfusions.id'), nullable=False, index=True)
    actor_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    reaction_type = db.Column(db.String(32), nullable=False)
    severity = db.Column(db.String(24), nullable=False)
    notes = db.Column(db.Text, nullable=False)
    vitals = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)


class BloodRecallEvidence(db.Model):
    __tablename__ = 'blood_recall_evidence'
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    blood_unit_id = db.Column(db.Integer, db.ForeignKey('blood_units.id'), nullable=False, index=True)
    actor_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    affected_order_id = db.Column(db.Integer, db.ForeignKey('blood_product_orders.id'), nullable=True)
    affected_patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=True)
    reaction_evidence_id = db.Column(db.Integer, db.ForeignKey('blood_reaction_evidence.id'), nullable=True)
    previous_status = db.Column(db.String(24), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)


class BloodReturnEvidence(db.Model):
    __tablename__ = 'blood_return_evidence'
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    blood_unit_id = db.Column(db.Integer, db.ForeignKey('blood_units.id'), nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey('blood_product_orders.id'), nullable=False)
    actor_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    inspection_reference = db.Column(db.String(255), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
