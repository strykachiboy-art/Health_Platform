"""Append correction rejection evidence and critical release attestation.

Revision ID: c8261124a004
Revises: c8261124a003
No backfill or production execution authorized.
"""
from alembic import op
import sqlalchemy as sa
revision = 'c8261124a004'
down_revision = 'c8261124a003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('lab_orders', sa.Column('patient_release_critical_review', sa.JSON(none_as_null=True), nullable=True))
    op.create_check_constraint('ck_lab_release_critical_review', 'lab_orders', 'patient_release_critical_review IS NULL OR patient_released_at IS NOT NULL')
    op.create_table('lab_report_correction_rejections',
        sa.Column('amendment_id', sa.Integer(), sa.ForeignKey('clinical_record_amendments.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('lab_orders.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('rejected_by_id', sa.Integer(), sa.ForeignKey('staff.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reason_code', sa.String(32), nullable=False),
        sa.UniqueConstraint('order_id','version',name='uq_lab_correction_rejection_version'),
        sa.CheckConstraint('version >= 2',name='ck_lab_correction_rejection_version'),
        sa.CheckConstraint('length(content_sha256) = 64',name='ck_lab_correction_rejection_hash'),
        sa.CheckConstraint("reason_code IN ('incorrect_correction','insufficient_evidence')",name='ck_lab_correction_rejection_reason'))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT EXISTS (SELECT 1 FROM lab_report_correction_rejections) OR EXISTS (SELECT 1 FROM lab_orders WHERE patient_release_critical_review IS NOT NULL)')).scalar_one():
        raise RuntimeError('Cannot destroy clinical release decision evidence')
    op.drop_table('lab_report_correction_rejections')
    op.drop_constraint('ck_lab_release_critical_review','lab_orders',type_='check')
    op.drop_column('lab_orders','patient_release_critical_review')
