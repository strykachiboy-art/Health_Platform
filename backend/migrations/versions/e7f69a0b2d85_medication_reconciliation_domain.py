"""add medication reconciliation clinical record domain

Revision ID: e7f69a0b2d85
Revises: d6e58f9a1c74
"""

from alembic import op
import sqlalchemy as sa


revision = "e7f69a0b2d85"
down_revision = "d6e58f9a1c74"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "medication_reconciliations",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "clinic_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "admission_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "ward_transfer_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "event_type",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "event_identity",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column(
            "created_by_staff_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "no_medications_reported",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "notes",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "record_status",
            sa.String(length=16),
            server_default="draft",
            nullable=False,
        ),
        sa.Column(
            "record_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "finalized_by_staff_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "finalized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "latest_amendment_id",
            sa.Integer(),
            nullable=True,
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
        sa.ForeignKeyConstraint(
            ["clinic_id"],
            ["clinics.id"],
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
        ),
        sa.ForeignKeyConstraint(
            ["admission_id"],
            ["admissions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ward_transfer_id"],
            ["ward_transfers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_staff_id"],
            ["staff.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["finalized_by_staff_id"],
            ["staff.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["latest_amendment_id"],
            ["clinical_record_amendments.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "event_type IN ('admission', 'transfer', 'discharge')",
            name="ck_med_recon_event_type",
        ),
        sa.CheckConstraint(
            "record_status IN ('draft', 'final', 'amended')",
            name="ck_med_recon_record_status",
        ),
        sa.CheckConstraint(
            "record_version >= 1",
            name="ck_med_recon_record_version_positive",
        ),
        sa.CheckConstraint(
            "("
            "event_type = 'transfer' AND ward_transfer_id IS NOT NULL"
            ") OR ("
            "event_type != 'transfer' AND ward_transfer_id IS NULL"
            ")",
            name="ck_med_recon_transfer_reference",
        ),
        sa.CheckConstraint(
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
            name="ck_med_recon_record_linkage",
        ),
        sa.UniqueConstraint(
            "clinic_id",
            "event_identity",
            name="uq_med_recon_clinic_event_identity",
        ),
    )

    op.create_index(
        "ix_med_recon_admission_event_created",
        "medication_reconciliations",
        [
            "admission_id",
            "event_type",
            "created_at",
            "id",
        ],
    )
    op.create_index(
        "ix_med_recon_clinic_patient_created",
        "medication_reconciliations",
        [
            "clinic_id",
            "patient_id",
            "created_at",
            "id",
        ],
    )
    op.create_index(
        "ix_med_recon_clinic_record_status",
        "medication_reconciliations",
        [
            "clinic_id",
            "record_status",
        ],
    )
    op.create_index(
        "ix_medication_reconciliations_clinic_id",
        "medication_reconciliations",
        ["clinic_id"],
    )
    op.create_index(
        "ix_medication_reconciliations_patient_id",
        "medication_reconciliations",
        ["patient_id"],
    )
    op.create_index(
        "ix_medication_reconciliations_admission_id",
        "medication_reconciliations",
        ["admission_id"],
    )
    op.create_index(
        "ix_medication_reconciliations_ward_transfer_id",
        "medication_reconciliations",
        ["ward_transfer_id"],
    )
    op.create_index(
        "ix_medication_reconciliations_event_type",
        "medication_reconciliations",
        ["event_type"],
    )
    op.create_index(
        "ix_medication_reconciliations_created_by_staff_id",
        "medication_reconciliations",
        ["created_by_staff_id"],
    )
    op.create_index(
        "ix_medication_reconciliations_record_status",
        "medication_reconciliations",
        ["record_status"],
    )
    op.create_index(
        "ix_medication_reconciliations_finalized_by_staff_id",
        "medication_reconciliations",
        ["finalized_by_staff_id"],
    )
    op.create_index(
        "ix_medication_reconciliations_latest_amendment_id",
        "medication_reconciliations",
        ["latest_amendment_id"],
    )
    op.create_index(
        "ix_medication_reconciliations_created_at",
        "medication_reconciliations",
        ["created_at"],
    )

    op.create_table(
        "medication_reconciliation_items",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
        ),
        sa.Column(
            "reconciliation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "source_prescription_item_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "medication_name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "decision",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "replacement_medication_name",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "dose_text",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "route_text",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column(
            "frequency_text",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "instructions",
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            "rationale",
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["reconciliation_id"],
            ["medication_reconciliations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_prescription_item_id"],
            ["prescription_items.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "decision IN ("
            "'continue', 'hold', 'stop', 'replace', "
            "'new', 'verify', 'unknown'"
            ")",
            name="ck_med_recon_item_decision",
        ),
        sa.CheckConstraint(
            "("
            "decision = 'replace' "
            "AND replacement_medication_name IS NOT NULL"
            ") OR ("
            "decision != 'replace' "
            "AND replacement_medication_name IS NULL"
            ")",
            name="ck_med_recon_item_replacement",
        ),
    )

    op.create_index(
        "ix_med_recon_items_reconciliation_decision",
        "medication_reconciliation_items",
        [
            "reconciliation_id",
            "decision",
            "id",
        ],
    )
    op.create_index(
        "ix_med_recon_items_source_item",
        "medication_reconciliation_items",
        ["source_prescription_item_id"],
    )
    op.create_index(
        "ix_medication_reconciliation_items_reconciliation_id",
        "medication_reconciliation_items",
        ["reconciliation_id"],
    )
    op.create_index(
        "ix_medication_reconciliation_items_source_prescription_item_id",
        "medication_reconciliation_items",
        ["source_prescription_item_id"],
    )
    op.create_index(
        "ix_medication_reconciliation_items_decision",
        "medication_reconciliation_items",
        ["decision"],
    )


def downgrade():
    op.drop_table(
        "medication_reconciliation_items"
    )
    op.drop_table(
        "medication_reconciliations"
    )
