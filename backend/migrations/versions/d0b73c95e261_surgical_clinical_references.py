"""CE1-S10 source-only cross-module surgical clinical evidence references.

Revision ID: d0b73c95e261
Revises: c9a62e84f130
Do NOT apply to application databases during S10 focused slice.
"""
from alembic import op
import sqlalchemy as sa

revision='d0b73c95e261'
down_revision='c9a62e84f130'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('surgical_clinical_references',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('clinic_id',sa.Integer(),sa.ForeignKey('clinics.id'),nullable=False),
        sa.Column('patient_id',sa.Integer(),sa.ForeignKey('patients.id'),nullable=False),
        sa.Column('admission_id',sa.Integer(),sa.ForeignKey('admissions.id'),nullable=False),
        sa.Column('surgical_case_id',sa.Integer(),sa.ForeignKey('surgical_cases.id'),nullable=False),
        sa.Column('report_id',sa.Integer(),sa.ForeignKey('surgical_operative_reports.id'),nullable=False),
        sa.Column('source_kind',sa.String(length=16),nullable=False),
        sa.Column('source_id',sa.Integer(),nullable=False),
        sa.Column('source_sha256',sa.String(length=64),nullable=False),
        sa.Column('report_sha256',sa.String(length=64),nullable=False),
        sa.Column('signatures_sha256',sa.String(length=64),nullable=False),
        sa.Column('record_note',sa.String(length=1200),nullable=False),
        sa.Column('recorded_by_staff_id',sa.Integer(),sa.ForeignKey('staff.id'),nullable=False),
        sa.Column('recorded_at',sa.DateTime(),nullable=False),
        sa.Column('evidence_sha256',sa.String(length=64),nullable=False),
        sa.CheckConstraint("source_kind IN ('emar','pharmacy','lab','billing','audit')",
            name='ck_surgical_clinical_reference_kind'),
        sa.CheckConstraint('source_id > 0', name='ck_surgical_clinical_reference_source_positive'),
        sa.CheckConstraint('length(source_sha256) = 64 AND length(report_sha256) = 64 '
            'AND length(signatures_sha256) = 64 AND length(evidence_sha256) = 64',
            name='ck_surgical_clinical_reference_digests'),
        sa.UniqueConstraint('report_id','source_kind','source_id',name='uq_surgical_clinical_reference_source'),
    )
    op.create_index('ix_surgical_clinical_references_clinic_id','surgical_clinical_references',['clinic_id'])
    op.create_index('ix_surgical_clinical_references_report_id','surgical_clinical_references',['report_id'])
    op.create_index('ix_surgical_clinical_reference_clinic_report','surgical_clinical_references',['clinic_id','report_id'])
    op.create_index('ix_surgical_clinical_reference_clinic_kind','surgical_clinical_references',['clinic_id','source_kind','source_id'])


def downgrade():
    op.drop_index('ix_surgical_clinical_reference_clinic_kind',table_name='surgical_clinical_references')
    op.drop_index('ix_surgical_clinical_reference_clinic_report',table_name='surgical_clinical_references')
    op.drop_index('ix_surgical_clinical_references_report_id',table_name='surgical_clinical_references')
    op.drop_index('ix_surgical_clinical_references_clinic_id',table_name='surgical_clinical_references')
    op.drop_table('surgical_clinical_references')
