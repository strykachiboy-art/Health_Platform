"""CE1-S09 source-only operative report and clinical signatures.

Revision ID: c9a62e84f130
Revises: b8e53a7c1d92
Do not apply to application databases during the S09 focused slice.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9a62e84f130'
down_revision = 'b8e53a7c1d92'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('surgical_operative_reports',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('clinic_id',sa.Integer(),sa.ForeignKey('clinics.id'),nullable=False),
        sa.Column('patient_id',sa.Integer(),sa.ForeignKey('patients.id'),nullable=False),
        sa.Column('admission_id',sa.Integer(),sa.ForeignKey('admissions.id'),nullable=False),
        sa.Column('surgical_case_id',sa.Integer(),sa.ForeignKey('surgical_cases.id'),nullable=False),
        sa.Column('case_version',sa.Integer(),nullable=False),
        sa.Column('journal_id',sa.Integer(),sa.ForeignKey('surgical_evidence_journals.id'),nullable=False),
        sa.Column('journal_seal_sha256',sa.String(length=64),nullable=False),
        sa.Column('pacu_preparation_id',sa.Integer(),sa.ForeignKey('surgical_pacu_preparations.id'),nullable=False),
        sa.Column('pacu_review_sha256',sa.String(length=64),nullable=False),
        sa.Column('trace_set_sha256',sa.String(length=64),nullable=False),
        sa.Column('surgeon_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=False),
        sa.Column('anesthetist_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=False),
        sa.Column('drafted_by_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=False),
        sa.Column('drafted_at',sa.DateTime(),nullable=False),
        sa.Column('preoperative_diagnosis',sa.String(length=1200),nullable=False),
        sa.Column('postoperative_diagnosis',sa.String(length=1200),nullable=False),
        sa.Column('procedure_narrative',sa.String(length=6000),nullable=False),
        sa.Column('operative_findings',sa.String(length=4000),nullable=False),
        sa.Column('operative_course',sa.String(length=6000),nullable=False),
        sa.Column('complication_status',sa.String(length=24),nullable=False),
        sa.Column('complication_notes',sa.String(length=2000),nullable=False),
        sa.Column('estimated_blood_loss_ml',sa.Integer(),nullable=False),
        sa.Column('specimen_notes',sa.String(length=2000),nullable=False),
        sa.Column('implant_notes',sa.String(length=2000),nullable=False),
        sa.Column('postoperative_plan',sa.String(length=3000),nullable=False),
        sa.Column('status',sa.String(length=24),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('source_sha256',sa.String(length=64),nullable=False),
        sa.Column('content_sha256',sa.String(length=64),nullable=False),
        sa.CheckConstraint("complication_status IN ('none_reported','documented')",name='ck_surgical_operative_report_complications'),
        sa.CheckConstraint('length(source_sha256) = 64 AND length(content_sha256) = 64',name='ck_surgical_operative_report_digests'),
        sa.CheckConstraint('estimated_blood_loss_ml >= 0',name='ck_surgical_operative_report_ebl'),
        sa.CheckConstraint("status IN ('draft','surgeon_signed','dual_signed')",name='ck_surgical_operative_report_status'),
        sa.CheckConstraint('version >= 1 AND case_version >= 1',name='ck_surgical_operative_report_versions'),
        sa.UniqueConstraint('surgical_case_id',name='uq_surgical_operative_report_case'),
        sa.UniqueConstraint('journal_id',name='uq_surgical_operative_report_journal'),
    )
    op.create_index('ix_surgical_operative_report_clinic_case','surgical_operative_reports',['clinic_id','surgical_case_id'])
    op.create_index('ix_surgical_operative_reports_clinic_id','surgical_operative_reports',['clinic_id'])
    op.create_table('surgical_operative_signatures',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('clinic_id',sa.Integer(),sa.ForeignKey('clinics.id'),nullable=False),
        sa.Column('report_id',sa.Integer(),sa.ForeignKey('surgical_operative_reports.id'),nullable=False),
        sa.Column('signer_kind',sa.String(length=20),nullable=False),
        sa.Column('signed_by_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=False),
        sa.Column('signed_at',sa.DateTime(),nullable=False),
        sa.Column('document_sha256',sa.String(length=64),nullable=False),
        sa.Column('attestation_note',sa.String(length=800),nullable=False),
        sa.Column('evidence_sha256',sa.String(length=64),nullable=False),
        sa.CheckConstraint('length(document_sha256) = 64 AND length(evidence_sha256) = 64',name='ck_surgical_operative_signature_digests'),
        sa.CheckConstraint("signer_kind IN ('surgeon','anesthetist')",name='ck_surgical_operative_signature_kind'),
        sa.UniqueConstraint('report_id','signer_kind',name='uq_surgical_operative_signature_kind'),
    )
    op.create_index('ix_surgical_operative_signature_clinic_report','surgical_operative_signatures',['clinic_id','report_id'])
    op.create_index('ix_surgical_operative_signatures_clinic_id','surgical_operative_signatures',['clinic_id'])
    op.create_index('ix_surgical_operative_signatures_report_id','surgical_operative_signatures',['report_id'])


def downgrade():
    op.drop_index('ix_surgical_operative_signatures_report_id',table_name='surgical_operative_signatures')
    op.drop_index('ix_surgical_operative_signatures_clinic_id',table_name='surgical_operative_signatures')
    op.drop_index('ix_surgical_operative_signature_clinic_report',table_name='surgical_operative_signatures')
    op.drop_table('surgical_operative_signatures')
    op.drop_index('ix_surgical_operative_reports_clinic_id',table_name='surgical_operative_reports')
    op.drop_index('ix_surgical_operative_report_clinic_case',table_name='surgical_operative_reports')
    op.drop_table('surgical_operative_reports')
