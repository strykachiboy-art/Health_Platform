"""Blood-bank specimen, independent verification and safety investigation evidence.

No patient record is invented for a blood donor. All rows are documentary, and
verified results never by themselves release a physical component.
"""
from datetime import datetime, timezone
from app.extensions import db


def now():
    return datetime.now(timezone.utc)


class BloodLabArtifact(db.Model):
    __tablename__ = 'blood_lab_artifacts'
    __table_args__ = (
        db.UniqueConstraint('clinic_id', 'reference', name='uq_bbla_clinic_reference'),
        db.CheckConstraint("kind IN ('screening','blood_group','compatibility','special_requirements','confirmatory','emergency_assessment')", name='ck_bbla_kind'),
        db.CheckConstraint("(donation_id IS NOT NULL AND blood_unit_id IS NULL AND order_id IS NULL) OR (donation_id IS NULL AND blood_unit_id IS NOT NULL)", name='ck_bbla_subject'),
        db.CheckConstraint("(kind IN ('compatibility','special_requirements','emergency_assessment') AND order_id IS NOT NULL) OR (kind NOT IN ('compatibility','special_requirements','emergency_assessment') AND order_id IS NULL)", name='ck_bbla_order_kind'),
        db.CheckConstraint("(kind = 'compatibility' AND lab_order_item_id IS NOT NULL) OR (kind != 'compatibility' AND lab_order_item_id IS NULL)", name='ck_bbla_patient_lab_link'),
        db.CheckConstraint("(order_id IS NULL AND patient_id IS NULL AND admission_id IS NULL) OR (order_id IS NOT NULL AND patient_id IS NOT NULL AND admission_id IS NOT NULL)", name='ck_bbla_order_context'),
    )
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    reference = db.Column(db.String(255), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    marker = db.Column(db.String(80), nullable=False)
    specimen_identifier = db.Column(db.String(128), nullable=False)
    donation_id = db.Column(db.Integer, db.ForeignKey('blood_donations.id'), nullable=True)
    blood_unit_id = db.Column(db.Integer, db.ForeignKey('blood_units.id'), nullable=True)
    order_id = db.Column(db.Integer, db.ForeignKey('blood_product_orders.id'), nullable=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=True)
    admission_id = db.Column(db.Integer, db.ForeignKey('admissions.id'), nullable=True)
    result = db.Column(db.String(80), nullable=False)
    test_code = db.Column(db.String(80), nullable=False)
    source_reference = db.Column(db.String(255), nullable=False)
    lab_order_item_id = db.Column(db.Integer, db.ForeignKey('lab_order_items.id'), nullable=True, unique=True)
    lab_result_snapshot = db.Column(db.JSON, nullable=True)
    policy_version = db.Column(db.String(80), nullable=False)
    observed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    recorded_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)


class BloodLabVerification(db.Model):
    __tablename__ = 'blood_lab_verifications'
    __table_args__ = (db.UniqueConstraint('artifact_id', name='uq_bblv_artifact'),)
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    artifact_id = db.Column(db.Integer, db.ForeignKey('blood_lab_artifacts.id'), nullable=False)
    content_sha256 = db.Column(db.String(64), nullable=False)
    verification_reference = db.Column(db.String(255), nullable=False)
    verified_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class BloodInvestigationEvent(db.Model):
    __tablename__ = 'blood_investigation_events'
    __table_args__ = (
        db.CheckConstraint('(donation_id IS NOT NULL AND recall_id IS NULL) OR (donation_id IS NULL AND recall_id IS NOT NULL)', name='ck_bbie_subject'),
        db.CheckConstraint("outcome IN ('opened','evidence_recorded','escalated','reviewed')", name='ck_bbie_outcome'),
        db.UniqueConstraint('clinic_id', 'operation_identity', name='uq_bbie_operation'),
    )
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    donation_id = db.Column(db.Integer, db.ForeignKey('blood_donations.id'), nullable=True)
    recall_id = db.Column(db.Integer, db.ForeignKey('blood_recall_evidence.id'), nullable=True)
    artifact_id = db.Column(db.Integer, db.ForeignKey('blood_lab_artifacts.id'), nullable=True)
    operation_identity = db.Column(db.String(128), nullable=False)
    outcome = db.Column(db.String(24), nullable=False)
    notes = db.Column(db.Text, nullable=False)
    actor_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)


class BloodSafetyInbox(db.Model):
    __tablename__ = 'blood_safety_inbox'
    __table_args__ = (db.UniqueConstraint('clinic_id', 'event_key', 'recipient_staff_id', name='uq_bbsi_event_recipient'),)
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    event_key = db.Column(db.String(180), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    recipient_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    recall_id = db.Column(db.Integer, db.ForeignKey('blood_recall_evidence.id'), nullable=True)
    order_id = db.Column(db.Integer, db.ForeignKey('blood_product_orders.id'), nullable=True)
    message = db.Column(db.String(500), nullable=False)
    notification_id = db.Column(db.Integer, db.ForeignKey('notifications.id'), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    acknowledged_at = db.Column(db.DateTime(timezone=True), nullable=True)


class BloodPatientRelease(db.Model):
    __tablename__ = 'blood_patient_releases'
    __table_args__ = (db.UniqueConstraint('transfusion_id','transfusion_version', name='uq_bbpr_transfusion_version'),)
    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False, index=True)
    transfusion_id = db.Column(db.Integer, db.ForeignKey('blood_transfusions.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    recipient_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    actor_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    reference = db.Column(db.String(255), nullable=False)
    transfusion_version = db.Column(db.Integer, nullable=False)
    snapshot = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
