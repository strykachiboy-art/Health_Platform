"""P23D.E S01: durable doctor emergency intent (migration source only).

Revision ID: e24a9bd7c101
Revises: b5e1a76c4d39
"""
from alembic import op
import sqlalchemy as sa

revision = "e24a9bd7c101"
down_revision = "b5e1a76c4d39"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "blood_product_orders",
        sa.Column("emergency_release_requested", sa.Boolean(),
                  server_default=sa.false(), nullable=False),
    )
    op.add_column("blood_product_orders",
                  sa.Column("emergency_request_reason", sa.Text(), nullable=True))
    op.add_column("blood_product_orders",
                  sa.Column("emergency_requested_at", sa.DateTime(timezone=True),
                            nullable=True))
    op.add_column("blood_product_orders",
                  sa.Column("emergency_requested_by_staff_id", sa.Integer(),
                            nullable=True))
    op.create_foreign_key(
        "fk_blood_product_orders_emergency_requested_by_staff_id",
        "blood_product_orders", "staff",
        ["emergency_requested_by_staff_id"], ["id"],
    )
    op.create_check_constraint(
        "ck_blood_product_orders_emergency_request_evidence",
        "blood_product_orders",
        "emergency_release_requested = false OR "
        "(emergency_request_reason IS NOT NULL AND "
        "emergency_requested_by_staff_id IS NOT NULL AND "
        "emergency_requested_at IS NOT NULL AND priority = 'stat')",
    )


def downgrade():
    op.drop_constraint(
        "ck_blood_product_orders_emergency_request_evidence",
        "blood_product_orders", type_="check",
    )
    op.drop_constraint(
        "fk_blood_product_orders_emergency_requested_by_staff_id",
        "blood_product_orders", type_="foreignkey",
    )
    op.drop_column("blood_product_orders", "emergency_requested_by_staff_id")
    op.drop_column("blood_product_orders", "emergency_requested_at")
    op.drop_column("blood_product_orders", "emergency_request_reason")
    op.drop_column("blood_product_orders", "emergency_release_requested")
