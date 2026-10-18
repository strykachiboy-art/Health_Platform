"""Add the missing WARD member to the PostgreSQL reporttype enum."""

from alembic import op


revision = "f3b9a84c2d71"
down_revision = "a6e2c9517b30"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TYPE public.reporttype "
        "ADD VALUE IF NOT EXISTS 'WARD'"
    )


def downgrade():
    raise NotImplementedError(
        "Downgrade is intentionally unsupported because PostgreSQL enum "
        "values cannot be safely removed once application data may use them."
    )