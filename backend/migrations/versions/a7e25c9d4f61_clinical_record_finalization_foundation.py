"""add clinical record finalization foundation

Revision ID: a7e25c9d4f61
Revises: f6d14b8c2e35
"""

from alembic import op
import sqlalchemy as sa


revision = "a7e25c9d4f61"
down_revision = "f6d14b8c2e35"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "clinical_record_amendments",
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
            "source_record_type",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "source_record_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "previous_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "new_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "previous_amendment_id",
            sa.Integer(),
            sa.ForeignKey(
                "clinical_record_amendments.id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column(
            "amended_by_staff_id",
            sa.Integer(),
            sa.ForeignKey("staff.id"),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "new_content",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "new_content_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "amended_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_record_type IN ("
            "'consultation_note', "
            "'nursing_note', "
            "'medication_administration', "
            "'medication_reconciliation', "
            "'discharge_summary'"
            ")",
            name="ck_clinical_record_amendments_type",
        ),
        sa.CheckConstraint(
            "source_record_id > 0",
            name="ck_clinical_record_amendments_source_id_positive",
        ),
        sa.CheckConstraint(
            "previous_version >= 1",
            name="ck_clinical_record_amendments_previous_version_positive",
        ),
        sa.CheckConstraint(
            "new_version = previous_version + 1",
            name="ck_clinical_record_amendments_version_contiguous",
        ),
        sa.CheckConstraint(
            "length(reason) > 0",
            name="ck_clinical_record_amendments_reason_nonempty",
        ),
        sa.CheckConstraint(
            "length(new_content_sha256) = 64",
            name="ck_clinical_record_amendments_sha256_length",
        ),
        sa.UniqueConstraint(
            "clinic_id",
            "source_record_type",
            "source_record_id",
            "new_version",
            name="uq_clinical_record_amendments_source_version",
        ),
    )

    for column in (
        "clinic_id",
        "patient_id",
        "source_record_type",
        "source_record_id",
        "previous_amendment_id",
        "amended_by_staff_id",
        "amended_at",
    ):
        op.create_index(
            f"ix_clinical_record_amendments_{column}",
            "clinical_record_amendments",
            [column],
        )

    op.create_index(
        "ix_clinical_record_amendments_source_version",
        "clinical_record_amendments",
        [
            "clinic_id",
            "source_record_type",
            "source_record_id",
            "new_version",
        ],
    )

    op.create_index(
        "ix_clinical_record_amendments_patient_amended",
        "clinical_record_amendments",
        [
            "clinic_id",
            "patient_id",
            "amended_at",
            "id",
        ],
    )


def downgrade():
    op.drop_table(
        "clinical_record_amendments"
    )
