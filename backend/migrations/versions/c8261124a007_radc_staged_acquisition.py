"""RAD-C B02B staged acquisition evidence, no diagnostic verification."""
from alembic import op
import sqlalchemy as sa
revision = 'c8261124a007'
down_revision = 'c8261124a006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('radiology_staged_acquisitions',
        sa.Column('publication_id', sa.Integer(), sa.ForeignKey('radiology_worklist_deliveries.publication_id'), primary_key=True),
        sa.Column('association_id', sa.Integer(), sa.ForeignKey('radiology_study_associations.id'), nullable=False),
        sa.Column('archive_key', sa.String(64), nullable=False),
        sa.Column('instance_id', sa.String(44)),
        sa.Column('series_uid', sa.String(64)),
        sa.Column('sop_uid', sa.String(64)),
        sa.Column('file_sha256', sa.String(64)),
        sa.Column('pixel_sha256', sa.String(64)),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('actor_staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('association_id', name='uq_radc_acq_association'),
        sa.UniqueConstraint('archive_key', 'instance_id', name='uq_radc_acq_instance'),
        sa.CheckConstraint("state IN ('staged_validated','quarantined')", name='ck_radc_acq_state'),
        sa.CheckConstraint("state != 'staged_validated' OR (instance_id IS NOT NULL AND series_uid IS NOT NULL AND sop_uid IS NOT NULL AND file_sha256 IS NOT NULL AND pixel_sha256 IS NOT NULL)", name='ck_radc_acq_evidence'),
        sa.CheckConstraint('file_sha256 IS NULL OR length(file_sha256) = 64', name='ck_radc_acq_digest'),
        sa.CheckConstraint('pixel_sha256 IS NULL OR length(pixel_sha256) = 64', name='ck_radc_acq_pixels'))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM radiology_staged_acquisitions')).scalar_one():
        raise RuntimeError('Staged acquisition evidence exists; destructive downgrade denied')
    op.drop_table('radiology_staged_acquisitions')
