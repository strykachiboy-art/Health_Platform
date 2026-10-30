"""add roadmap-aligned eMAR medication administration core

Revision ID: d4e7c1a9f620
Revises: b7c2e1a4d905
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa


revision = "d4e7c1a9f620"
down_revision = "b7c2e1a4d905"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "medication_administrations",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "clinic_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "admission_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "prescription_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "prescription_item_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "dose_value",
            sa.Numeric(
                precision=12,
                scale=4,
            ),
            nullable=True,
        ),
        sa.Column(
            "dose_unit",
            sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            "route_code",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column(
            "administered_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "administered_by_staff_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "held_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "held_by_staff_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "hold_reason",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "refused_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "refused_recorded_by_staff_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "refusal_reason",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "missed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "missed_reason",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "cancel_reason",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "source_order_version",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "notes",
            sa.Text(),
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
        sa.CheckConstraint(
            "status IN ("
            "'scheduled', "
            "'due', "
            "'administered', "
            "'held', "
            "'refused', "
            "'missed', "
            "'cancelled'"
            ")",
            name="ck_med_admin_status",
        ),
        sa.CheckConstraint(
            "dose_value IS NULL OR dose_value > 0",
            name="ck_med_admin_dose_positive",
        ),
        sa.CheckConstraint(
            (
                "source_order_version IS NULL "
                "OR source_order_version > 0"
            ),
            name=(
                "ck_med_admin_source_order_version_positive"
            ),
        ),
        sa.CheckConstraint(
            (
                "status NOT IN ('scheduled', 'due') "
                "OR scheduled_at IS NOT NULL"
            ),
            name=(
                "ck_med_admin_scheduled_requires_time"
            ),
        ),
        sa.CheckConstraint(
            (
                "status != 'administered' "
                "OR (administered_at IS NOT NULL "
                "AND administered_by_staff_id IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_administered_requires_actor_time"
            ),
        ),
        sa.CheckConstraint(
            (
                "status != 'held' "
                "OR (held_at IS NOT NULL "
                "AND held_by_staff_id IS NOT NULL "
                "AND hold_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_held_requires_evidence"
            ),
        ),
        sa.CheckConstraint(
            (
                "status != 'refused' "
                "OR (refused_at IS NOT NULL "
                "AND refused_recorded_by_staff_id IS NOT NULL "
                "AND refusal_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_refused_requires_evidence"
            ),
        ),
        sa.CheckConstraint(
            (
                "status != 'missed' "
                "OR (missed_at IS NOT NULL "
                "AND missed_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_missed_requires_evidence"
            ),
        ),
        sa.CheckConstraint(
            (
                "status != 'cancelled' "
                "OR (cancelled_at IS NOT NULL "
                "AND cancel_reason IS NOT NULL)"
            ),
            name=(
                "ck_med_admin_cancelled_requires_evidence"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["admission_id"],
            ["admissions.id"],
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
            ["prescription_id"],
            ["prescriptions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["prescription_item_id"],
            ["prescription_items.id"],
        ),
        sa.ForeignKeyConstraint(
            ["administered_by_staff_id"],
            ["staff.id"],
        ),
        sa.ForeignKeyConstraint(
            ["held_by_staff_id"],
            ["staff.id"],
        ),
        sa.ForeignKeyConstraint(
            ["refused_recorded_by_staff_id"],
            ["staff.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "admission_id",
            "prescription_item_id",
            "scheduled_at",
            name=(
                "uq_med_admin_"
                "admission_item_scheduled"
            ),
        ),
        sa.UniqueConstraint(
            "clinic_id",
            "idempotency_key",
            name=(
                "uq_med_admin_clinic_idempotency_key"
            ),
        ),
    )

    indexes = (
        (
            "ix_medication_administrations_clinic_id",
            ["clinic_id"],
        ),
        (
            "ix_medication_administrations_patient_id",
            ["patient_id"],
        ),
        (
            "ix_medication_administrations_admission_id",
            ["admission_id"],
        ),
        (
            "ix_medication_administrations_prescription_id",
            ["prescription_id"],
        ),
        (
            "ix_medication_administrations_prescription_item_id",
            ["prescription_item_id"],
        ),
        (
            "ix_medication_administrations_scheduled_at",
            ["scheduled_at"],
        ),
        (
            "ix_medication_administrations_status",
            ["status"],
        ),
        (
            "ix_medication_administrations_administered_at",
            ["administered_at"],
        ),
        (
            "ix_medication_administrations_administered_by_staff_id",
            ["administered_by_staff_id"],
        ),
        (
            "ix_medication_administrations_created_at",
            ["created_at"],
        ),
        (
            "ix_med_admin_clinic_patient_scheduled",
            [
                "clinic_id",
                "patient_id",
                "scheduled_at",
                "id",
            ],
        ),
        (
            "ix_med_admin_admission_status_scheduled",
            [
                "admission_id",
                "status",
                "scheduled_at",
                "id",
            ],
        ),
        (
            "ix_med_admin_prescription_item_scheduled",
            [
                "prescription_item_id",
                "scheduled_at",
                "id",
            ],
        ),
    )

    for name, columns in indexes:
        op.create_index(
            name,
            "medication_administrations",
            columns,
            unique=False,
        )


def downgrade():
    for name in (
        "ix_med_admin_prescription_item_scheduled",
        "ix_med_admin_admission_status_scheduled",
        "ix_med_admin_clinic_patient_scheduled",
        "ix_medication_administrations_created_at",
        "ix_medication_administrations_administered_by_staff_id",
        "ix_medication_administrations_administered_at",
        "ix_medication_administrations_status",
        "ix_medication_administrations_scheduled_at",
        "ix_medication_administrations_prescription_item_id",
        "ix_medication_administrations_prescription_id",
        "ix_medication_administrations_admission_id",
        "ix_medication_administrations_patient_id",
        "ix_medication_administrations_clinic_id",
    ):
        op.drop_index(
            name,
            table_name="medication_administrations",
        )

    op.drop_table(
        "medication_administrations"
    )
