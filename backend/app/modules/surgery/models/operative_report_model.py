"""CE1-S09: surgeon narrative and clinical document signatures ONLY.

A surgeon signature attests authorship of the stored record, not that surgery
occurred, was completed, or that transfer, Sign-Out or discharge is authorized.
"""
from __future__ import annotations
from datetime import datetime, timezone
from app.extensions import db


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SurgicalOperativeReport(db.Model):
    __tablename__ = 'surgical_operative_reports'
    __table_args__ = (
        db.UniqueConstraint('surgical_case_id', name='uq_surgical_operative_report_case'),
        db.UniqueConstraint('journal_id', name='uq_surgical_operative_report_journal'),
        db.CheckConstraint("status IN ('draft','surgeon_signed','dual_signed')", name='ck_surgical_operative_report_status'),
        db.CheckConstraint('version >= 1 AND case_version >= 1', name='ck_surgical_operative_report_versions'),
        db.CheckConstraint('estimated_blood_loss_ml >= 0', name='ck_surgical_operative_report_ebl'),
        db.CheckConstraint("complication_status IN ('none_reported','documented')", name='ck_surgical_operative_report_complications'),
        db.CheckConstraint('length(source_sha256) = 64 AND length(content_sha256) = 64', name='ck_surgical_operative_report_digests'),
        db.Index('ix_surgical_operative_report_clinic_case', 'clinic_id','surgical_case_id'),
    )
    id = db.Column(db.Integer,primary_key=True)
    clinic_id = db.Column(db.Integer,db.ForeignKey('clinics.id'),nullable=False,index=True)
    patient_id = db.Column(db.Integer,db.ForeignKey('patients.id'),nullable=False)
    admission_id = db.Column(db.Integer,db.ForeignKey('admissions.id'),nullable=False)
    surgical_case_id = db.Column(db.Integer,db.ForeignKey('surgical_cases.id'),nullable=False)
    case_version = db.Column(db.Integer,nullable=False)
    journal_id = db.Column(db.Integer,db.ForeignKey('surgical_evidence_journals.id'),nullable=False)
    journal_seal_sha256 = db.Column(db.String(64),nullable=False)
    pacu_preparation_id = db.Column(db.Integer,db.ForeignKey('surgical_pacu_preparations.id'),nullable=False)
    pacu_review_sha256 = db.Column(db.String(64),nullable=False)
    trace_set_sha256 = db.Column(db.String(64),nullable=False)
    surgeon_staff_id = db.Column(db.Integer,db.ForeignKey('staff.id'),nullable=False)
    anesthetist_staff_id = db.Column(db.Integer,db.ForeignKey('staff.id'),nullable=False)
    drafted_by_staff_id = db.Column(db.Integer,db.ForeignKey('staff.id'),nullable=False)
    drafted_at = db.Column(db.DateTime,nullable=False,default=_now)
    preoperative_diagnosis = db.Column(db.String(1200),nullable=False)
    postoperative_diagnosis = db.Column(db.String(1200),nullable=False)
    procedure_narrative = db.Column(db.String(6000),nullable=False)
    operative_findings = db.Column(db.String(4000),nullable=False)
    operative_course = db.Column(db.String(6000),nullable=False)
    complication_status = db.Column(db.String(24),nullable=False)
    complication_notes = db.Column(db.String(2000),nullable=False)
    estimated_blood_loss_ml = db.Column(db.Integer,nullable=False)
    specimen_notes = db.Column(db.String(2000),nullable=False)
    implant_notes = db.Column(db.String(2000),nullable=False)
    postoperative_plan = db.Column(db.String(3000),nullable=False)
    status = db.Column(db.String(24),nullable=False,default='draft')
    version = db.Column(db.Integer,nullable=False,default=1)
    source_sha256 = db.Column(db.String(64),nullable=False)
    content_sha256 = db.Column(db.String(64),nullable=False)


class SurgicalOperativeSignature(db.Model):
    __tablename__ = 'surgical_operative_signatures'
    __table_args__ = (
        db.UniqueConstraint('report_id','signer_kind',name='uq_surgical_operative_signature_kind'),
        db.CheckConstraint("signer_kind IN ('surgeon','anesthetist')",name='ck_surgical_operative_signature_kind'),
        db.CheckConstraint('length(document_sha256) = 64 AND length(evidence_sha256) = 64',name='ck_surgical_operative_signature_digests'),
        db.Index('ix_surgical_operative_signature_clinic_report','clinic_id','report_id'),
    )
    id = db.Column(db.Integer,primary_key=True)
    clinic_id = db.Column(db.Integer,db.ForeignKey('clinics.id'),nullable=False,index=True)
    report_id = db.Column(db.Integer,db.ForeignKey('surgical_operative_reports.id'),nullable=False,index=True)
    signer_kind = db.Column(db.String(20),nullable=False)
    signed_by_staff_id = db.Column(db.Integer,db.ForeignKey('staff.id'),nullable=False)
    signed_at = db.Column(db.DateTime,nullable=False)
    document_sha256 = db.Column(db.String(64),nullable=False)
    attestation_note = db.Column(db.String(800),nullable=False)
    evidence_sha256 = db.Column(db.String(64),nullable=False)
    report = db.relationship('SurgicalOperativeReport')
