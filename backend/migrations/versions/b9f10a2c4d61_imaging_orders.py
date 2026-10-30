
"""add imaging orders

Revision ID: b9f10a2c4d61
Revises: a4d8c6f2b913
"""

from alembic import op
import sqlalchemy as sa


revision = "b9f10a2c4d61"
down_revision = "a4d8c6f2b913"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "imaging_orders",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
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
            "study_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "study_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "modality",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "body_site",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "laterality",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "contrast_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "contrast_type",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "indication",
            sa.Text(),
            nullable=False,
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
            "performed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "result_summary",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "report_text",
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
            "modality IN ("
            "'xray', 'ct', 'mri', 'ultrasound', "
            "'mammography', 'fluoroscopy', "
            "'nuclear_medicine', 'other'"
            ")",
            name="ck_imaging_orders_modality",
        ),
        sa.CheckConstraint(
            "priority IN ('routine', 'urgent', 'stat')",
            name="ck_imaging_orders_priority",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'pending', 'active', 'in_progress', "
            "'on_hold', 'completed', 'cancelled'"
            ")",
            name="ck_imaging_orders_status",
        ),
        sa.CheckConstraint(
            "contrast_required = true "
            "OR contrast_type IS NULL",
            name=(
                "ck_imaging_orders_"
                "contrast_type_requires_contrast"
            ),
        ),
        sa.CheckConstraint(
            "status != 'completed' "
            "OR (completed_at IS NOT NULL "
            "AND completed_by_staff_id IS NOT NULL "
            "AND report_text IS NOT NULL)",
            name="ck_imaging_orders_completed_evidence",
        ),
        sa.CheckConstraint(
            "status != 'cancelled' "
            "OR (cancelled_at IS NOT NULL "
            "AND cancelled_by_staff_id IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_imaging_orders_cancelled_evidence",
        ),
    )

    op.create_index(
        "ix_imaging_orders_clinic_patient_ordered",
        "imaging_orders",
        [
            "clinic_id",
            "patient_id",
            "ordered_at",
            "id",
        ],
    )

    op.create_index(
        "ix_imaging_orders_admission_status_ordered",
        "imaging_orders",
        [
            "admission_id",
            "status",
            "ordered_at",
            "id",
        ],
    )

    op.create_index(
        "ix_imaging_orders_clinic_status_scheduled",
        "imaging_orders",
        [
            "clinic_id",
            "status",
            "scheduled_at",
            "id",
        ],
    )

    for column in (
        "clinic_id",
        "patient_id",
        "admission_id",
        "consultation_id",
        "ordered_by_staff_id",
        "completed_by_staff_id",
        "cancelled_by_staff_id",
        "study_code",
        "modality",
        "priority",
        "status",
        "scheduled_at",
        "ordered_at",
        "performed_at",
        "completed_at",
        "cancelled_at",
    ):
        op.create_index(
            f"ix_imaging_orders_{column}",
            "imaging_orders",
            [column],
        )


def downgrade():
    op.drop_table(
        "imaging_orders"
    )
