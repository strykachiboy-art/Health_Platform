"""CE1-S01: non-executable surgical case foundation.

Revision ID: a19f7c3e2d4b
Revises: b5e1a76c4d39

Migration is created only; S01 does not apply it to a running database.
"""
from alembic import op
import sqlalchemy as sa

revision = "a19f7c3e2d4b"
down_revision = "b5e1a76c4d39"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "surgical_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("admission_id", sa.Integer(), sa.ForeignKey("admissions.id"), nullable=False),
        sa.Column("procedure_order_id", sa.Integer(), sa.ForeignKey("procedure_orders.id"), nullable=False),
        sa.Column("planned_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("procedure_order_id", name="uq_surgical_cases_procedure_order"),
        sa.CheckConstraint("status IN ('planned', 'on_hold', 'cancelled')", name="ck_surgical_cases_foundation_status"),
        sa.CheckConstraint("version >= 1", name="ck_surgical_cases_version_positive"),
    )
    for column in ("clinic_id", "patient_id", "admission_id", "planned_by_staff_id", "status"):
        op.create_index(f"ix_surgical_cases_{column}", "surgical_cases", [column])
    op.create_index(
        "ix_surgical_cases_clinic_admission_status",
        "surgical_cases", ["clinic_id", "admission_id", "status", "id"],
    )


def downgrade():
    op.drop_index("ix_surgical_cases_clinic_admission_status", table_name="surgical_cases")
    for column in ("status", "planned_by_staff_id", "admission_id", "patient_id", "clinic_id"):
        op.drop_index(f"ix_surgical_cases_{column}", table_name="surgical_cases")
    op.drop_table("surgical_cases")
