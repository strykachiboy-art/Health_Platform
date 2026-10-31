"""add consultation record finalization metadata

Revision ID: b4c36d7e9a52
Revises: a7e25c9d4f61
"""

from alembic import op
import sqlalchemy as sa


revision = "b4c36d7e9a52"
down_revision = "a7e25c9d4f61"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "consultations",
        sa.Column(
            "record_status",
            sa.String(length=16),
            server_default="draft",
            nullable=False,
        ),
    )

    op.add_column(
        "consultations",
        sa.Column(
            "record_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )

    op.add_column(
        "consultations",
        sa.Column(
            "finalized_by_staff_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "consultations",
        sa.Column(
            "finalized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "consultations",
        sa.Column(
            "latest_amendment_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_consultations_finalized_by_staff_id_staff",
        "consultations",
        "staff",
        ["finalized_by_staff_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_consultations_latest_amendment_id_clinical_record_amendments",
        "consultations",
        "clinical_record_amendments",
        ["latest_amendment_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_consultations_record_status",
        "consultations",
        "record_status IN ('draft', 'final', 'amended')",
    )

    op.create_check_constraint(
        "ck_consultations_record_version_positive",
        "consultations",
        "record_version >= 1",
    )

    op.create_check_constraint(
        "ck_consultations_record_finalization_consistency",
        "consultations",
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
    )

    op.create_index(
        "ix_consultations_clinic_record_status",
        "consultations",
        ["clinic_id", "record_status"],
    )

    op.create_index(
        "ix_consultations_finalized_by_staff_id",
        "consultations",
        ["finalized_by_staff_id"],
    )

    op.create_index(
        "ix_consultations_latest_amendment_id",
        "consultations",
        ["latest_amendment_id"],
    )


def downgrade():
    op.drop_index(
        "ix_consultations_latest_amendment_id",
        table_name="consultations",
    )

    op.drop_index(
        "ix_consultations_finalized_by_staff_id",
        table_name="consultations",
    )

    op.drop_index(
        "ix_consultations_clinic_record_status",
        table_name="consultations",
    )

    op.drop_constraint(
        "ck_consultations_record_finalization_consistency",
        "consultations",
        type_="check",
    )

    op.drop_constraint(
        "ck_consultations_record_version_positive",
        "consultations",
        type_="check",
    )

    op.drop_constraint(
        "ck_consultations_record_status",
        "consultations",
        type_="check",
    )

    op.drop_constraint(
        "fk_consultations_latest_amendment_id_clinical_record_amendments",
        "consultations",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_consultations_finalized_by_staff_id_staff",
        "consultations",
        type_="foreignkey",
    )

    op.drop_column(
        "consultations",
        "latest_amendment_id",
    )

    op.drop_column(
        "consultations",
        "finalized_at",
    )

    op.drop_column(
        "consultations",
        "finalized_by_staff_id",
    )

    op.drop_column(
        "consultations",
        "record_version",
    )

    op.drop_column(
        "consultations",
        "record_status",
    )
