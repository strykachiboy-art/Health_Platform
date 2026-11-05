"""Add admission-scoped primary clinician assignments.

Revision ID: 3a8f12c6d4b9
Revises: c1d03e4f6a29
"""

from alembic import op
import sqlalchemy as sa

revision = "3a8f12c6d4b9"
down_revision = "c1d03e4f6a29"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admission_care_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "clinic_id", sa.Integer(),
            sa.ForeignKey("clinics.id"), nullable=False,
        ),
        sa.Column(
            "patient_id", sa.Integer(),
            sa.ForeignKey("patients.id"), nullable=False,
        ),
        sa.Column(
            "admission_id", sa.Integer(),
            sa.ForeignKey("admissions.id"), nullable=False,
        ),
        sa.Column(
            "staff_id", sa.Integer(),
            sa.ForeignKey("staff.id"), nullable=False,
        ),
        sa.Column(
            "assignment_kind", sa.String(length=32), nullable=False,
        ),
        sa.Column(
            "assigned_by_user_id", sa.Integer(),
            sa.ForeignKey("users.id"), nullable=False,
        ),
        sa.Column(
            "assignment_reason", sa.String(length=500), nullable=False,
        ),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column(
            "ended_by_user_id", sa.Integer(),
            sa.ForeignKey("users.id"), nullable=True,
        ),
        sa.Column("end_reason", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "assignment_kind IN ('primary_doctor', 'primary_nurse')",
            name="ck_admission_care_assignment_kind",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= assigned_at",
            name="ck_admission_care_assignment_time",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR "
            "(ended_by_user_id IS NOT NULL AND end_reason IS NOT NULL)",
            name="ck_admission_care_assignment_end_evidence",
        ),
    )

    for field in (
        "clinic_id", "patient_id", "admission_id", "staff_id",
    ):
        op.create_index(
            f"ix_admission_care_assignments_{field}",
            "admission_care_assignments",
            [field],
        )

    op.create_index(
        "ix_admission_care_assignment_history",
        "admission_care_assignments",
        ["clinic_id", "admission_id", "assigned_at"],
    )

    op.create_index(
        "uq_admission_care_assignment_active_kind",
        "admission_care_assignments",
        ["clinic_id", "admission_id", "assignment_kind"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
        sqlite_where=sa.text("ended_at IS NULL"),
    )


def downgrade():
    op.drop_table("admission_care_assignments")