"""integrate nursing notes with clinical record finalization

Revision ID: c5d47e8f0b63
Revises: b4c36d7e9a52
"""

from alembic import op
import sqlalchemy as sa


revision = "c5d47e8f0b63"
down_revision = "b4c36d7e9a52"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "nursing_notes",
        sa.Column(
            "record_status",
            sa.String(length=16),
            server_default="draft",
            nullable=False,
        ),
    )

    op.add_column(
        "nursing_notes",
        sa.Column(
            "record_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )

    op.add_column(
        "nursing_notes",
        sa.Column(
            "finalized_by_staff_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.add_column(
        "nursing_notes",
        sa.Column(
            "latest_amendment_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE nursing_notes
            SET
                record_status = 'final',
                record_version = 1,
                finalized_by_staff_id = author_staff_id
            WHERE status = 'SIGNED'
            """
        )
    )

    op.create_foreign_key(
        "fk_nursing_notes_finalized_by_staff_id_staff",
        "nursing_notes",
        "staff",
        ["finalized_by_staff_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_foreign_key(
        "fk_nursing_notes_latest_amendment_id_clinical_record_amendments",
        "nursing_notes",
        "clinical_record_amendments",
        ["latest_amendment_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_nursing_notes_record_status",
        "nursing_notes",
        "record_status IN ('draft', 'final', 'amended')",
    )

    op.create_check_constraint(
        "ck_nursing_notes_record_version_positive",
        "nursing_notes",
        "record_version >= 1",
    )

    op.create_check_constraint(
        "ck_nursing_notes_record_finalization_consistency",
        "nursing_notes",
        "("
        "record_status = 'draft' "
        "AND status = 'DRAFT' "
        "AND record_version = 1 "
        "AND finalized_by_staff_id IS NULL "
        "AND signed_at IS NULL "
        "AND latest_amendment_id IS NULL"
        ") OR ("
        "record_status = 'final' "
        "AND status = 'SIGNED' "
        "AND record_version = 1 "
        "AND finalized_by_staff_id IS NOT NULL "
        "AND signed_at IS NOT NULL "
        "AND latest_amendment_id IS NULL"
        ") OR ("
        "record_status = 'amended' "
        "AND status = 'SIGNED' "
        "AND record_version >= 2 "
        "AND finalized_by_staff_id IS NOT NULL "
        "AND signed_at IS NOT NULL "
        "AND latest_amendment_id IS NOT NULL"
        ")",
    )

    op.create_index(
        "ix_nursing_notes_clinic_record_status",
        "nursing_notes",
        ["clinic_id", "record_status"],
    )

    op.create_index(
        "ix_nursing_notes_finalized_by_staff_id",
        "nursing_notes",
        ["finalized_by_staff_id"],
    )

    op.create_index(
        "ix_nursing_notes_latest_amendment_id",
        "nursing_notes",
        ["latest_amendment_id"],
    )


def downgrade():
    op.drop_index(
        "ix_nursing_notes_latest_amendment_id",
        table_name="nursing_notes",
    )

    op.drop_index(
        "ix_nursing_notes_finalized_by_staff_id",
        table_name="nursing_notes",
    )

    op.drop_index(
        "ix_nursing_notes_clinic_record_status",
        table_name="nursing_notes",
    )

    op.drop_constraint(
        "ck_nursing_notes_record_finalization_consistency",
        "nursing_notes",
        type_="check",
    )

    op.drop_constraint(
        "ck_nursing_notes_record_version_positive",
        "nursing_notes",
        type_="check",
    )

    op.drop_constraint(
        "ck_nursing_notes_record_status",
        "nursing_notes",
        type_="check",
    )

    op.drop_constraint(
        "fk_nursing_notes_latest_amendment_id_clinical_record_amendments",
        "nursing_notes",
        type_="foreignkey",
    )

    op.drop_constraint(
        "fk_nursing_notes_finalized_by_staff_id_staff",
        "nursing_notes",
        type_="foreignkey",
    )

    op.drop_column(
        "nursing_notes",
        "latest_amendment_id",
    )

    op.drop_column(
        "nursing_notes",
        "finalized_by_staff_id",
    )

    op.drop_column(
        "nursing_notes",
        "record_version",
    )

    op.drop_column(
        "nursing_notes",
        "record_status",
    )
