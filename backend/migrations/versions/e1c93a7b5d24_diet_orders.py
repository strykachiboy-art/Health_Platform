"""add diet orders

Revision ID: e1c93a7b5d24
Revises: d4b82f6a9c13
"""

from alembic import op
import sqlalchemy as sa


revision = "e1c93a7b5d24"
down_revision = "d4b82f6a9c13"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "diet_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "clinic_id",
            sa.Integer(),
            sa.ForeignKey("clinics.id"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            sa.Integer(),
            sa.ForeignKey("patients.id"),
            nullable=False,
        ),
        sa.Column(
            "admission_id",
            sa.Integer(),
            sa.ForeignKey("admissions.id"),
            nullable=False,
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
        sa.Column("diet_type", sa.String(length=32), nullable=False),
        sa.Column(
            "custom_description",
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            "feeding_route",
            sa.String(length=24),
            nullable=True,
        ),
        sa.Column(
            "texture",
            sa.String(length=24),
            nullable=True,
        ),
        sa.Column(
            "fluid_restriction_ml_per_day",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "sodium_restriction_mg_per_day",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "calorie_target_kcal_per_day",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "instructions",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "priority",
            sa.String(length=16),
            nullable=False,
            server_default="routine",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "start_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "ordered_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "completion_note",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "cancellation_reason",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
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
        sa.CheckConstraint(
            "diet_type IN ("
            "'regular', 'clear_liquid', 'full_liquid', 'soft', "
            "'diabetic', 'renal', 'cardiac', 'low_sodium', "
            "'high_protein', 'npo', 'enteral', 'custom')",
            name="ck_diet_orders_type",
        ),
        sa.CheckConstraint(
            "feeding_route IS NULL OR feeding_route IN "
            "('oral', 'enteral', 'parenteral')",
            name="ck_diet_orders_feeding_route",
        ),
        sa.CheckConstraint(
            "texture IS NULL OR texture IN "
            "('regular', 'soft', 'minced', 'pureed', 'liquid')",
            name="ck_diet_orders_texture",
        ),
        sa.CheckConstraint(
            "priority IN ('routine', 'urgent', 'stat')",
            name="ck_diet_orders_priority",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'active', 'in_progress', "
            "'on_hold', 'completed', 'cancelled')",
            name="ck_diet_orders_status",
        ),
        sa.CheckConstraint(
            "fluid_restriction_ml_per_day IS NULL "
            "OR fluid_restriction_ml_per_day > 0",
            name="ck_diet_orders_fluid_restriction_positive",
        ),
        sa.CheckConstraint(
            "sodium_restriction_mg_per_day IS NULL "
            "OR sodium_restriction_mg_per_day > 0",
            name="ck_diet_orders_sodium_restriction_positive",
        ),
        sa.CheckConstraint(
            "calorie_target_kcal_per_day IS NULL "
            "OR calorie_target_kcal_per_day > 0",
            name="ck_diet_orders_calorie_target_positive",
        ),
        sa.CheckConstraint(
            "diet_type != 'npo' "
            "OR (feeding_route IS NULL AND texture IS NULL)",
            name="ck_diet_orders_npo_no_route_texture",
        ),
        sa.CheckConstraint(
            "diet_type = 'npo' OR feeding_route IS NOT NULL",
            name="ck_diet_orders_non_npo_requires_route",
        ),
        sa.CheckConstraint(
            "diet_type != 'custom' OR custom_description IS NOT NULL",
            name="ck_diet_orders_custom_description",
        ),
        sa.CheckConstraint(
            "status != 'completed' "
            "OR (completed_at IS NOT NULL "
            "AND completed_by_staff_id IS NOT NULL "
            "AND completion_note IS NOT NULL)",
            name="ck_diet_orders_completed_evidence",
        ),
        sa.CheckConstraint(
            "status != 'cancelled' "
            "OR (cancelled_at IS NOT NULL "
            "AND cancelled_by_staff_id IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_diet_orders_cancelled_evidence",
        ),
    )

    for column in (
        "clinic_id",
        "patient_id",
        "admission_id",
        "consultation_id",
        "ordered_by_staff_id",
        "completed_by_staff_id",
        "cancelled_by_staff_id",
        "diet_type",
        "feeding_route",
        "priority",
        "status",
        "start_at",
        "ordered_at",
        "completed_at",
        "cancelled_at",
    ):
        op.create_index(
            f"ix_diet_orders_{column}",
            "diet_orders",
            [column],
        )

    op.create_index(
        "ix_diet_orders_clinic_patient_ordered",
        "diet_orders",
        ["clinic_id", "patient_id", "ordered_at", "id"],
    )
    op.create_index(
        "ix_diet_orders_admission_status_ordered",
        "diet_orders",
        ["admission_id", "status", "ordered_at", "id"],
    )


def downgrade():
    op.drop_table("diet_orders")
