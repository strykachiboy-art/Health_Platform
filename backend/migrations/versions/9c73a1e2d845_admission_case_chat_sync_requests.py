"""Persist coalesced case-chat synchronization requests.

Revision ID: 9c73a1e2d845
Revises: 8e6d3b4c21fa
"""
from alembic import op
import sqlalchemy as sa

revision = "9c73a1e2d845"
down_revision = "8e6d3b4c21fa"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admission_case_chat_sync_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("admission_id", sa.Integer(), sa.ForeignKey("admissions.id"), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_sequence", sa.Integer(), nullable=False),
        sa.Column("processed_sequence", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "clinic_id", "admission_id", name="uq_admission_case_chat_sync_clinic_admission"
        ),
        sa.CheckConstraint(
            "request_sequence >= 1 AND processed_sequence >= 0 "
            "AND request_sequence >= processed_sequence",
            name="ck_admission_case_chat_sync_sequence",
        ),
    )
    op.create_index(
        "ix_admission_case_chat_sync_pending", "admission_case_chat_sync_requests",
        ["clinic_id", "processed_sequence", "request_sequence"],
    )
    op.create_index(
        "ix_admission_case_chat_sync_requests_clinic_id", "admission_case_chat_sync_requests",
        ["clinic_id"],
    )
    op.create_index(
        "ix_admission_case_chat_sync_requests_admission_id", "admission_case_chat_sync_requests",
        ["admission_id"],
    )
    op.create_index(
        "ix_admission_case_chat_sync_requests_requested_by_user_id", "admission_case_chat_sync_requests",
        ["requested_by_user_id"],
    )


def downgrade():
    op.drop_table("admission_case_chat_sync_requests")
