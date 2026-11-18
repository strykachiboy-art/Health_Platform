"""GAP-B Gate 3: explicit clinic memberships and organization roles.

Only approved disposable PostgreSQL testing may execute this migration.
No automatic user/clinic clinical-rights backfill is permitted.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'b4f6e8c20d51'
down_revision = 'a6b71c2d9e40'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tenant_memberships', sa.Column(
        'role', sa.String(32), nullable=False, server_default='member'))
    op.add_column('tenant_memberships', sa.Column(
        'status', sa.String(16), nullable=False, server_default='invited'))
    op.execute("UPDATE tenant_memberships SET status = 'active' WHERE is_active = true")
    op.create_check_constraint('ck_tenant_memberships_role', 'tenant_memberships',
        "role IN ('member', 'organization_admin', 'billing_admin')")
    op.create_check_constraint('ck_tenant_memberships_status', 'tenant_memberships',
        "status IN ('invited', 'active', 'suspended', 'revoked')")
    op.create_table('clinic_memberships',
        sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('clinic_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(32), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('clinic_id', 'user_id', name='uq_clinic_memberships_clinic_user'),
        sa.ForeignKeyConstraint(['clinic_id', 'organization_id'],
            ['clinics.id', 'clinics.organization_id'],
            name='fk_clinic_memberships_clinic_same_organization'),
        sa.ForeignKeyConstraint(['organization_id', 'user_id'],
            ['tenant_memberships.organization_id', 'tenant_memberships.user_id'],
            name='fk_clinic_memberships_user_same_organization'),
    )
    op.create_index('ix_clinic_memberships_user_id', 'clinic_memberships', ['user_id'])
    op.create_index('ix_clinic_memberships_organization_id', 'clinic_memberships', ['organization_id'])


def downgrade():
    conn = op.get_bind()
    if conn.execute(sa.text('SELECT COUNT(*) FROM clinic_memberships')).scalar_one():
        raise RuntimeError('GAP-B downgrade refuses existing clinic grants')
    if conn.execute(sa.text('SELECT COUNT(*) FROM tenant_memberships')).scalar_one():
        raise RuntimeError('GAP-B downgrade refuses loss of tenant membership role/status')
    op.drop_index('ix_clinic_memberships_organization_id', table_name='clinic_memberships')
    op.drop_index('ix_clinic_memberships_user_id', table_name='clinic_memberships')
    op.drop_table('clinic_memberships')
    op.drop_constraint('ck_tenant_memberships_status', 'tenant_memberships', type_='check')
    op.drop_constraint('ck_tenant_memberships_role', 'tenant_memberships', type_='check')
    op.drop_column('tenant_memberships', 'status')
    op.drop_column('tenant_memberships', 'role')
