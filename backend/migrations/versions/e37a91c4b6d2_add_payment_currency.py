"""add expected currency to payments

Revision ID: e37a91c4b6d2
Revises: c8e4f1a9d2b7
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa


revision = "e37a91c4b6d2"
down_revision = "c8e4f1a9d2b7"
branch_labels = None
depends_on = None


def upgrade():
    # Existing rows stay NULL: we cannot safely infer their original currency.
    op.add_column(
        "payments",
        sa.Column("currency", sa.String(length=3), nullable=True),
    )


def downgrade():
    op.drop_column("payments", "currency")