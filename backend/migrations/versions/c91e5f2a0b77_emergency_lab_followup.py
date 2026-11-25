"""P23D.E S03.B: link independently verified lab evidence to issued units.

Revision ID: c91e5f2a0b77
Revises: a82f6e1c9d30

Migration source only. No deployment or production migration authorized.
"""
from alembic import op
import sqlalchemy as sa

revision = "c91e5f2a0b77"
down_revision = "a82f6e1c9d30"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "blood_product_lab_followups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("admission_id", sa.Integer(), sa.ForeignKey("admissions.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("blood_product_orders.id"), nullable=False),
        sa.Column("issued_unit_id", sa.Integer(), sa.ForeignKey("blood_product_issued_units.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("lab_order_item_id", sa.Integer(), sa.ForeignKey("lab_order_items.id"), nullable=True),
        sa.Column("recorded_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_note", sa.Text(), nullable=True),
        sa.Column("escalation_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issued_unit_id", name="uq_blood_product_lab_followups_issued_unit"),
        sa.UniqueConstraint("lab_order_item_id", name="uq_blood_product_lab_followups_lab_item"),
        sa.CheckConstraint(
            "status IN ('pending', 'evidence_recorded', 'discrepancy_escalated')",
            name="ck_blood_product_lab_followups_status",
        ),
        sa.CheckConstraint(
            "status != 'pending' OR (lab_order_item_id IS NULL AND "
            "recorded_by_staff_id IS NULL AND recorded_at IS NULL AND "
            "evidence_note IS NULL AND escalation_note IS NULL)",
            name="ck_blood_product_lab_followups_pending_evidence",
        ),
        sa.CheckConstraint(
            "status = 'pending' OR (lab_order_item_id IS NOT NULL AND "
            "recorded_by_staff_id IS NOT NULL AND recorded_at IS NOT NULL AND "
            "evidence_note IS NOT NULL)",
            name="ck_blood_product_lab_followups_recorded_evidence",
        ),
        sa.CheckConstraint(
            "status != 'discrepancy_escalated' OR escalation_note IS NOT NULL",
            name="ck_blood_product_lab_followups_escalation_evidence",
        ),
    )
    op.create_index(
        "ix_blood_product_lab_followups_order_status",
        "blood_product_lab_followups", ["order_id", "status"],
    )
    op.create_index(
        "ix_blood_product_lab_followups_clinic_patient",
        "blood_product_lab_followups", ["clinic_id", "patient_id"],
    )
    # Existing S02 emergency-issued units get a pending follow-up.
    # Historical compatibility is NEVER inferred or auto-certified.
    op.execute(sa.text("""
        INSERT INTO blood_product_lab_followups
          (clinic_id, patient_id, admission_id, order_id, issued_unit_id, status, created_at)
        SELECT u.clinic_id, u.patient_id, u.admission_id, u.order_id,
               u.id, 'pending', u.issued_at
        FROM blood_product_issued_units AS u
        JOIN blood_product_orders AS o ON o.id = u.order_id
        WHERE o.compatibility_status = 'emergency_release'
    """))


def downgrade():
    op.drop_index("ix_blood_product_lab_followups_clinic_patient", table_name="blood_product_lab_followups")
    op.drop_index("ix_blood_product_lab_followups_order_status", table_name="blood_product_lab_followups")
    op.drop_table("blood_product_lab_followups")
