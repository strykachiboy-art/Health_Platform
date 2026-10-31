"""integrate medication administration with clinical record finalization

Revision ID: d6e58f9a1c74
Revises: c5d47e8f0b63
"""

from alembic import op
import sqlalchemy as sa


revision = "d6e58f9a1c74"
down_revision = "c5d47e8f0b63"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "medication_administrations",
        sa.Column(
            "record_status",
            sa.String(length=16),
            server_default="draft",
            nullable=False,
        ),
    )
    op.add_column(
        "medication_administrations",
        sa.Column(
            "record_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "medication_administrations",
        sa.Column(
            "finalized_by_staff_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "medication_administrations",
        sa.Column(
            "finalized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "medication_administrations",
        sa.Column(
            "latest_amendment_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    # Historical terminal rows are final clinical records.  For older
    # MISSED/CANCELLED rows the original schema did not persist the actor;
    # do not fabricate one during backfill.
    op.execute(
        sa.text(
            """
            UPDATE medication_administrations
            SET
                record_status = 'final',
                record_version = 1,
                finalized_by_staff_id = CASE
                    WHEN status = 'administered'
                        THEN administered_by_staff_id
                    WHEN status = 'held'
                        THEN held_by_staff_id
                    WHEN status = 'refused'
                        THEN refused_recorded_by_staff_id
                    ELSE NULL
                END,
                finalized_at = CASE
                    WHEN status = 'administered'
                        THEN administered_at
                    WHEN status = 'held'
                        THEN held_at
                    WHEN status = 'refused'
                        THEN refused_at
                    WHEN status = 'missed'
                        THEN missed_at
                    WHEN status = 'cancelled'
                        THEN cancelled_at
                    ELSE updated_at
                END
            WHERE status IN (
                'administered',
                'held',
                'refused',
                'missed',
                'cancelled'
            )
            """
        )
    )

    op.create_foreign_key(
        "fk_med_admin_finalized_by_staff",
        "medication_administrations",
        "staff",
        ["finalized_by_staff_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_med_admin_latest_amendment",
        "medication_administrations",
        "clinical_record_amendments",
        ["latest_amendment_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_med_admin_record_status",
        "medication_administrations",
        "record_status IN ('draft', 'final', 'amended')",
    )
    op.create_check_constraint(
        "ck_med_admin_record_version_positive",
        "medication_administrations",
        "record_version >= 1",
    )
    op.create_check_constraint(
        "ck_med_admin_record_linkage",
        "medication_administrations",
        "("
        "record_status = 'draft' "
        "AND record_version = 1 "
        "AND finalized_at IS NULL "
        "AND latest_amendment_id IS NULL"
        ") OR ("
        "record_status = 'final' "
        "AND record_version = 1 "
        "AND finalized_at IS NOT NULL "
        "AND latest_amendment_id IS NULL"
        ") OR ("
        "record_status = 'amended' "
        "AND record_version >= 2 "
        "AND finalized_at IS NOT NULL "
        "AND latest_amendment_id IS NOT NULL"
        ")",
    )

    op.create_index(
        "ix_med_admin_clinic_record_status",
        "medication_administrations",
        ["clinic_id", "record_status"],
    )
    op.create_index(
        "ix_med_admin_finalized_by_staff_id",
        "medication_administrations",
        ["finalized_by_staff_id"],
    )
    op.create_index(
        "ix_med_admin_latest_amendment_id",
        "medication_administrations",
        ["latest_amendment_id"],
    )


def downgrade():
    op.drop_index(
        "ix_med_admin_latest_amendment_id",
        table_name="medication_administrations",
    )
    op.drop_index(
        "ix_med_admin_finalized_by_staff_id",
        table_name="medication_administrations",
    )
    op.drop_index(
        "ix_med_admin_clinic_record_status",
        table_name="medication_administrations",
    )

    op.drop_constraint(
        "ck_med_admin_record_linkage",
        "medication_administrations",
        type_="check",
    )
    op.drop_constraint(
        "ck_med_admin_record_version_positive",
        "medication_administrations",
        type_="check",
    )
    op.drop_constraint(
        "ck_med_admin_record_status",
        "medication_administrations",
        type_="check",
    )
    op.drop_constraint(
        "fk_med_admin_latest_amendment",
        "medication_administrations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_med_admin_finalized_by_staff",
        "medication_administrations",
        type_="foreignkey",
    )

    op.drop_column(
        "medication_administrations",
        "latest_amendment_id",
    )
    op.drop_column(
        "medication_administrations",
        "finalized_at",
    )
    op.drop_column(
        "medication_administrations",
        "finalized_by_staff_id",
    )
    op.drop_column(
        "medication_administrations",
        "record_version",
    )
    op.drop_column(
        "medication_administrations",
        "record_status",
    )
