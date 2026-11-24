"""Add explicit patient lab release and result-entry display metadata.

Revision ID: c8261124a002
Revises: c8261124a001
No production execution or historical result backfill is authorized.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c8261124a002'
down_revision = 'c8261124a001'
branch_labels = None
depends_on = None

PAIR = ('(patient_released_at IS NULL AND patient_released_by_id IS NULL AND patient_release_user_id IS NULL AND patient_release_snapshot IS NULL) OR '
        '(patient_released_at IS NOT NULL AND patient_released_by_id IS NOT NULL AND patient_release_user_id IS NOT NULL AND patient_release_snapshot IS NOT NULL AND completed_at IS NOT NULL AND verified_at IS NOT NULL AND patient_released_at >= completed_at)')
WITHDRAW = ('(patient_release_withdrawn_at IS NULL AND patient_release_withdrawn_by_id IS NULL) OR '
            '(patient_release_withdrawn_at IS NOT NULL AND patient_release_withdrawn_by_id IS NOT NULL AND patient_released_at IS NOT NULL AND patient_release_withdrawn_at >= patient_released_at)')


def upgrade():
    op.add_column('lab_order_items', sa.Column('result_display_metadata', sa.JSON(none_as_null=True), nullable=True))
    for name, kind in [('patient_released_at', sa.DateTime(timezone=True)), ('patient_released_by_id', sa.Integer()), ('patient_release_user_id', sa.Integer()), ('patient_release_snapshot', sa.JSON(none_as_null=True)), ('patient_release_withdrawn_at', sa.DateTime(timezone=True)), ('patient_release_withdrawn_by_id', sa.Integer())]:
        op.add_column('lab_orders', sa.Column(name, kind, nullable=True))
    for field, table, constraint in [('patient_released_by_id','staff','fk_lab_release_doctor'), ('patient_release_user_id','users','fk_lab_release_recipient'), ('patient_release_withdrawn_by_id','staff','fk_lab_release_withdrawal_doctor')]:
        op.create_foreign_key(constraint, 'lab_orders', table, [field], ['id'])
    op.create_check_constraint('ck_lab_patient_release_pair', 'lab_orders', PAIR)
    op.create_check_constraint('ck_lab_patient_release_withdrawal', 'lab_orders', WITHDRAW)
    op.create_index('ix_lab_patient_release_lookup', 'lab_orders', ['clinic_id','patient_id','patient_release_user_id','patient_released_at'])


def downgrade():
    connection = op.get_bind()
    # No destructive rollback of recorded releases or result-entry metadata.
    for table, condition in [('lab_orders','patient_released_at IS NOT NULL OR patient_release_snapshot IS NOT NULL OR patient_release_withdrawn_at IS NOT NULL'), ('lab_order_items','result_display_metadata IS NOT NULL')]:
        if connection.execute(sa.text(f'SELECT COUNT(*) FROM {table} WHERE {condition}')).scalar_one():
            raise RuntimeError('Laboratory release evidence requires manual downgrade approval')
    op.drop_index('ix_lab_patient_release_lookup', table_name='lab_orders')
    for constraint in ['ck_lab_patient_release_withdrawal','ck_lab_patient_release_pair']:
        op.drop_constraint(constraint, 'lab_orders', type_='check')
    for constraint in ['fk_lab_release_withdrawal_doctor','fk_lab_release_recipient','fk_lab_release_doctor']:
        op.drop_constraint(constraint, 'lab_orders', type_='foreignkey')
    for field in ['patient_release_withdrawn_by_id','patient_release_withdrawn_at','patient_release_snapshot','patient_release_user_id','patient_released_by_id','patient_released_at']:
        op.drop_column('lab_orders', field)
    op.drop_column('lab_order_items','result_display_metadata')
