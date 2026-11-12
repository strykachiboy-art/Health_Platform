"""CE1-S05 pre-anesthesia assessment and preoperative baseline evidence.

Revision ID: e5a9c1d04b83
Revises: d4e6f8a31c72

Source only; not applied to the application database by S05.
"""
from alembic import op
import sqlalchemy as sa

revision = "e5a9c1d04b83"
down_revision = "d4e6f8a31c72"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "anesthesia_preoperative_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("surgical_case_id", sa.Integer(), sa.ForeignKey("surgical_cases.id"), nullable=False),
        sa.Column("procedure_order_id", sa.Integer(), sa.ForeignKey("procedure_orders.id"), nullable=False),
        sa.Column("procedure_order_version", sa.Integer(), nullable=False),
        sa.Column("theatre_booking_id", sa.Integer(), sa.ForeignKey("theatre_bookings.id"), nullable=False),
        sa.Column("booking_version", sa.Integer(), nullable=False),
        sa.Column("surgical_consent_id", sa.Integer(), sa.ForeignKey("surgical_consent_evidence.id"), nullable=False),
        sa.Column("surgeon_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("anesthetist_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("asa_class", sa.Integer(), nullable=False),
        sa.Column("technique", sa.String(length=16), nullable=False),
        sa.Column("airway_assessment", sa.String(length=32), nullable=False),
        sa.Column("airway_plan", sa.String(length=1000), nullable=False),
        sa.Column("allergy_status", sa.String(length=16), nullable=False),
        sa.Column("allergy_notes", sa.String(length=1000), nullable=True),
        sa.Column("fasting_confirmed", sa.Boolean(), nullable=False),
        sa.Column("fasting_notes", sa.String(length=1000), nullable=False),
        sa.Column("risk_and_comorbidity_notes", sa.String(length=4000), nullable=False),
        sa.Column("plan_summary", sa.String(length=4000), nullable=False),
        sa.Column("monitoring_plan", sa.String(length=2000), nullable=False),
        sa.Column("contingency_plan", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("attested_at", sa.DateTime(), nullable=True),
        sa.Column("attested_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("surgical_case_id", name="uq_anesthesia_assessment_case"),
        sa.CheckConstraint("asa_class BETWEEN 1 AND 6", name="ck_anesthesia_assessment_asa"),
        sa.CheckConstraint("technique IN ('general','regional','local','sedation')",
                           name="ck_anesthesia_assessment_technique"),
        sa.CheckConstraint("airway_assessment IN ('routine','anticipated_difficult','unknown')",
                           name="ck_anesthesia_assessment_airway"),
        sa.CheckConstraint("allergy_status IN ('known','none_known','unknown')",
                           name="ck_anesthesia_assessment_allergy"),
        sa.CheckConstraint("status IN ('draft','attested')", name="ck_anesthesia_assessment_status"),
        sa.CheckConstraint("version >= 1 AND procedure_order_version >= 1 AND booking_version >= 1",
                           name="ck_anesthesia_assessment_versions"),
        sa.CheckConstraint(
            "status != 'attested' OR (attested_at IS NOT NULL AND "
            "attested_by_staff_id IS NOT NULL AND evidence_sha256 IS NOT NULL AND "
            "length(evidence_sha256) = 64)",
            name="ck_anesthesia_assessment_attestation_evidence",
        ),
    )
    op.create_index(
        "ix_anesthesia_preoperative_assessments_clinic_id",
        "anesthesia_preoperative_assessments", ["clinic_id"],
    )
    op.create_index("ix_anesthesia_assessment_clinic_case",
                    "anesthesia_preoperative_assessments", ["clinic_id", "surgical_case_id"])

    op.create_table(
        "anesthesia_baseline_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("assessment_id", sa.Integer(),
                  sa.ForeignKey("anesthesia_preoperative_assessments.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("observed_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("heart_rate_bpm", sa.Integer(), nullable=False),
        sa.Column("systolic_bp", sa.Integer(), nullable=False),
        sa.Column("diastolic_bp", sa.Integer(), nullable=False),
        sa.Column("spo2_percent", sa.Integer(), nullable=False),
        sa.Column("respiratory_rate", sa.Integer(), nullable=False),
        sa.Column("temperature_tenths_c", sa.Integer(), nullable=False),
        sa.Column("oxygen_delivery", sa.String(length=16), nullable=False),
        sa.Column("observation_notes", sa.String(length=1000), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("assessment_id", "sequence", name="uq_anesthesia_baseline_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_anesthesia_baseline_sequence"),
        sa.CheckConstraint("heart_rate_bpm BETWEEN 1 AND 300", name="ck_anesthesia_baseline_heart_rate"),
        sa.CheckConstraint("systolic_bp BETWEEN 1 AND 350 AND diastolic_bp BETWEEN 1 AND 250 "
                           "AND systolic_bp >= diastolic_bp", name="ck_anesthesia_baseline_bp"),
        sa.CheckConstraint("spo2_percent BETWEEN 0 AND 100", name="ck_anesthesia_baseline_spo2"),
        sa.CheckConstraint("respiratory_rate BETWEEN 1 AND 100", name="ck_anesthesia_baseline_resp"),
        sa.CheckConstraint("temperature_tenths_c BETWEEN 200 AND 450", name="ck_anesthesia_baseline_temp"),
        sa.CheckConstraint("oxygen_delivery IN ('ambient_air','supplemental')",
                           name="ck_anesthesia_baseline_oxygen"),
        sa.CheckConstraint("length(evidence_sha256) = 64", name="ck_anesthesia_baseline_digest"),
    )
    for column in ("clinic_id", "assessment_id"):
        op.create_index(f"ix_anesthesia_baseline_observations_{column}",
                        "anesthesia_baseline_observations", [column])
    op.create_index("ix_anesthesia_baseline_clinic_assessment",
                    "anesthesia_baseline_observations", ["clinic_id", "assessment_id"])


def downgrade():
    op.drop_index("ix_anesthesia_baseline_clinic_assessment", table_name="anesthesia_baseline_observations")
    for col in ("assessment_id", "clinic_id"):
        op.drop_index(f"ix_anesthesia_baseline_observations_{col}",
                      table_name="anesthesia_baseline_observations")
    op.drop_table("anesthesia_baseline_observations")
    op.drop_index("ix_anesthesia_assessment_clinic_case", table_name="anesthesia_preoperative_assessments")
    op.drop_index("ix_anesthesia_preoperative_assessments_clinic_id",
                  table_name="anesthesia_preoperative_assessments")
    op.drop_table("anesthesia_preoperative_assessments")
