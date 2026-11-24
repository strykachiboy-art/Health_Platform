"""RAD-C local worklist identity, intent evidence and interpretation grant.
No data migration or automatic credential grants.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c8261124a005'
down_revision = 'c8261124a004'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('radiology_dicom_identities',
        sa.Column('exam_id', sa.Integer(), sa.ForeignKey('radiology_exams.id'), primary_key=True),
        sa.Column('issuer_namespace', sa.String(64), nullable=False),
        sa.Column('dicom_accession', sa.String(16), nullable=False),
        sa.Column('patient_id', sa.Integer(), sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('patient_issuer', sa.String(64), nullable=False),
        sa.Column('study_instance_uid', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('issuer_namespace', 'dicom_accession', name='uq_radc_issuer_accession'),
        sa.UniqueConstraint('study_instance_uid', name='uq_radc_allocated_study_uid'),
        sa.CheckConstraint('length(dicom_accession) BETWEEN 1 AND 16', name='ck_radc_accession_length'),
        sa.CheckConstraint('length(study_instance_uid) BETWEEN 1 AND 64', name='ck_radc_study_uid_length'))
    op.create_table('radiology_worklist_publications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('exam_id', sa.Integer(), sa.ForeignKey('radiology_dicom_identities.exam_id'), nullable=False),
        sa.Column('exam_version', sa.Integer(), nullable=False),
        sa.Column('order_version', sa.Integer(), nullable=False),
        sa.Column('step_id', sa.Integer(), sa.ForeignKey('radiology_scheduled_steps.id'), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('payload_sha256', sa.String(64), nullable=False),
        sa.Column('published_by_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('exam_id', 'exam_version', name='uq_radc_worklist_version'),
        sa.UniqueConstraint('step_id', name='uq_radc_worklist_step'),
        sa.CheckConstraint('exam_version >= 2 AND order_version >= 1', name='ck_radc_worklist_version'),
        sa.CheckConstraint('length(payload_sha256) = 64', name='ck_radc_worklist_digest'))
    op.create_table('radiology_interpretation_grants',
        sa.Column('exam_id', sa.Integer(), sa.ForeignKey('radiology_exams.id'), primary_key=True),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('staff.id'), primary_key=True),
        sa.Column('clinic_id', sa.Integer(), sa.ForeignKey('clinics.id'), nullable=False),
        sa.Column('modality', sa.String(32), nullable=False),
        sa.Column('credential_reference', sa.String(128), nullable=False),
        sa.Column('approved_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint('expires_at > approved_at', name='ck_radc_grant_interval'),
        sa.CheckConstraint('revoked_at IS NULL OR revoked_at >= approved_at', name='ck_radc_grant_revocation'),
        sa.CheckConstraint('length(credential_reference) BETWEEN 1 AND 128', name='ck_radc_grant_reference'))


def downgrade():
    bind = op.get_bind()
    tables = ('radiology_interpretation_grants', 'radiology_worklist_publications', 'radiology_dicom_identities')
    for name in tables:
        if bind.execute(sa.text('SELECT COUNT(*) FROM ' + name)).scalar_one():
            raise RuntimeError('RAD-C evidence exists; destructive downgrade denied')
    for name in tables:
        op.drop_table(name)
