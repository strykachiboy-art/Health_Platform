"""CT3C: provenance of nurse-authorized case chat synchronization.

Revision ID: b5e1a76c4d39
Revises: 9c73a1e2d845
"""
from alembic import op
import sqlalchemy as sa

revision = "b5e1a76c4d39"
down_revision = "9c73a1e2d845"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "admission_case_chat_sync_requests",
        sa.Column(
            "authorization_handoff_id",
            sa.Integer(),
            sa.ForeignKey("nursing_handoffs.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_admission_case_chat_sync_requests_authorization_handoff_id",
        "admission_case_chat_sync_requests", ["authorization_handoff_id"],
    )


def downgrade():
    op.drop_index(
        "ix_admission_case_chat_sync_requests_authorization_handoff_id",
        table_name="admission_case_chat_sync_requests",
    )
    op.drop_column("admission_case_chat_sync_requests", "authorization_handoff_id")
