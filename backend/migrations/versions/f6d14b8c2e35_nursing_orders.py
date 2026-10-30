"""add nursing orders

Revision ID: f6d14b8c2e35
Revises: e1c93a7b5d24
"""

from alembic import op
import sqlalchemy as sa


revision = "f6d14b8c2e35"
down_revision = "e1c93a7b5d24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nursing_orders",
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
            "assigned_to_staff_id",
            sa.Integer(),
            sa.ForeignKey("staff.id"),
            nullable=True,
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
            "category",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "order_text",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "instructions",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "frequency",
            sa.String(length=24),
            nullable=False,
        ),
        sa.Column(
            "custom_frequency",
            sa.String(length=255),
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
            "category IN ("
            "'monitoring', 'activity', 'positioning', "
            "'wound_care', 'intake_output', 'safety', "
            "'specimen', 'respiratory', 'catheter', "
            "'hygiene', 'communication', 'other')",
            name="ck_nursing_orders_category",
        ),
        sa.CheckConstraint(
            "frequency IN ("
            "'once', 'q1h', 'q2h', 'q4h', 'q6h', "
            "'q8h', 'q12h', 'daily', 'prn', "
            "'continuous', 'custom')",
            name="ck_nursing_orders_frequency",
        ),
        sa.CheckConstraint(
            "frequency != 'custom' OR custom_frequency IS NOT NULL",
            name="ck_nursing_orders_custom_frequency",
        ),
        sa.CheckConstraint(
            "priority IN ('routine', 'urgent', 'stat')",
            name="ck_nursing_orders_priority",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'active', 'in_progress', "
            "'on_hold', 'completed', 'cancelled')",
            name="ck_nursing_orders_status",
        ),
        sa.CheckConstraint(
            "status != 'completed' "
            "OR (completed_at IS NOT NULL "
            "AND completed_by_staff_id IS NOT NULL "
            "AND completion_note IS NOT NULL)",
            name="ck_nursing_orders_completed_evidence",
        ),
        sa.CheckConstraint(
            "status != 'cancelled' "
            "OR (cancelled_at IS NOT NULL "
            "AND cancelled_by_staff_id IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_nursing_orders_cancelled_evidence",
        ),
    )

    for column in (
        "clinic_id",
        "patient_id",
        "admission_id",
        "consultation_id",
        "ordered_by_staff_id",
        "assigned_to_staff_id",
        "completed_by_staff_id",
        "cancelled_by_staff_id",
        "category",
        "frequency",
        "priority",
        "status",
        "scheduled_at",
        "ordered_at",
        "completed_at",
        "cancelled_at",
    ):
        op.create_index(
            f"ix_nursing_orders_{column}",
            "nursing_orders",
            [column],
        )

    op.create_index(
        "ix_nursing_orders_clinic_patient_ordered",
        "nursing_orders",
        ["clinic_id", "patient_id", "ordered_at", "id"],
    )
    op.create_index(
        "ix_nursing_orders_admission_status_due",
        "nursing_orders",
        ["admission_id", "status", "scheduled_at", "id"],
    )

    op.create_table(
        "nursing_order_executions",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "nursing_order_id",
            sa.Integer(),
            sa.ForeignKey(
                "nursing_orders.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
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
            "performed_by_staff_id",
            sa.Integer(),
            sa.ForeignKey("staff.id"),
            nullable=False,
        ),
        sa.Column(
            "performed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "result_note",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )

    for column in (
        "nursing_order_id",
        "clinic_id",
        "patient_id",
        "admission_id",
        "performed_by_staff_id",
        "performed_at",
    ):
        op.create_index(
            f"ix_nursing_order_executions_{column}",
            "nursing_order_executions",
            [column],
        )

    op.create_index(
        "ix_nursing_order_executions_order_performed",
        "nursing_order_executions",
        ["nursing_order_id", "performed_at", "id"],
    )
    op.create_index(
        "ix_nursing_order_executions_admission_performed",
        "nursing_order_executions",
        ["admission_id", "performed_at", "id"],
    )


def downgrade():
    op.drop_table("nursing_order_executions")
    op.drop_table("nursing_orders")
