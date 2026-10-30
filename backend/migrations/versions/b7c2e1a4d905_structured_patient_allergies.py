"""Add structured patient allergy registry.

Revision ID: b7c2e1a4d905
Revises: f3a1c7d9e204
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c2e1a4d905"
down_revision = "f3a1c7d9e204"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "patient_allergies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column(
            "allergen_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "allergen_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "allergen_type",
            sa.String(length=32),
            nullable=False,
            server_default="other",
        ),
        sa.Column(
            "reaction",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "severity",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "clinical_status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "verification_status",
            sa.String(length=16),
            nullable=False,
            server_default="unconfirmed",
        ),
        sa.Column(
            "onset_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "recorded_by_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "notes",
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(trim(allergen_name)) > 0",
            name="ck_patient_allergies_name_nonempty",
        ),
        sa.CheckConstraint(
            "allergen_type IN "
            "('drug', 'food', 'environmental', "
            "'latex', 'biologic', 'other')",
            name="ck_patient_allergies_type",
        ),
        sa.CheckConstraint(
            "severity IN "
            "('unknown', 'mild', 'moderate', "
            "'severe', 'life_threatening')",
            name="ck_patient_allergies_severity",
        ),
        sa.CheckConstraint(
            "clinical_status IN "
            "('active', 'inactive', 'resolved')",
            name="ck_patient_allergies_clinical_status",
        ),
        sa.CheckConstraint(
            "verification_status IN "
            "('unconfirmed', 'confirmed', 'refuted')",
            name="ck_patient_allergies_verification_status",
        ),
        sa.ForeignKeyConstraint(
            ["clinic_id"],
            ["clinics.id"],
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_id"],
            ["staff.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    index_specs = (
        (
            "ix_patient_allergies_clinic_id",
            ["clinic_id"],
        ),
        (
            "ix_patient_allergies_patient_id",
            ["patient_id"],
        ),
        (
            "ix_patient_allergies_allergen_code",
            ["allergen_code"],
        ),
        (
            "ix_patient_allergies_clinical_status",
            ["clinical_status"],
        ),
        (
            "ix_patient_allergies_verification_status",
            ["verification_status"],
        ),
        (
            "ix_patient_allergies_recorded_at",
            ["recorded_at"],
        ),
        (
            "ix_patient_allergies_recorded_by_id",
            ["recorded_by_id"],
        ),
        (
            "ix_patient_allergies_clinic_patient_status",
            [
                "clinic_id",
                "patient_id",
                "clinical_status",
                "verification_status",
                "id",
            ],
        ),
        (
            "ix_patient_allergies_patient_allergen",
            [
                "patient_id",
                "allergen_name",
                "allergen_code",
                "id",
            ],
        ),
    )

    for name, columns in index_specs:
        op.create_index(
            name,
            "patient_allergies",
            columns,
            unique=False,
        )


def downgrade():
    for index_name in (
        "ix_patient_allergies_patient_allergen",
        "ix_patient_allergies_clinic_patient_status",
        "ix_patient_allergies_recorded_by_id",
        "ix_patient_allergies_recorded_at",
        "ix_patient_allergies_verification_status",
        "ix_patient_allergies_clinical_status",
        "ix_patient_allergies_allergen_code",
        "ix_patient_allergies_patient_id",
        "ix_patient_allergies_clinic_id",
    ):
        op.drop_index(
            index_name,
            table_name="patient_allergies",
        )

    op.drop_table("patient_allergies")
