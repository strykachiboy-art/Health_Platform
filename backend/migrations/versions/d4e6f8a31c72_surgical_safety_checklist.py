"""CE1-S04 WHO-inspired surgical checklist attestation evidence.

Revision ID: d4e6f8a31c72
Revises: c3f7a2e91b64

Source-only. No migration on the application PostgreSQL database in S04.
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e6f8a31c72"
down_revision = "c3f7a2e91b64"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "surgical_safety_checklist_attestations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("admission_id", sa.Integer(), sa.ForeignKey("admissions.id"), nullable=False),
        sa.Column("surgical_case_id", sa.Integer(), sa.ForeignKey("surgical_cases.id"), nullable=False),
        sa.Column("procedure_order_id", sa.Integer(), sa.ForeignKey("procedure_orders.id"), nullable=False),
        sa.Column("procedure_order_version", sa.Integer(), nullable=False),
        sa.Column("theatre_booking_id", sa.Integer(), sa.ForeignKey("theatre_bookings.id"), nullable=False),
        sa.Column("booking_version", sa.Integer(), nullable=False),
        sa.Column("surgical_consent_id", sa.Integer(), sa.ForeignKey("surgical_consent_evidence.id"), nullable=False),
        sa.Column("surgeon_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("anesthetist_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("attested_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("attested_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("surgical_case_id", "phase", name="uq_surgical_checklist_case_phase"),
        sa.CheckConstraint(
            "phase IN ('sign_in', 'time_out', 'sign_out')", name="ck_surgical_checklist_phase"
        ),
        sa.CheckConstraint("length(evidence_sha256) = 64", name="ck_surgical_checklist_evidence_digest"),
        sa.CheckConstraint("procedure_order_version >= 1 AND booking_version >= 1",
                           name="ck_surgical_checklist_source_versions"),
        sa.CheckConstraint("surgeon_staff_id != anesthetist_staff_id",
                           name="ck_surgical_checklist_distinct_professionals"),
    )
    op.create_index(
        "ix_surgical_safety_checklist_attestations_clinic_id",
        "surgical_safety_checklist_attestations", ["clinic_id"],
    )
    op.create_index(
        "ix_surgical_checklist_clinic_case_phase", "surgical_safety_checklist_attestations",
        ["clinic_id", "surgical_case_id", "phase"],
    )


def downgrade():
    op.drop_index("ix_surgical_checklist_clinic_case_phase",
                  table_name="surgical_safety_checklist_attestations")
    op.drop_index("ix_surgical_safety_checklist_attestations_clinic_id",
                  table_name="surgical_safety_checklist_attestations")
    op.drop_table("surgical_safety_checklist_attestations")
