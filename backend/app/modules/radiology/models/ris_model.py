"""RAD1 RIS metadata only. No DICOM binaries, verified archives or final signatures.

The ImagingOrder is the sole physician request/identity authority. RIS records do
not duplicate patient/admission/clinic IDs: those are always resolved by joining
back to their canonical order, preventing divergent copied identity columns.
"""
from __future__ import annotations

from datetime import datetime, timezone
from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RadiologyExam(db.Model):
    __tablename__ = 'radiology_exams'
    __table_args__ = (
        db.UniqueConstraint('imaging_order_id', name='uq_rad1_exam_order'),
        db.UniqueConstraint('issuer_namespace', 'accession_number',
                            name='uq_rad1_exam_issuer_accession'),
        db.CheckConstraint("status IN ('planned','scheduled','cancelled')",
                           name='ck_rad1_exam_status'),
        db.CheckConstraint('version >= 1', name='ck_rad1_exam_version'),
    )
    id = db.Column(db.Integer, primary_key=True)
    imaging_order_id = db.Column(db.Integer, db.ForeignKey('imaging_orders.id'),
                                 nullable=False, index=True)
    issuer_namespace = db.Column(db.String(64), nullable=False)
    accession_number = db.Column(db.String(48), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='planned')
    version = db.Column(db.Integer, nullable=False, default=1)
    created_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class RadiologyScheduledStep(db.Model):
    __tablename__ = 'radiology_scheduled_steps'
    __table_args__ = (
        db.UniqueConstraint('exam_id', 'sequence', name='uq_rad1_step_sequence'),
        db.CheckConstraint("status IN ('intent','superseded','cancelled')",
                           name='ck_rad1_step_status'),
        db.CheckConstraint('sequence >= 1', name='ck_rad1_step_sequence_positive'),
    )
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('radiology_exams.id'), nullable=False, index=True)
    sequence = db.Column(db.Integer, nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    modality = db.Column(db.String(32), nullable=False)
    station_code = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='intent')
    created_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    # Deliberately NO published/acknowledged flag: MWL does not yet exist.


class RadiologyReport(db.Model):
    __tablename__ = 'radiology_reports'
    __table_args__ = (
        db.UniqueConstraint('exam_id', name='uq_rad1_report_exam'),
        db.CheckConstraint("status = 'private_draft'", name='ck_rad1_report_not_final'),
        db.CheckConstraint('version >= 1', name='ck_rad1_report_version'),
    )
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('radiology_exams.id'), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default='private_draft')
    version = db.Column(db.Integer, nullable=False, default=1)
    author_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    # The draft is *never* a patient-chart report or a signed interpretation.


class RadiologyReportVersion(db.Model):
    __tablename__ = 'radiology_report_versions'
    __table_args__ = (
        db.UniqueConstraint('report_id', 'revision', name='uq_rad1_report_revision'),
        db.CheckConstraint('revision >= 1', name='ck_rad1_report_revision_positive'),
    )
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('radiology_reports.id'), nullable=False, index=True)
    revision = db.Column(db.Integer, nullable=False)
    content = db.Column(db.Text, nullable=False)
    content_sha256 = db.Column(db.String(64), nullable=False)
    edited_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    # Append-only in the RIS service. Signed-report tables do not exist yet.


class RadiologyStudyAssociation(db.Model):
    __tablename__ = 'radiology_study_associations'
    __table_args__ = (
        db.UniqueConstraint('study_instance_uid', name='uq_rad1_study_uid_global'),
        db.CheckConstraint("match_status IN ('unverified','quarantined')",
                           name='ck_rad1_study_unverified_only'),
    )
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('radiology_exams.id'), nullable=False, index=True)
    study_instance_uid = db.Column(db.String(96), nullable=False)
    match_status = db.Column(db.String(24), nullable=False, default='unverified')
    source = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    # No writer/HTTP ingestion in RAD1. No archive_status='archived' field.

class RadiologyDicomIdentity(db.Model):
    """Durable local identity; never an archive verification receipt."""
    __tablename__ = 'radiology_dicom_identities'
    __table_args__ = (
        db.UniqueConstraint('issuer_namespace', 'dicom_accession', name='uq_radc_issuer_accession'),
        db.UniqueConstraint('study_instance_uid', name='uq_radc_allocated_study_uid'),
        db.CheckConstraint('length(dicom_accession) BETWEEN 1 AND 16', name='ck_radc_accession_length'),
        db.CheckConstraint('length(study_instance_uid) BETWEEN 1 AND 64', name='ck_radc_study_uid_length'),
    )
    exam_id = db.Column(db.Integer, db.ForeignKey('radiology_exams.id'), primary_key=True)
    issuer_namespace = db.Column(db.String(64), nullable=False)
    dicom_accession = db.Column(db.String(16), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    patient_issuer = db.Column(db.String(64), nullable=False)
    study_instance_uid = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class RadiologyWorklistPublication(db.Model):
    """Append-only RIS publication intent, not external delivery evidence."""
    __tablename__ = 'radiology_worklist_publications'
    __table_args__ = (
        db.UniqueConstraint('exam_id', 'exam_version', name='uq_radc_worklist_version'),
        db.UniqueConstraint('step_id', name='uq_radc_worklist_step'),
        db.CheckConstraint('exam_version >= 2 AND order_version >= 1', name='ck_radc_worklist_version'),
        db.CheckConstraint('length(payload_sha256) = 64', name='ck_radc_worklist_digest'),
    )
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey('radiology_dicom_identities.exam_id'), nullable=False)
    exam_version = db.Column(db.Integer, nullable=False)
    order_version = db.Column(db.Integer, nullable=False)
    step_id = db.Column(db.Integer, db.ForeignKey('radiology_scheduled_steps.id'), nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    payload_sha256 = db.Column(db.String(64), nullable=False)
    published_by_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class RadiologyInterpretationGrant(db.Model):
    """Exam-specific credential approval; no self-service or HTTP grant writer."""
    __tablename__ = 'radiology_interpretation_grants'
    __table_args__ = (
        db.CheckConstraint('expires_at > approved_at', name='ck_radc_grant_interval'),
        db.CheckConstraint('revoked_at IS NULL OR revoked_at >= approved_at', name='ck_radc_grant_revocation'),
        db.CheckConstraint('length(credential_reference) BETWEEN 1 AND 128', name='ck_radc_grant_reference'),
    )
    exam_id = db.Column(db.Integer, db.ForeignKey('radiology_exams.id'), primary_key=True)
    staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'), primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id'), nullable=False)
    modality = db.Column(db.String(32), nullable=False)
    credential_reference = db.Column(db.String(128), nullable=False)
    approved_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)
