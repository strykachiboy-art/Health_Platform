"""add blood product orders

Revision ID: d4b82f6a9c13
Revises: c7a31d5e8f42
"""

from alembic import op
import sqlalchemy as sa


revision = "d4b82f6a9c13"
down_revision = "c7a31d5e8f42"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "blood_product_orders",
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
            "compatibility_verified_by_staff_id",
            sa.Integer(),
            sa.ForeignKey("staff.id"),
            nullable=True,
        ),
        sa.Column(
            "fulfilled_by_staff_id",
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
        sa.Column("product_type", sa.String(length=40), nullable=False),
        sa.Column("units_requested", sa.Integer(), nullable=False),
        sa.Column(
            "units_issued",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("indication", sa.Text(), nullable=False),
        sa.Column("special_requirements", sa.Text(), nullable=True),
        sa.Column(
            "patient_blood_type_at_order",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "patient_blood_type_verified",
            sa.String(length=16),
            nullable=True,
        ),
        sa.Column(
            "product_blood_type",
            sa.String(length=16),
            nullable=True,
        ),
        sa.Column(
            "compatibility_status",
            sa.String(length=24),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "crossmatch_reference",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column("compatibility_notes", sa.Text(), nullable=True),
        sa.Column(
            "compatibility_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "emergency_release_reason",
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
            "scheduled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "ordered_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("fulfillment_note", sa.Text(), nullable=True),
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
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
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
            "product_type IN ("
            "'packed_red_blood_cells', 'whole_blood', "
            "'platelets', 'fresh_frozen_plasma', "
            "'cryoprecipitate')",
            name="ck_blood_product_orders_product_type",
        ),
        sa.CheckConstraint(
            "units_requested > 0",
            name="ck_blood_product_orders_units_requested",
        ),
        sa.CheckConstraint(
            "units_issued >= 0 AND units_issued <= units_requested",
            name="ck_blood_product_orders_units_issued",
        ),
        sa.CheckConstraint(
            "priority IN ('routine', 'urgent', 'stat')",
            name="ck_blood_product_orders_priority",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'active', 'in_progress', "
            "'on_hold', 'completed', 'cancelled')",
            name="ck_blood_product_orders_status",
        ),
        sa.CheckConstraint(
            "compatibility_status IN ("
            "'pending', 'compatible', 'emergency_release')",
            name="ck_blood_product_orders_compatibility_status",
        ),
        sa.CheckConstraint(
            "compatibility_status != 'compatible' "
            "OR (compatibility_verified_at IS NOT NULL "
            "AND compatibility_verified_by_staff_id IS NOT NULL "
            "AND crossmatch_reference IS NOT NULL)",
            name="ck_blood_product_orders_compatible_evidence",
        ),
        sa.CheckConstraint(
            "compatibility_status != 'emergency_release' "
            "OR (compatibility_verified_at IS NOT NULL "
            "AND compatibility_verified_by_staff_id IS NOT NULL "
            "AND emergency_release_reason IS NOT NULL)",
            name="ck_blood_product_orders_emergency_evidence",
        ),
        sa.CheckConstraint(
            "status != 'completed' "
            "OR (completed_at IS NOT NULL "
            "AND fulfilled_by_staff_id IS NOT NULL "
            "AND units_issued > 0 "
            "AND fulfillment_note IS NOT NULL "
            "AND compatibility_status IN "
            "('compatible', 'emergency_release'))",
            name="ck_blood_product_orders_completed_evidence",
        ),
        sa.CheckConstraint(
            "status != 'cancelled' "
            "OR (cancelled_at IS NOT NULL "
            "AND cancelled_by_staff_id IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_blood_product_orders_cancelled_evidence",
        ),
    )

    for column in (
        "clinic_id",
        "patient_id",
        "admission_id",
        "consultation_id",
        "ordered_by_staff_id",
        "compatibility_verified_by_staff_id",
        "fulfilled_by_staff_id",
        "cancelled_by_staff_id",
        "product_type",
        "compatibility_status",
        "crossmatch_reference",
        "compatibility_verified_at",
        "priority",
        "status",
        "scheduled_at",
        "ordered_at",
        "completed_at",
        "cancelled_at",
    ):
        op.create_index(
            f"ix_blood_product_orders_{column}",
            "blood_product_orders",
            [column],
        )

    op.create_index(
        "ix_blood_product_orders_clinic_patient_ordered",
        "blood_product_orders",
        ["clinic_id", "patient_id", "ordered_at", "id"],
    )
    op.create_index(
        "ix_blood_product_orders_admission_status_ordered",
        "blood_product_orders",
        ["admission_id", "status", "ordered_at", "id"],
    )


def downgrade():
    op.drop_table("blood_product_orders")
