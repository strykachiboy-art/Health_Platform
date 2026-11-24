"""RAD1 RIS metadata foundation (not final diagnostic signing or PACS).

Revision ID: f7a9e20bd613
Revises: resolved from verified pinned-source Alembic graph at installation
"""
from alembic import op
import sqlalchemy as sa

revision = 'f7a9e20bd613'
down_revision = 'd0b73c95e261'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('radiology_exams',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('imaging_order_id', sa.Integer(), sa.ForeignKey('imaging_orders.id'), nullable=False),
        sa.Column('issuer_namespace', sa.String(64), nullable=False),
        sa.Column('accession_number', sa.String(48), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='planned'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_by_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('imaging_order_id', name='uq_rad1_exam_order'),
        sa.UniqueConstraint('issuer_namespace', 'accession_number', name='uq_rad1_exam_issuer_accession'),
        sa.CheckConstraint("status IN ('planned','scheduled','cancelled')", name='ck_rad1_exam_status'),
        sa.CheckConstraint('version >= 1', name='ck_rad1_exam_version'),
    )
    op.create_index('ix_radiology_exams_imaging_order_id', 'radiology_exams', ['imaging_order_id'])
    op.create_table('radiology_scheduled_steps',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('exam_id', sa.Integer(), sa.ForeignKey('radiology_exams.id'), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(), nullable=False),
        sa.Column('modality', sa.String(32), nullable=False),
        sa.Column('station_code', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='intent'),
        sa.Column('created_by_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('exam_id', 'sequence', name='uq_rad1_step_sequence'),
        sa.CheckConstraint("status IN ('intent','superseded','cancelled')", name='ck_rad1_step_status'),
        sa.CheckConstraint('sequence >= 1', name='ck_rad1_step_sequence_positive'),
    )
    op.create_index('ix_radiology_scheduled_steps_exam_id', 'radiology_scheduled_steps', ['exam_id'])
    op.create_table('radiology_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('exam_id', sa.Integer(), sa.ForeignKey('radiology_exams.id'), nullable=False),
        sa.Column('status', sa.String(24), nullable=False, server_default='private_draft'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('author_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('exam_id', name='uq_rad1_report_exam'),
        sa.CheckConstraint("status = 'private_draft'", name='ck_rad1_report_not_final'),
        sa.CheckConstraint('version >= 1', name='ck_rad1_report_version'),
    )
    op.create_index('ix_radiology_reports_exam_id', 'radiology_reports', ['exam_id'])
    op.create_table('radiology_report_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('report_id', sa.Integer(), sa.ForeignKey('radiology_reports.id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('edited_by_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('report_id', 'revision', name='uq_rad1_report_revision'),
        sa.CheckConstraint('revision >= 1', name='ck_rad1_report_revision_positive'),
    )
    op.create_index('ix_radiology_report_versions_report_id', 'radiology_report_versions', ['report_id'])
    op.create_table('radiology_study_associations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('exam_id', sa.Integer(), sa.ForeignKey('radiology_exams.id'), nullable=False),
        sa.Column('study_instance_uid', sa.String(96), nullable=False),
        sa.Column('match_status', sa.String(24), nullable=False, server_default='unverified'),
        sa.Column('source', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('study_instance_uid', name='uq_rad1_study_uid_global'),
        sa.CheckConstraint("match_status IN ('unverified','quarantined')", name='ck_rad1_study_unverified_only'),
    )
    op.create_index('ix_radiology_study_associations_exam_id', 'radiology_study_associations', ['exam_id'])


def downgrade():
    op.drop_index('ix_radiology_study_associations_exam_id', table_name='radiology_study_associations')
    op.drop_table('radiology_study_associations')
    op.drop_index('ix_radiology_report_versions_report_id', table_name='radiology_report_versions')
    op.drop_table('radiology_report_versions')
    op.drop_index('ix_radiology_reports_exam_id', table_name='radiology_reports')
    op.drop_table('radiology_reports')
    op.drop_index('ix_radiology_scheduled_steps_exam_id', table_name='radiology_scheduled_steps')
    op.drop_table('radiology_scheduled_steps')
    op.drop_index('ix_radiology_exams_imaging_order_id', table_name='radiology_exams')
    op.drop_table('radiology_exams')
