"""Append-only corrected lab report publication evidence; no historical backfill."""
from alembic import op
import sqlalchemy as sa
revision = 'c8261124a003'
down_revision = 'c8261124a002'
branch_labels = None
depends_on = None
OLD = "source_record_type IN ('consultation_note', 'nursing_note', 'medication_administration', 'medication_reconciliation', 'discharge_summary')"
NEW = OLD[:-1] + ", 'lab_report')"
CHECK = 'ck_clinical_record_amendments_type'
TABLE = 'lab_report_publications'


def upgrade():
    op.add_column('lab_orders',sa.Column('patient_release_withdrawal_reason',sa.String(32),nullable=True))
    op.create_check_constraint('ck_lab_patient_release_withdraw_reason','lab_orders',"patient_release_withdrawal_reason IS NULL OR (patient_release_withdrawn_at IS NOT NULL AND patient_release_withdrawal_reason IN ('clinical_correction','identity_review','other_safety_review'))")
    op.drop_constraint(CHECK,'clinical_record_amendments',type_='check')
    op.create_check_constraint(CHECK,'clinical_record_amendments',NEW)
    op.create_table(TABLE,
        sa.Column('id',sa.Integer,primary_key=True),
        sa.Column('order_id',sa.Integer,sa.ForeignKey('lab_orders.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('version',sa.Integer,nullable=False),
        sa.Column('amendment_id',sa.Integer,sa.ForeignKey('clinical_record_amendments.id',ondelete='RESTRICT'),nullable=False,unique=True),
        sa.Column('recipient_user_id',sa.Integer,sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('content_sha256',sa.String(64),nullable=False),
        sa.Column('released_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('released_by_id',sa.Integer,sa.ForeignKey('staff.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('withdrawn_at',sa.DateTime(timezone=True)),
        sa.Column('withdrawn_by_id',sa.Integer,sa.ForeignKey('staff.id',ondelete='RESTRICT')),
        sa.Column('withdrawal_reason',sa.String(32)),
        sa.CheckConstraint("(withdrawn_at IS NULL AND withdrawal_reason IS NULL) OR (withdrawn_at IS NOT NULL AND withdrawal_reason IS NOT NULL AND withdrawal_reason IN ('clinical_correction','identity_review','other_safety_review'))",name='ck_lab_report_publication_reason'),
        sa.UniqueConstraint('order_id','version',name='uq_lab_report_publication_version'),
        sa.CheckConstraint('version >= 2',name='ck_lab_report_publication_version'),
        sa.CheckConstraint('length(content_sha256) = 64',name='ck_lab_report_publication_hash'),
        sa.CheckConstraint('(withdrawn_at IS NULL AND withdrawn_by_id IS NULL) OR (withdrawn_at IS NOT NULL AND withdrawn_by_id IS NOT NULL AND withdrawn_at >= released_at)',name='ck_lab_report_publication_withdrawal'))
    op.create_index('uq_lab_report_current_publication',TABLE,['order_id'],unique=True,postgresql_where=sa.text('withdrawn_at IS NULL'),sqlite_where=sa.text('withdrawn_at IS NULL'))


def downgrade():
    c=op.get_bind()
    if c.execute(sa.text('SELECT count(*) FROM lab_orders WHERE patient_release_withdrawal_reason IS NOT NULL')).scalar_one() or c.execute(sa.text('SELECT count(*) FROM lab_report_publications')).scalar_one() or c.execute(sa.text("SELECT count(*) FROM clinical_record_amendments WHERE source_record_type='lab_report'")).scalar_one():
        raise RuntimeError('Corrected laboratory report evidence requires manual downgrade approval')
    op.drop_table(TABLE)
    op.drop_constraint('ck_lab_patient_release_withdraw_reason','lab_orders',type_='check')
    op.drop_column('lab_orders','patient_release_withdrawal_reason')
    op.drop_constraint(CHECK,'clinical_record_amendments',type_='check')
    op.create_check_constraint(CHECK,'clinical_record_amendments',OLD)
