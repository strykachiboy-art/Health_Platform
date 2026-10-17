"""add durable offline operation dispositions

Revision ID: f19b3a7d6c42
Revises: c8e4f1a9d2b7
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa


revision = "f19b3a7d6c42"
down_revision = "e37a91c4b6d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "offline_operation_dispositions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("operation_type", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("resolution", sa.String(length=48), nullable=False),
        sa.Column("reason_code", sa.String(length=48), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "clinic_id", "user_id", "operation_id",
            name="uq_offline_disposition_user_clinic_operation_id",
        ),
        sa.UniqueConstraint(
            "clinic_id", "user_id", "operation_type", "idempotency_key",
            name="uq_offline_disposition_user_clinic_idempotency",
        ),
        sa.CheckConstraint(
            "resolution = 'confirmed_no_effect'",
            name="ck_offline_disposition_resolution",
        ),
        sa.CheckConstraint(
            "reason_code IN ('verified_not_sent', 'manual_reconciliation_no_effect')",
            name="ck_offline_disposition_reason_code",
        ),
    )
    op.create_index(
        "ix_offline_operation_dispositions_clinic_user_created_at",
        "offline_operation_dispositions",
        ["clinic_id", "user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_offline_operation_dispositions_user_id",
        "offline_operation_dispositions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_offline_operation_dispositions_clinic_id",
        "offline_operation_dispositions",
        ["clinic_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_offline_operation_dispositions_clinic_id",
        table_name="offline_operation_dispositions",
    )
    op.drop_index(
        "ix_offline_operation_dispositions_user_id",
        table_name="offline_operation_dispositions",
    )
    op.drop_index(
        "ix_offline_operation_dispositions_clinic_user_created_at",
        table_name="offline_operation_dispositions",
    )
    op.drop_table("offline_operation_dispositions")