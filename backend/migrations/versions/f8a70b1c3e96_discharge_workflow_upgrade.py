"""upgrade clinical discharge workflow and add discharge summaries

Revision ID: f8a70b1c3e96
Revises: e7f69a0b2d85
"""

from alembic import op
import sqlalchemy as sa


revision = "f8a70b1c3e96"
down_revision = "e7f69a0b2d85"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "discharge_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("admission_id", sa.Integer(), nullable=False),
        sa.Column(
            "medication_reconciliation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "discharged_by_staff_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("primary_diagnosis", sa.Text(), nullable=False),
        sa.Column(
            "secondary_diagnoses",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "condition_at_discharge",
            sa.Text(),
            nullable=False,
        ),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("follow_up_plan", sa.Text(), nullable=False),
        sa.Column(
            "final_medication_list",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "destination",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "discharged_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "record_status",
            sa.String(length=16),
            server_default="draft",
            nullable=False,
        ),
        sa.Column(
            "record_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "finalized_by_staff_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "finalized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "latest_amendment_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id"],
            ["clinics.id"],
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
        ),
        sa.ForeignKeyConstraint(
            ["admission_id"],
            ["admissions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["medication_reconciliation_id"],
            ["medication_reconciliations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["discharged_by_staff_id"],
            ["staff.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["finalized_by_staff_id"],
            ["staff.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["latest_amendment_id"],
            ["clinical_record_amendments.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "admission_id",
            name="uq_discharge_summaries_admission",
        ),
        sa.CheckConstraint(
            "record_status IN ('draft', 'final', 'amended')",
            name="ck_discharge_summary_record_status",
        ),
        sa.CheckConstraint(
            "record_version >= 1",
            name="ck_discharge_summary_record_version_positive",
        ),
        sa.CheckConstraint(
            "("
            "record_status = 'draft' "
            "AND record_version = 1 "
            "AND finalized_by_staff_id IS NULL "
            "AND finalized_at IS NULL "
            "AND latest_amendment_id IS NULL"
            ") OR ("
            "record_status = 'final' "
            "AND record_version = 1 "
            "AND finalized_by_staff_id IS NOT NULL "
            "AND finalized_at IS NOT NULL "
            "AND latest_amendment_id IS NULL"
            ") OR ("
            "record_status = 'amended' "
            "AND record_version >= 2 "
            "AND finalized_by_staff_id IS NOT NULL "
            "AND finalized_at IS NOT NULL "
            "AND latest_amendment_id IS NOT NULL"
            ")",
            name="ck_discharge_summary_record_linkage",
        ),
    )

    for name, columns in (
        (
            "ix_discharge_summary_clinic_patient_discharged",
            ["clinic_id", "patient_id", "discharged_at", "id"],
        ),
        (
            "ix_discharge_summary_clinic_record_status",
            ["clinic_id", "record_status"],
        ),
        ("ix_discharge_summaries_clinic_id", ["clinic_id"]),
        ("ix_discharge_summaries_patient_id", ["patient_id"]),
        ("ix_discharge_summaries_admission_id", ["admission_id"]),
        (
            "ix_discharge_summaries_medication_reconciliation_id",
            ["medication_reconciliation_id"],
        ),
        (
            "ix_discharge_summaries_discharged_by_staff_id",
            ["discharged_by_staff_id"],
        ),
        ("ix_discharge_summaries_discharged_at", ["discharged_at"]),
        ("ix_discharge_summaries_record_status", ["record_status"]),
        (
            "ix_discharge_summaries_finalized_by_staff_id",
            ["finalized_by_staff_id"],
        ),
        (
            "ix_discharge_summaries_latest_amendment_id",
            ["latest_amendment_id"],
        ),
    ):
        op.create_index(
            name,
            "discharge_summaries",
            columns,
        )


def downgrade():
    op.drop_table("discharge_summaries")
