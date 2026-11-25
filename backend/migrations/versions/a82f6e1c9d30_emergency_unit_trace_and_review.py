"""P23D.E S02 emergency unit issue provenance and post-release review.

Revision ID: a82f6e1c9d30
Revises: e24a9bd7c101

Migration source only; review and deployment remain separately gated.
"""
from alembic import op
import sqlalchemy as sa

revision = "a82f6e1c9d30"
down_revision = "e24a9bd7c101"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "blood_product_orders",
        sa.Column("emergency_review_outcome", sa.String(20), nullable=True),
    )
    op.add_column(
        "blood_product_orders",
        sa.Column("emergency_review_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "blood_product_orders",
        sa.Column("emergency_reviewed_by_staff_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "blood_product_orders",
        sa.Column("emergency_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_blood_product_orders_emergency_reviewed_by_staff_id",
        "blood_product_orders", "staff", ["emergency_reviewed_by_staff_id"], ["id"],
    )
    op.create_check_constraint(
        "ck_blood_product_orders_emergency_review_evidence",
        "blood_product_orders",
        "emergency_review_outcome IS NULL OR "
        "(emergency_review_outcome IN ('reviewed', 'escalated') AND "
        "compatibility_status = 'emergency_release' AND "
        "status = 'completed' AND "
        "emergency_reviewed_by_staff_id IS NOT NULL AND "
        "emergency_reviewed_at IS NOT NULL AND "
        "emergency_review_note IS NOT NULL)",
    )

    op.create_table(
        "blood_product_issued_units",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("admission_id", sa.Integer(), sa.ForeignKey("admissions.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("blood_product_orders.id"), nullable=False),
        sa.Column("unit_identifier", sa.String(128), nullable=False),
        sa.Column("product_blood_type", sa.String(16), nullable=False),
        sa.Column("issued_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "clinic_id", "unit_identifier",
            name="uq_blood_product_issued_unit_clinic_identifier",
        ),
        sa.CheckConstraint(
            "length(trim(unit_identifier)) > 0 AND unit_identifier = upper(unit_identifier)",
            name="ck_blood_product_issued_unit_normalized_identifier",
        ),
    )
    op.create_index(
        "ix_blood_product_issued_units_order_id",
        "blood_product_issued_units", ["order_id", "id"],
    )
    op.create_index(
        "ix_blood_product_issued_units_clinic_patient",
        "blood_product_issued_units", ["clinic_id", "patient_id"],
    )


def downgrade():
    op.drop_index("ix_blood_product_issued_units_clinic_patient", "blood_product_issued_units")
    op.drop_index("ix_blood_product_issued_units_order_id", "blood_product_issued_units")
    op.drop_table("blood_product_issued_units")
    op.drop_constraint(
        "ck_blood_product_orders_emergency_review_evidence",
        "blood_product_orders", type_="check",
    )
    op.drop_constraint(
        "fk_blood_product_orders_emergency_reviewed_by_staff_id",
        "blood_product_orders", type_="foreignkey",
    )
    op.drop_column("blood_product_orders", "emergency_reviewed_at")
    op.drop_column("blood_product_orders", "emergency_reviewed_by_staff_id")
    op.drop_column("blood_product_orders", "emergency_review_note")
    op.drop_column("blood_product_orders", "emergency_review_outcome")
