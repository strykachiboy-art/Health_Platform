"""Staged tenant-owned print branding; no clinical or payment migration."""
from alembic import op
import sqlalchemy as sa

revision = 'c8261127b001'
down_revision = 'c8261124a010'
branch_labels = None
depends_on = None


def _logo_columns(prefix):
    return (sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('logo_png', sa.LargeBinary(), nullable=True),
            sa.Column('logo_sha256', sa.String(64), nullable=True),
            sa.CheckConstraint('version >= 1', name=f'ck_{prefix}_brand_version'),
            sa.CheckConstraint('(logo_png IS NULL AND logo_sha256 IS NULL) OR (logo_png IS NOT NULL AND logo_sha256 IS NOT NULL AND length(logo_png) BETWEEN 1 AND 2097152 AND length(logo_sha256) = 64)', name=f'ck_{prefix}_brand_logo'))


def upgrade():
    op.create_table('organization_branding',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), sa.ForeignKey('hospital_organizations.id'), nullable=False, unique=True),
        sa.Column('clinic_override_allowed', sa.Boolean(), nullable=False, server_default=sa.false()),
        *_logo_columns('org'))
    op.create_table('clinic_branding',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('clinic_id', sa.Integer(), nullable=False, unique=True),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['clinic_id', 'organization_id'], ['clinics.id', 'clinics.organization_id'], name='fk_clinic_brand_owner'),
        *_logo_columns('clinic'))


def downgrade():
    conn = op.get_bind()
    for table in ('clinic_branding', 'organization_branding'):
        if conn.execute(sa.text(f'SELECT 1 FROM {table} LIMIT 1')).first() is not None:
            raise RuntimeError('Branding evidence exists; destructive downgrade denied')
    op.drop_table('clinic_branding')
    op.drop_table('organization_branding')
