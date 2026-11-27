"""Minimal historical print issuance registry, no stored PDF or clinical text."""
from alembic import op
import sqlalchemy as sa
revision='c8261127b003'
down_revision='c8261127b002'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('print_issuances',
        sa.Column('token_sha256',sa.String(64),primary_key=True),
        sa.Column('clinic_id',sa.Integer(),sa.ForeignKey('clinics.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('kind',sa.String(32),nullable=False),
        sa.Column('record_id',sa.Integer(),nullable=False),
        sa.Column('source_version',sa.Integer(),nullable=False),
        sa.Column('source_sha256',sa.String(64),nullable=False),
        sa.Column('pdf_sha256',sa.String(64),nullable=False),
        sa.Column('issued_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint('record_id > 0 AND source_version >= 0',name='ck_print_issuance_record'))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM print_issuances')).scalar_one():
        raise RuntimeError('Nonempty issuance ledger downgrade denied')
    op.drop_table('print_issuances')
