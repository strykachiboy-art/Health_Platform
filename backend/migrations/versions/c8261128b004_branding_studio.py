"""Staged per-field branding studio; preserve legacy paired colours."""
from alembic import op
import sqlalchemy as sa

revision = 'c8261128b004'
down_revision = 'c8261127b003'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('organization_branding', 'clinic_branding'):
        op.add_column(table, sa.Column('studio_palette', sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    conn = op.get_bind()
    for table in ('organization_branding', 'clinic_branding'):
        if conn.execute(sa.text(f'SELECT 1 FROM {table} WHERE studio_palette IS NOT NULL LIMIT 1')).first() is not None:
            raise RuntimeError('Studio settings exist; destructive downgrade denied')
    for table in ('clinic_branding', 'organization_branding'):
        op.drop_column(table, 'studio_palette')
