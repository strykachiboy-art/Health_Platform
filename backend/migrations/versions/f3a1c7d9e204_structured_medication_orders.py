"""Add structured medication-order fields.

Revision ID: f3a1c7d9e204
Revises: 2fcd41e8c6ed
"""

from alembic import op
import sqlalchemy as sa


revision = "f3a1c7d9e204"
down_revision = "2fcd41e8c6ed"
branch_labels = None
depends_on = None


def upgrade():
    columns = (
        sa.Column("dose_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("dose_unit", sa.String(length=32), nullable=True),
        sa.Column("route_code", sa.String(length=64), nullable=True),
        sa.Column("route_display", sa.String(length=128), nullable=True),
        sa.Column("frequency_code", sa.String(length=64), nullable=True),
        sa.Column("frequency_interval", sa.Integer(), nullable=True),
        sa.Column(
            "frequency_interval_unit",
            sa.String(length=16),
            nullable=True,
        ),
        sa.Column("scheduled_times", sa.JSON(), nullable=True),
        sa.Column(
            "start_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "end_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "is_prn",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("prn_reason", sa.String(length=500), nullable=True),
        sa.Column("max_single_dose", sa.Numeric(12, 4), nullable=True),
        sa.Column("max_daily_dose", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "administration_instructions",
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            "order_priority",
            sa.String(length=16),
            nullable=False,
            server_default="routine",
        ),
        sa.Column(
            "order_type",
            sa.String(length=16),
            nullable=False,
            server_default="outpatient",
        ),
        sa.Column(
            "ordered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    for column in columns:
        op.add_column(
            "prescription_items",
            column,
        )

    op.execute(
        sa.text(
            """
            UPDATE prescription_items
            SET ordered_at = (
                SELECT prescriptions.issued_at
                FROM prescriptions
                WHERE prescriptions.id =
                    prescription_items.prescription_id
            )
            WHERE ordered_at IS NULL
            """
        )
    )

    op.alter_column(
        "prescription_items",
        "ordered_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )

    constraints = (
        (
            "ck_prescription_item_dose_value_positive",
            "dose_value IS NULL OR dose_value > 0",
        ),
        (
            "ck_prescription_item_dose_pair",
            "(dose_value IS NULL AND dose_unit IS NULL) OR "
            "(dose_value IS NOT NULL AND dose_unit IS NOT NULL)",
        ),
        (
            "ck_prescription_item_frequency_interval_positive",
            "frequency_interval IS NULL OR frequency_interval > 0",
        ),
        (
            "ck_prescription_item_frequency_pair",
            "(frequency_interval IS NULL AND "
            "frequency_interval_unit IS NULL) OR "
            "(frequency_interval IS NOT NULL AND "
            "frequency_interval_unit IS NOT NULL)",
        ),
        (
            "ck_prescription_item_frequency_interval_unit",
            "frequency_interval_unit IS NULL OR "
            "frequency_interval_unit IN "
            "('minute', 'hour', 'day', 'week')",
        ),
        (
            "ck_prescription_item_route_display_requires_code",
            "route_display IS NULL OR route_code IS NOT NULL",
        ),
        (
            "ck_prescription_item_max_single_dose_positive",
            "max_single_dose IS NULL OR max_single_dose > 0",
        ),
        (
            "ck_prescription_item_max_daily_dose_positive",
            "max_daily_dose IS NULL OR max_daily_dose > 0",
        ),
        (
            "ck_prescription_item_max_daily_ge_single",
            "max_single_dose IS NULL OR max_daily_dose IS NULL OR "
            "max_daily_dose >= max_single_dose",
        ),
        (
            "ck_prescription_item_max_dose_requires_unit",
            "(max_single_dose IS NULL AND max_daily_dose IS NULL) OR "
            "dose_unit IS NOT NULL",
        ),
        (
            "ck_prescription_item_prn_reason",
            "(is_prn = false AND prn_reason IS NULL) OR "
            "(is_prn = true AND prn_reason IS NOT NULL)",
        ),
        (
            "ck_prescription_item_time_order",
            "end_at IS NULL OR start_at IS NULL OR end_at >= start_at",
        ),
        (
            "ck_prescription_item_order_priority",
            "order_priority IN ('routine', 'urgent', 'stat')",
        ),
        (
            "ck_prescription_item_order_type",
            "order_type IN ('inpatient', 'outpatient', 'discharge')",
        ),
    )

    for name, condition in constraints:
        op.create_check_constraint(
            name,
            "prescription_items",
            condition,
        )


def downgrade():
    for name in (
        "ck_prescription_item_order_type",
        "ck_prescription_item_order_priority",
        "ck_prescription_item_time_order",
        "ck_prescription_item_prn_reason",
        "ck_prescription_item_max_dose_requires_unit",
        "ck_prescription_item_max_daily_ge_single",
        "ck_prescription_item_max_daily_dose_positive",
        "ck_prescription_item_max_single_dose_positive",
        "ck_prescription_item_route_display_requires_code",
        "ck_prescription_item_frequency_interval_unit",
        "ck_prescription_item_frequency_pair",
        "ck_prescription_item_frequency_interval_positive",
        "ck_prescription_item_dose_pair",
        "ck_prescription_item_dose_value_positive",
    ):
        op.drop_constraint(
            name,
            "prescription_items",
            type_="check",
        )

    for column in (
        "ordered_at",
        "order_type",
        "order_priority",
        "administration_instructions",
        "max_daily_dose",
        "max_single_dose",
        "prn_reason",
        "is_prn",
        "end_at",
        "start_at",
        "scheduled_times",
        "frequency_interval_unit",
        "frequency_interval",
        "frequency_code",
        "route_display",
        "route_code",
        "dose_unit",
        "dose_value",
    ):
        op.drop_column(
            "prescription_items",
            column,
        )
