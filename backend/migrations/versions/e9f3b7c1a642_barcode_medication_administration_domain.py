"""add complete barcode medication administration domain

Revision ID: e9f3b7c1a642
Revises: d4e7c1a9f620
Create Date: 2026-10-30
"""

from alembic import op
import sqlalchemy as sa


revision = "e9f3b7c1a642"
down_revision = "d4e7c1a9f620"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "patients",
        sa.Column("barcode", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_patients_clinic_barcode",
        "patients",
        ["clinic_id", "barcode"],
    )
    op.create_index(
        "ix_patients_clinic_barcode",
        "patients",
        ["clinic_id", "barcode"],
        unique=False,
    )

    for column in (
        sa.Column("patient_barcode", sa.String(length=128), nullable=True),
        sa.Column("medication_barcode", sa.String(length=128), nullable=True),
        sa.Column("scan_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_device", sa.String(length=255), nullable=True),
        sa.Column("verification_result", sa.String(length=32), nullable=True),
        sa.Column("barcode_verified_by_staff_id", sa.Integer(), nullable=True),
        sa.Column("barcode_verification_event_id", sa.Integer(), nullable=True),
    ):
        op.add_column("medication_administrations", column)

    op.create_foreign_key(
        "fk_med_admin_barcode_verified_by_staff",
        "medication_administrations",
        "staff",
        ["barcode_verified_by_staff_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_med_admin_barcode_verification_result",
        "medication_administrations",
        "verification_result IS NULL OR verification_result IN ('barcode_verified', 'not_barcode_verified')",
    )
    op.create_check_constraint(
        "ck_med_admin_barcode_verification_evidence",
        "medication_administrations",
        (
            "verification_result IS NULL OR (patient_barcode IS NOT NULL "
            "AND medication_barcode IS NOT NULL AND scan_timestamp IS NOT NULL "
            "AND scan_device IS NOT NULL AND barcode_verified_by_staff_id IS NOT NULL)"
        ),
    )
    for name, columns in (
        ("ix_medication_administrations_scan_timestamp", ["scan_timestamp"]),
        ("ix_medication_administrations_verification_result", ["verification_result"]),
        ("ix_medication_administrations_barcode_verified_by_staff_id", ["barcode_verified_by_staff_id"]),
        ("ix_med_admin_barcode_verification", ["clinic_id", "verification_result", "scan_timestamp", "id"]),
    ):
        op.create_index(name, "medication_administrations", columns, unique=False)

    op.create_table(
        "medication_administration_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=True),
        sa.Column("admission_id", sa.Integer(), nullable=True),
        sa.Column("medication_administration_id", sa.Integer(), nullable=True),
        sa.Column("prescription_id", sa.Integer(), nullable=True),
        sa.Column("prescription_item_id", sa.Integer(), nullable=True),
        sa.Column("actor_staff_id", sa.Integer(), nullable=False),
        sa.Column("scanned_patient_id", sa.Integer(), nullable=True),
        sa.Column("scanned_drug_id", sa.Integer(), nullable=True),
        sa.Column("patient_barcode", sa.Text(), nullable=False),
        sa.Column("medication_barcode", sa.Text(), nullable=False),
        sa.Column("scan_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scan_device", sa.String(length=255), nullable=False),
        sa.Column("verification_result", sa.String(length=32), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("patient_match", sa.Boolean(), nullable=False),
        sa.Column("medication_match", sa.Boolean(), nullable=False),
        sa.Column("dose_match", sa.Boolean(), nullable=False),
        sa.Column("route_match", sa.Boolean(), nullable=False),
        sa.Column("time_window_match", sa.Boolean(), nullable=False),
        sa.Column("expected_drug_id", sa.Integer(), nullable=True),
        sa.Column("expected_dose_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("expected_dose_unit", sa.String(length=50), nullable=True),
        sa.Column("expected_route_code", sa.String(length=100), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_order_version", sa.Integer(), nullable=True),
        sa.Column("verification_policy_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("operation_identity", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("expected_dose_value > 0", name="ck_med_admin_event_expected_dose_positive"),
        sa.CheckConstraint("source_order_version > 0", name="ck_med_admin_event_source_order_version_positive"),
        sa.CheckConstraint("verification_policy_version > 0", name="ck_med_admin_event_policy_version_positive"),
        sa.CheckConstraint("verification_result IN ('barcode_verified', 'not_barcode_verified')", name="ck_med_admin_event_verification_result"),
        sa.CheckConstraint(
            "verification_result != 'barcode_verified' OR (patient_match AND medication_match AND dose_match AND route_match AND time_window_match AND failure_code IS NULL AND failure_reason IS NULL)",
            name="ck_med_admin_event_verified_consistency",
        ),
        sa.CheckConstraint(
            (
                "verification_result != 'barcode_verified' OR "
                "(patient_id IS NOT NULL "
                "AND admission_id IS NOT NULL "
                "AND medication_administration_id IS NOT NULL "
                "AND prescription_id IS NOT NULL "
                "AND prescription_item_id IS NOT NULL "
                "AND scanned_patient_id IS NOT NULL "
                "AND scanned_drug_id IS NOT NULL "
                "AND expected_drug_id IS NOT NULL "
                "AND expected_dose_value IS NOT NULL "
                "AND expected_dose_unit IS NOT NULL "
                "AND expected_route_code IS NOT NULL "
                "AND source_order_version IS NOT NULL)"
            ),
            name="ck_med_admin_event_verified_context",
        ),
        sa.CheckConstraint(
            (
                "verification_result != 'not_barcode_verified' OR "
                "(failure_code IS NOT NULL AND failure_reason IS NOT NULL)"
            ),
            name="ck_med_admin_event_failed_consistency",
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["admission_id"], ["admissions.id"]),
        sa.ForeignKeyConstraint(["medication_administration_id"], ["medication_administrations.id"]),
        sa.ForeignKeyConstraint(["prescription_id"], ["prescriptions.id"]),
        sa.ForeignKeyConstraint(["prescription_item_id"], ["prescription_items.id"]),
        sa.ForeignKeyConstraint(["actor_staff_id"], ["staff.id"]),
        sa.ForeignKeyConstraint(["scanned_patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["scanned_drug_id"], ["drugs.id"]),
        sa.ForeignKeyConstraint(["expected_drug_id"], ["drugs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "operation_identity", name="uq_med_admin_event_clinic_operation"),
    )

    for name, columns in (
        ("ix_med_admin_event_clinic_id", ["clinic_id"]),
        ("ix_med_admin_event_patient_id", ["patient_id"]),
        ("ix_med_admin_event_admission_id", ["admission_id"]),
        ("ix_med_admin_event_medication_administration_id", ["medication_administration_id"]),
        ("ix_med_admin_event_prescription_id", ["prescription_id"]),
        ("ix_med_admin_event_prescription_item_id", ["prescription_item_id"]),
        ("ix_med_admin_event_actor_staff_id", ["actor_staff_id"]),
        ("ix_med_admin_event_scanned_patient_id", ["scanned_patient_id"]),
        ("ix_med_admin_event_scanned_drug_id", ["scanned_drug_id"]),
        ("ix_med_admin_event_scan_timestamp", ["scan_timestamp"]),
        ("ix_med_admin_event_verification_result", ["verification_result"]),
        ("ix_med_admin_event_created_at", ["created_at"]),
        ("ix_med_admin_event_admin_scan", ["medication_administration_id", "scan_timestamp", "id"]),
        ("ix_med_admin_event_clinic_patient_scan", ["clinic_id", "patient_id", "scan_timestamp", "id"]),
        ("ix_med_admin_event_clinic_result_scan", ["clinic_id", "verification_result", "scan_timestamp", "id"]),
    ):
        op.create_index(name, "medication_administration_events", columns, unique=False)

    op.create_foreign_key(
        "fk_med_admin_barcode_verification_event",
        "medication_administrations",
        "medication_administration_events",
        ["barcode_verification_event_id"],
        ["id"],
    )
    op.create_index(
        "ix_medication_administrations_barcode_verification_event_id",
        "medication_administrations",
        ["barcode_verification_event_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_medication_administrations_barcode_verification_event_id",
        table_name="medication_administrations",
    )
    op.drop_constraint(
        "fk_med_admin_barcode_verification_event",
        "medication_administrations",
        type_="foreignkey",
    )
    op.drop_table("medication_administration_events")
    for name in (
        "ix_med_admin_barcode_verification",
        "ix_medication_administrations_barcode_verified_by_staff_id",
        "ix_medication_administrations_verification_result",
        "ix_medication_administrations_scan_timestamp",
    ):
        op.drop_index(name, table_name="medication_administrations")
    op.drop_constraint("ck_med_admin_barcode_verification_evidence", "medication_administrations", type_="check")
    op.drop_constraint("ck_med_admin_barcode_verification_result", "medication_administrations", type_="check")
    op.drop_constraint("fk_med_admin_barcode_verified_by_staff", "medication_administrations", type_="foreignkey")
    for column in (
        "barcode_verification_event_id",
        "barcode_verified_by_staff_id",
        "verification_result",
        "scan_device",
        "scan_timestamp",
        "medication_barcode",
        "patient_barcode",
    ):
        op.drop_column("medication_administrations", column)
    op.drop_index("ix_patients_clinic_barcode", table_name="patients")
    op.drop_constraint("uq_patients_clinic_barcode", "patients", type_="unique")
    op.drop_column("patients", "barcode")
