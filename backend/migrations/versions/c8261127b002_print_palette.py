"""Add optional paired palette columns; no clinical or payment schema change."""
from alembic import op
import sqlalchemy as sa
revision='c8261127b002'
down_revision='c8261127b001'
branch_labels=None
depends_on=None
TABLES=(('organization_branding','org'),('clinic_branding','clinic'))
CHECK="(primary_color IS NULL AND secondary_color IS NULL) OR (primary_color IS NOT NULL AND secondary_color IS NOT NULL AND length(primary_color)=7 AND length(secondary_color)=7 AND substr(primary_color,1,1)='#' AND substr(secondary_color,1,1)='#')"

def upgrade():
    for table,prefix in TABLES:
        op.add_column(table,sa.Column('primary_color',sa.String(7),nullable=True))
        op.add_column(table,sa.Column('secondary_color',sa.String(7),nullable=True))
        op.create_check_constraint(f'ck_{prefix}_brand_colors',table,CHECK)

def downgrade():
    conn=op.get_bind()
    for table,_ in TABLES:
        if conn.execute(sa.text(f'SELECT COUNT(*) FROM {table} WHERE primary_color IS NOT NULL OR secondary_color IS NOT NULL')).scalar_one():
            raise RuntimeError('destructive palette downgrade denied')
    for table,prefix in reversed(TABLES):
        op.drop_constraint(f'ck_{prefix}_brand_colors',table,type_='check')
        op.drop_column(table,'secondary_color');op.drop_column(table,'primary_color')
