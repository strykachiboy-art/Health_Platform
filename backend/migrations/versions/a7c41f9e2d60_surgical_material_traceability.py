"""CE1-S07 passive surgical trace links (source-only).

Revision ID: a7c41f9e2d60
Revises: f6b2d8a41e09
Do not apply to application DB in the S07 focused bundle.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7c41f9e2d60'
down_revision = 'f6b2d8a41e09'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'surgical_material_traces',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('clinic_id', sa.Integer(), sa.ForeignKey('clinics.id'), nullable=False),
        sa.Column('patient_id', sa.Integer(), sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('journal_id', sa.Integer(), sa.ForeignKey('surgical_evidence_journals.id'), nullable=False),
        sa.Column('trace_kind', sa.String(24), nullable=False),
        sa.Column('lab_order_id', sa.Integer(), sa.ForeignKey('lab_orders.id'), nullable=True),
        sa.Column('stock_movement_id', sa.Integer(), sa.ForeignKey('stock_movements.id'), nullable=True),
        sa.Column('blood_product_order_id', sa.Integer(), sa.ForeignKey('blood_product_orders.id'), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('identity_label', sa.String(150), nullable=False),
        sa.Column('record_note', sa.String(1200), nullable=False),
        sa.Column('recorded_by_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('recorded_at', sa.DateTime(), nullable=False),
        sa.Column('source_sha256', sa.String(64), nullable=False),
        sa.Column('evidence_sha256', sa.String(64), nullable=False),
        sa.CheckConstraint(
            "trace_kind IN ('specimen','implant','consumable','blood_product')",
            name='ck_surgical_material_trace_kind',
        ),
        sa.CheckConstraint('quantity > 0', name='ck_surgical_material_quantity'),
        sa.CheckConstraint('length(source_sha256) = 64', name='ck_surgical_material_source_digest'),
        sa.CheckConstraint('length(evidence_sha256) = 64', name='ck_surgical_material_evidence_digest'),
        sa.CheckConstraint(
            "(trace_kind = 'specimen' AND lab_order_id IS NOT NULL "
            "AND stock_movement_id IS NULL AND blood_product_order_id IS NULL AND quantity = 1) "
            "OR (trace_kind IN ('implant','consumable') AND lab_order_id IS NULL "
            "AND stock_movement_id IS NOT NULL AND blood_product_order_id IS NULL) "
            "OR (trace_kind = 'blood_product' AND lab_order_id IS NULL "
            "AND stock_movement_id IS NULL AND blood_product_order_id IS NOT NULL)",
            name='ck_surgical_material_authority_type',
        ),
        sa.UniqueConstraint('lab_order_id', name='uq_surgical_material_lab_order'),
        sa.UniqueConstraint('stock_movement_id', name='uq_surgical_material_stock_movement'),
        sa.UniqueConstraint('blood_product_order_id', name='uq_surgical_material_blood_order'),
    )
    op.create_index('ix_surgical_material_traces_clinic_id', 'surgical_material_traces', ['clinic_id'])
    op.create_index('ix_surgical_material_traces_journal_id', 'surgical_material_traces', ['journal_id'])
    op.create_index('ix_surgical_material_clinic_journal', 'surgical_material_traces', ['clinic_id', 'journal_id'])


def downgrade():
    op.drop_index('ix_surgical_material_clinic_journal', table_name='surgical_material_traces')
    op.drop_index('ix_surgical_material_traces_journal_id', table_name='surgical_material_traces')
    op.drop_index('ix_surgical_material_traces_clinic_id', table_name='surgical_material_traces')
    op.drop_table('surgical_material_traces')
