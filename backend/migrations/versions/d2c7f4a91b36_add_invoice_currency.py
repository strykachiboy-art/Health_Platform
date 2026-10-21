"""add nullable per-invoice currency

Revision ID: d2c7f4a91b36
Revises: f19b3a7d6c42
Create Date: 2026-10-21
"""

from alembic import op
import sqlalchemy as sa


revision = "d2c7f4a91b36"
down_revision = "f19b3a7d6c42"
branch_labels = None
depends_on = None


def upgrade():
    # Do not infer currency for legacy invoices.
    op.add_column(
        "invoices",
        sa.Column("currency", sa.String(length=3), nullable=True),
    )


def downgrade():
    op.drop_column("invoices", "currency")
