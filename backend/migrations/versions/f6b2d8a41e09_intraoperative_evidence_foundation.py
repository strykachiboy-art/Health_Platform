"""CE1-S06 append-only evidence journal, events and count attestations.

Revision ID: f6b2d8a41e09
Revises: e5a9c1d04b83

Source-only: do not apply to the application database in CE1-S06.
"""
from alembic import op
import sqlalchemy as sa

revision = "f6b2d8a41e09"
down_revision = "e5a9c1d04b83"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "surgical_evidence_journals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("surgical_case_id", sa.Integer(), sa.ForeignKey("surgical_cases.id"), nullable=False),
        sa.Column("procedure_order_id", sa.Integer(), sa.ForeignKey("procedure_orders.id"), nullable=False),
        sa.Column("procedure_order_version", sa.Integer(), nullable=False),
        sa.Column("theatre_booking_id", sa.Integer(), sa.ForeignKey("theatre_bookings.id"), nullable=False),
        sa.Column("booking_version", sa.Integer(), nullable=False),
        sa.Column("surgical_consent_id", sa.Integer(), sa.ForeignKey("surgical_consent_evidence.id"), nullable=False),
        sa.Column("sign_in_attestation_id", sa.Integer(), sa.ForeignKey("surgical_safety_checklist_attestations.id"), nullable=False),
        sa.Column("sign_in_sha256", sa.String(length=64), nullable=False),
        sa.Column("time_out_attestation_id", sa.Integer(), sa.ForeignKey("surgical_safety_checklist_attestations.id"), nullable=False),
        sa.Column("time_out_sha256", sa.String(length=64), nullable=False),
        sa.Column("anesthesia_assessment_id", sa.Integer(), sa.ForeignKey("anesthesia_preoperative_assessments.id"), nullable=False),
        sa.Column("anesthesia_sha256", sa.String(length=64), nullable=False),
        sa.Column("surgeon_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("anesthetist_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("counting_nurse_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("created_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sealed_at", sa.DateTime(), nullable=True),
        sa.Column("sealed_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("seal_summary", sa.String(length=4000), nullable=True),
        sa.Column("seal_sha256", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("surgical_case_id", name="uq_surgical_evidence_journal_case"),
        sa.CheckConstraint("status IN ('draft','sealed')", name="ck_surgical_evidence_journal_status"),
        sa.CheckConstraint("version >= 1 AND procedure_order_version >= 1 AND booking_version >= 1",
                           name="ck_surgical_evidence_journal_versions"),
        sa.CheckConstraint("length(source_sha256) = 64", name="ck_surgical_evidence_journal_source_digest"),
        sa.CheckConstraint(
            "status != 'sealed' OR (sealed_at IS NOT NULL AND sealed_by_staff_id IS NOT NULL "
            "AND seal_summary IS NOT NULL AND seal_sha256 IS NOT NULL AND length(seal_sha256) = 64)",
            name="ck_surgical_evidence_journal_seal_evidence",
        ),
    )
    op.create_index("ix_surgical_evidence_journals_clinic_id", "surgical_evidence_journals", ["clinic_id"])
    op.create_index("ix_surgical_evidence_journal_clinic_case", "surgical_evidence_journals", ["clinic_id", "surgical_case_id"])

    op.create_table(
        "surgical_evidence_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("journal_id", sa.Integer(), sa.ForeignKey("surgical_evidence_journals.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("actor_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("related_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(length=2000), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("journal_id", "sequence", name="uq_surgical_evidence_event_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_surgical_evidence_event_sequence"),
        sa.CheckConstraint("category IN ('team_joined','team_left','safety_observation','equipment_issue','clinical_note')",
                           name="ck_surgical_evidence_event_category"),
        sa.CheckConstraint("length(evidence_sha256) = 64", name="ck_surgical_evidence_event_digest"),
    )
    for column in ("clinic_id", "journal_id"):
        op.create_index(f"ix_surgical_evidence_events_{column}", "surgical_evidence_events", [column])
    op.create_index("ix_surgical_evidence_event_clinic_journal", "surgical_evidence_events", ["clinic_id", "journal_id"])

    op.create_table(
        "surgical_instrument_counts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("journal_id", sa.Integer(), sa.ForeignKey("surgical_evidence_journals.id"), nullable=False),
        sa.Column("item_kind", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("observed_count", sa.Integer(), nullable=False),
        sa.Column("counted_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("journal_id", "item_kind", "phase", name="uq_surgical_instrument_count_phase"),
        sa.CheckConstraint("item_kind IN ('instruments','sponges','sharps')", name="ck_surgical_instrument_count_kind"),
        sa.CheckConstraint("phase IN ('baseline','final')", name="ck_surgical_instrument_count_phase"),
        sa.CheckConstraint("observed_count >= 0", name="ck_surgical_instrument_count_nonnegative"),
        sa.CheckConstraint("length(evidence_sha256) = 64", name="ck_surgical_instrument_count_digest"),
    )
    for column in ("clinic_id", "journal_id"):
        op.create_index(f"ix_surgical_instrument_counts_{column}", "surgical_instrument_counts", [column])
    op.create_index("ix_surgical_instrument_count_clinic_journal", "surgical_instrument_counts", ["clinic_id", "journal_id"])


def downgrade():
    op.drop_index("ix_surgical_instrument_count_clinic_journal", table_name="surgical_instrument_counts")
    for column in ("journal_id", "clinic_id"):
        op.drop_index(f"ix_surgical_instrument_counts_{column}", table_name="surgical_instrument_counts")
    op.drop_table("surgical_instrument_counts")
    op.drop_index("ix_surgical_evidence_event_clinic_journal", table_name="surgical_evidence_events")
    for column in ("journal_id", "clinic_id"):
        op.drop_index(f"ix_surgical_evidence_events_{column}", table_name="surgical_evidence_events")
    op.drop_table("surgical_evidence_events")
    op.drop_index("ix_surgical_evidence_journal_clinic_case", table_name="surgical_evidence_journals")
    op.drop_index("ix_surgical_evidence_journals_clinic_id", table_name="surgical_evidence_journals")
    op.drop_table("surgical_evidence_journals")
