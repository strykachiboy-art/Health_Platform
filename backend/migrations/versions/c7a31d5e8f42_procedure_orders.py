"""add procedure orders

Revision ID: c7a31d5e8f42
Revises: b9f10a2c4d61
"""

from alembic import op
import sqlalchemy as sa


revision = "c7a31d5e8f42"
down_revision = "b9f10a2c4d61"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "procedure_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False
        ),
        sa.Column(
            "patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False
        ),
        sa.Column(
            "admission_id", sa.Integer(), sa.ForeignKey("admissions.id"), nullable=False
        ),
        sa.Column(
            "consultation_id",
            sa.Integer(),
            sa.ForeignKey("consultations.id"),
            nullable=True,
        ),
        sa.Column(
            "ordered_by_staff_id",
            sa.Integer(),
            sa.ForeignKey("staff.id"),
            nullable=False,
        ),
        sa.Column(
            "completed_by_staff_id",
            sa.Integer(),
            sa.ForeignKey("staff.id"),
            nullable=True,
        ),
        sa.Column(
            "cancelled_by_staff_id",
            sa.Integer(),
            sa.ForeignKey("staff.id"),
            nullable=True,
        ),
        sa.Column(
            "consent_verified_by_staff_id",
            sa.Integer(),
            sa.ForeignKey("staff.id"),
            nullable=True,
        ),
        sa.Column("procedure_code", sa.String(length=64), nullable=True),
        sa.Column("procedure_name", sa.String(length=255), nullable=False),
        sa.Column("procedure_type", sa.String(length=32), nullable=False),
        sa.Column("body_site", sa.String(length=255), nullable=True),
        sa.Column("laterality", sa.String(length=32), nullable=True),
        sa.Column("indication", sa.Text(), nullable=False),
        sa.Column("prep_instructions", sa.Text(), nullable=True),
        sa.Column("anesthesia_type", sa.String(length=128), nullable=True),
        sa.Column(
            "consent_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("consent_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "priority", sa.String(length=16), nullable=False, server_default="routine"
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_summary", sa.Text(), nullable=True),
        sa.Column("procedure_note", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "procedure_type IN ("
            "'diagnostic', 'therapeutic', 'surgical', "
            "'bedside', 'interventional', 'other')",
            name="ck_procedure_orders_type",
        ),
        sa.CheckConstraint(
            "priority IN ('routine', 'urgent', 'stat')",
            name="ck_procedure_orders_priority",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'active', 'in_progress', "
            "'on_hold', 'completed', 'cancelled')",
            name="ck_procedure_orders_status",
        ),
        sa.CheckConstraint(
            "consent_required = true OR consent_verified_at IS NULL",
            name="ck_procedure_orders_consent_timestamp",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR ("
            "completed_at IS NOT NULL AND "
            "completed_by_staff_id IS NOT NULL AND "
            "performed_at IS NOT NULL AND "
            "procedure_note IS NOT NULL)",
            name="ck_procedure_orders_completed_evidence",
        ),
        sa.CheckConstraint(
            "status != 'cancelled' OR ("
            "cancelled_at IS NOT NULL AND "
            "cancelled_by_staff_id IS NOT NULL AND "
            "cancellation_reason IS NOT NULL)",
            name="ck_procedure_orders_cancelled_evidence",
        ),
    )

    for column in (
        "clinic_id", "patient_id", "admission_id", "consultation_id",
        "ordered_by_staff_id", "completed_by_staff_id",
        "cancelled_by_staff_id", "consent_verified_by_staff_id",
        "procedure_code", "procedure_type", "consent_verified_at",
        "priority", "status", "scheduled_at", "ordered_at",
        "performed_at", "completed_at", "cancelled_at",
    ):
        op.create_index(
            f"ix_procedure_orders_{column}",
            "procedure_orders",
            [column],
        )

    op.create_index(
        "ix_procedure_orders_clinic_patient_ordered",
        "procedure_orders",
        ["clinic_id", "patient_id", "ordered_at", "id"],
    )
    op.create_index(
        "ix_procedure_orders_admission_status_ordered",
        "procedure_orders",
        ["admission_id", "status", "ordered_at", "id"],
    )


def downgrade():
    op.drop_table("procedure_orders")
