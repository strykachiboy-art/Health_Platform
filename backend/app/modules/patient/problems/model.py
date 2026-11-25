from datetime import datetime, timezone
from app.extensions import db


class PatientProblem(db.Model):
    """Current structured record plus immutable original snapshot.

    Every subsequent version is preserved by ClinicalRecordAmendment.
    """
    __tablename__ = 'patient_problems'
    __table_args__ = (
        db.ForeignKeyConstraint(['clinic_id', 'organization_id'], ['clinics.id', 'clinics.organization_id'], name='fk_patient_problems_clinic_org'),
        db.ForeignKeyConstraint(['patient_id', 'clinic_id'], ['patients.id', 'patients.clinic_id'], name='fk_patient_problems_patient_clinic'),
        db.ForeignKeyConstraint(['recorded_by_staff_id', 'clinic_id'], ['staff.id', 'staff.clinic_id'], name='fk_patient_problems_actor_clinic'),
        db.ForeignKeyConstraint(['reviewed_by_staff_id', 'clinic_id'], ['staff.id', 'staff.clinic_id'], name='fk_patient_problems_review_clinic'),
        db.ForeignKeyConstraint(['confirmed_by_staff_id', 'clinic_id'], ['staff.id', 'staff.clinic_id'], name='fk_patient_problems_confirm_clinic'),
        db.CheckConstraint('(confirmed_by_staff_id IS NULL) = (confirmed_at IS NULL)', name='ck_patient_problems_confirmation_pair'),
        db.CheckConstraint('version >= 1', name='ck_patient_problems_version'),
        db.CheckConstraint("clinical_status IN ('active','inactive','resolved','remission','recurrence','relapse')", name='ck_patient_problems_clinical_status'),
        db.CheckConstraint("verification_status IN ('unconfirmed','provisional','differential','confirmed','refuted','entered_in_error')", name='ck_patient_problems_verification_status'),
        db.Index('ix_patient_problems_scope_status', 'organization_id', 'clinic_id', 'patient_id', 'clinical_status', 'id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.UUID(as_uuid=True), nullable=False)
    clinic_id = db.Column(db.Integer, nullable=False)
    patient_id = db.Column(db.Integer, nullable=False)
    clinical_status = db.Column(db.String(24), nullable=False)
    verification_status = db.Column(db.String(24), nullable=False)
    content = db.Column(db.JSON, nullable=False)
    original_snapshot = db.Column(db.JSON, nullable=False)
    consultation_ids = db.Column(db.JSON, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    recorded_by_staff_id = db.Column(db.Integer, nullable=False)
    reviewed_by_staff_id = db.Column(db.Integer, nullable=False)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    confirmed_by_staff_id = db.Column(db.Integer, nullable=True)
    confirmed_at = db.Column(db.DateTime(timezone=True), nullable=True)
