"""Staged radiology report and copy evidence; no clinical activation or backfill."""
from alembic import op
import sqlalchemy as sa
revision='c8261124a009'
down_revision='c8261124a008'
branch_labels=None
depends_on=None
OLD="source_record_type IN ('consultation_note','nursing_note','medication_administration','medication_reconciliation','discharge_summary','lab_report')"
NEW=OLD[:-1]+",'staged_radiology_report')"
CHECK='ck_clinical_record_amendments_type'


def upgrade():
    op.drop_constraint(CHECK,'clinical_record_amendments',type_='check')
    op.create_check_constraint(CHECK,'clinical_record_amendments',NEW)
    op.create_table('radiology_staged_reports',
        sa.Column('report_id',sa.Integer,sa.ForeignKey('radiology_reports.id'),primary_key=True),
        sa.Column('publication_id',sa.Integer,sa.ForeignKey('radiology_staged_ct_studies.publication_id'),nullable=False),
        sa.Column('draft_revision',sa.Integer,nullable=False),sa.Column('snapshot',sa.JSON,nullable=False),sa.Column('snapshot_sha256',sa.String(64),nullable=False),
        sa.Column('finalized_by_staff_id',sa.Integer,sa.ForeignKey('staff.id'),nullable=False),sa.Column('finalized_at',sa.DateTime,nullable=False),
        sa.UniqueConstraint('publication_id',name='uq_radc_report_publication'),sa.CheckConstraint('draft_revision >= 1',name='ck_radc_report_revision'),sa.CheckConstraint('length(snapshot_sha256) = 64',name='ck_radc_report_digest'))
    op.create_table('radiology_staged_report_releases',
        sa.Column('id',sa.Integer,primary_key=True),sa.Column('report_id',sa.Integer,sa.ForeignKey('radiology_staged_reports.report_id'),nullable=False),
        sa.Column('version',sa.Integer,nullable=False),sa.Column('recipient_user_id',sa.Integer,sa.ForeignKey('users.id'),nullable=False),sa.Column('content_sha256',sa.String(64),nullable=False),
        sa.Column('released_by_staff_id',sa.Integer,sa.ForeignKey('staff.id'),nullable=False),sa.Column('released_at',sa.DateTime,nullable=False),
        sa.Column('withdrawn_at',sa.DateTime),sa.Column('withdrawn_by_staff_id',sa.Integer,sa.ForeignKey('staff.id')),
        sa.UniqueConstraint('report_id','version',name='uq_radc_report_release_version'),sa.CheckConstraint('version >= 1',name='ck_radc_release_version'),sa.CheckConstraint('length(content_sha256) = 64',name='ck_radc_release_digest'),
        sa.CheckConstraint('(withdrawn_at IS NULL AND withdrawn_by_staff_id IS NULL) OR (withdrawn_at IS NOT NULL AND withdrawn_by_staff_id IS NOT NULL)',name='ck_radc_release_withdrawal'))
    op.create_index('uq_radc_report_active_release','radiology_staged_report_releases',['report_id'],unique=True,postgresql_where=sa.text('withdrawn_at IS NULL'),sqlite_where=sa.text('withdrawn_at IS NULL'))


def downgrade():
    c=op.get_bind()
    if any(c.execute(sa.text(q)).scalar_one() for q in ('SELECT COUNT(*) FROM radiology_staged_report_releases','SELECT COUNT(*) FROM radiology_staged_reports',"SELECT COUNT(*) FROM clinical_record_amendments WHERE source_record_type='staged_radiology_report'")):
        raise RuntimeError('Staged radiology evidence exists; destructive downgrade denied')
    op.drop_table('radiology_staged_report_releases');op.drop_table('radiology_staged_reports')
    op.drop_constraint(CHECK,'clinical_record_amendments',type_='check');op.create_check_constraint(CHECK,'clinical_record_amendments',OLD)
