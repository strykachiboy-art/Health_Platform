"""RAD-C B02A durable staged worklist delivery evidence."""
from alembic import op
import sqlalchemy as sa
revision = 'c8261124a006'
down_revision = 'c8261124a005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('radiology_worklist_deliveries',
        sa.Column('publication_id', sa.Integer(), sa.ForeignKey('radiology_worklist_publications.id'), primary_key=True),
        sa.Column('archive_key', sa.String(64), nullable=False),
        sa.Column('external_id', sa.String(36)),
        sa.Column('payload_sha256', sa.String(64), nullable=False),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('actor_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('attempted_at', sa.DateTime(), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime()),
        sa.Column('withdrawn_at', sa.DateTime()),
        sa.UniqueConstraint('archive_key', 'external_id', name='uq_radc_delivery_external'),
        sa.CheckConstraint("state IN ('dispatch_pending','acknowledged','withdraw_pending','withdrawn')", name='ck_radc_delivery_state'),
        sa.CheckConstraint("state = 'dispatch_pending' OR external_id IS NOT NULL", name='ck_radc_delivery_identity'),
        sa.CheckConstraint("state = 'dispatch_pending' OR acknowledged_at IS NOT NULL", name='ck_radc_delivery_ack'),
        sa.CheckConstraint("(state = 'withdrawn' AND withdrawn_at IS NOT NULL) OR (state != 'withdrawn' AND withdrawn_at IS NULL)", name='ck_radc_delivery_withdrawal'),
        sa.CheckConstraint('length(payload_sha256) = 64', name='ck_radc_delivery_digest'))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM radiology_worklist_deliveries')).scalar_one():
        raise RuntimeError('Worklist delivery evidence exists; destructive downgrade denied')
    op.drop_table('radiology_worklist_deliveries')
