"""RAD-C B02C bounded staged CT inventory."""
from alembic import op
import sqlalchemy as sa
revision = 'c8261124a008'
down_revision = 'c8261124a007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('radiology_staged_ct_studies',
        sa.Column('publication_id', sa.Integer(), sa.ForeignKey('radiology_worklist_deliveries.publication_id'), primary_key=True),
        sa.Column('association_id', sa.Integer(), sa.ForeignKey('radiology_study_associations.id'), nullable=False),
        sa.Column('archive_key', sa.String(64), nullable=False),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('manifest', sa.JSON(), nullable=False),
        sa.Column('manifest_sha256', sa.String(64)),
        sa.Column('instance_count', sa.Integer(), nullable=False),
        sa.Column('actor_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('association_id', name='uq_radc_ct_association'),
        sa.CheckConstraint("state IN ('staged_validated','quarantined')", name='ck_radc_ct_state'),
        sa.CheckConstraint("(state = 'staged_validated' AND manifest_sha256 IS NOT NULL AND instance_count BETWEEN 1 AND 16) OR (state = 'quarantined' AND manifest_sha256 IS NULL AND instance_count = 0)", name='ck_radc_ct_evidence'),
        sa.CheckConstraint('manifest_sha256 IS NULL OR length(manifest_sha256) = 64', name='ck_radc_ct_digest'))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM radiology_staged_ct_studies')).scalar_one():
        raise RuntimeError('CT validation evidence exists; destructive downgrade denied')
    op.drop_table('radiology_staged_ct_studies')
