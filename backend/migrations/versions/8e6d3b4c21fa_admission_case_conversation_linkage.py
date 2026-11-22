"""Associate one canonical patient chat conversation with an admission.

Revision ID: 8e6d3b4c21fa
Revises: 3a8f12c6d4b9
"""

from alembic import op
import sqlalchemy as sa

revision = "8e6d3b4c21fa"
down_revision = "3a8f12c6d4b9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admission_case_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("admission_id", sa.Integer(), sa.ForeignKey("admissions.id"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("chat_conversations.id"), nullable=False),
        sa.Column("linked_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("admission_id", name="uq_admission_case_conversations_admission"),
        sa.UniqueConstraint("conversation_id", name="uq_admission_case_conversations_conversation"),
    )
    for field in ("clinic_id", "patient_id", "admission_id", "conversation_id"):
        op.create_index(
            f"ix_admission_case_conversations_{field}",
            "admission_case_conversations", [field],
        )
    op.create_index(
        "ix_admission_case_conversations_clinic_patient",
        "admission_case_conversations", ["clinic_id", "patient_id"],
    )
    op.create_index(
        "ix_admission_case_conversations_clinic_conversation",
        "admission_case_conversations", ["clinic_id", "conversation_id"],
    )


def downgrade():
    op.drop_table("admission_case_conversations")
