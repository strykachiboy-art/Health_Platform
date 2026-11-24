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
