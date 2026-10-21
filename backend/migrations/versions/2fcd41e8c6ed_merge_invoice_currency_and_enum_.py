"""merge invoice currency and enum migration heads

Revision ID: 2fcd41e8c6ed
Revises: d2c7f4a91b36, f3b9a84c2d71
Create Date: 2026-10-21 11:34:36.270867

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2fcd41e8c6ed'
down_revision = ('d2c7f4a91b36', 'f3b9a84c2d71')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
