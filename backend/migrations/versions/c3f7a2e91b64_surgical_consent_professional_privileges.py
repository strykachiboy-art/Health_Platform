"""CE1-S03 surgical consent fingerprint and procedure-scoped staff privileges.

Revision ID: c3f7a2e91b64
Revises: b2d83a6e1f90

Source-only; not applied to the running application database.
"""
from alembic import op
import sqlalchemy as sa

revision = "c3f7a2e91b64"
down_revision = "b2d83a6e1f90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "surgical_professional_privileges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("privilege_kind", sa.String(length=24), nullable=False),
        sa.Column("procedure_code", sa.String(length=64), nullable=False),
        sa.Column("license_reference", sa.String(length=128), nullable=False),
        sa.Column("credential_document_ref", sa.String(length=255), nullable=False),
        sa.Column("credential_sha256", sa.String(length=64), nullable=False),
        sa.Column("verification_source", sa.String(length=255), nullable=False),
        sa.Column("granted_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("revocation_reason", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "privilege_kind IN ('surgeon', 'anesthetist')", name="ck_surgical_privileges_kind"
        ),
        sa.CheckConstraint("expires_at > granted_at", name="ck_surgical_privileges_duration"),
        sa.CheckConstraint("version >= 1", name="ck_surgical_privileges_version"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR (revoked_by_staff_id IS NOT NULL AND "
            "revocation_reason IS NOT NULL)",
            name="ck_surgical_privileges_revoke_evidence",
        ),
    )
    for column in ("clinic_id", "staff_id"):
        op.create_index(
            f"ix_surgical_professional_privileges_{column}",
            "surgical_professional_privileges", [column],
        )
    op.create_index(
        "uq_surgical_privileges_active_scope", "surgical_professional_privileges",
        ["clinic_id", "staff_id", "privilege_kind", "procedure_code"],
        unique=True, postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "surgical_consent_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("surgical_case_id", sa.Integer(), sa.ForeignKey("surgical_cases.id"), nullable=False),
        sa.Column("procedure_order_id", sa.Integer(), sa.ForeignKey("procedure_orders.id"), nullable=False),
        sa.Column("procedure_order_version", sa.Integer(), nullable=False),
        sa.Column("procedure_code", sa.String(length=64), nullable=False),
        sa.Column("procedure_name", sa.String(length=255), nullable=False),
        sa.Column("body_site", sa.String(length=255), nullable=True),
        sa.Column("laterality", sa.String(length=32), nullable=True),
        sa.Column("signer_kind", sa.String(length=32), nullable=False),
        sa.Column("signer_name", sa.String(length=255), nullable=False),
        sa.Column("signer_authority", sa.String(length=255), nullable=True),
        sa.Column("signed_document_ref", sa.String(length=255), nullable=False),
        sa.Column("signed_document_sha256", sa.String(length=64), nullable=False),
        sa.Column("signed_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("recorded_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("verified_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("withdrawn_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(), nullable=True),
        sa.Column("withdrawal_reason", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('recorded', 'verified', 'withdrawn')", name="ck_surgical_consent_status"
        ),
        sa.CheckConstraint(
            "signer_kind IN ('patient', 'guardian', 'authorized_representative')",
            name="ck_surgical_consent_signer",
        ),
        sa.CheckConstraint(
            "signer_kind = 'patient' OR (signer_authority IS NOT NULL AND "
            "length(signer_authority) > 0)",
            name="ck_surgical_consent_authority",
        ),
        sa.CheckConstraint("expires_at > signed_at", name="ck_surgical_consent_duration"),
        sa.CheckConstraint(
            "status != 'verified' OR (verified_at IS NOT NULL AND "
            "verified_by_staff_id IS NOT NULL)",
            name="ck_surgical_consent_verified_evidence",
        ),
        sa.CheckConstraint(
            "status != 'withdrawn' OR (withdrawn_at IS NOT NULL AND "
            "withdrawn_by_staff_id IS NOT NULL AND withdrawal_reason IS NOT NULL)",
            name="ck_surgical_consent_withdrawn_evidence",
        ),
        sa.CheckConstraint("version >= 1", name="ck_surgical_consent_version"),
    )
    op.create_index(
        "ix_surgical_consent_evidence_clinic_id", "surgical_consent_evidence", ["clinic_id"]
    )
    op.create_index(
        "uq_surgical_consent_open_case", "surgical_consent_evidence",
        ["surgical_case_id"], unique=True,
        postgresql_where=sa.text("status IN ('recorded', 'verified')"),
        sqlite_where=sa.text("status IN ('recorded', 'verified')"),
    )
    op.create_index(
        "ix_surgical_consent_clinic_case_status", "surgical_consent_evidence",
        ["clinic_id", "surgical_case_id", "status"],
    )


def downgrade():
    op.drop_index("ix_surgical_consent_clinic_case_status", table_name="surgical_consent_evidence")
    op.drop_index("uq_surgical_consent_open_case", table_name="surgical_consent_evidence")
    op.drop_index("ix_surgical_consent_evidence_clinic_id", table_name="surgical_consent_evidence")
    op.drop_table("surgical_consent_evidence")
    op.drop_index("uq_surgical_privileges_active_scope", table_name="surgical_professional_privileges")
    for column in ("staff_id", "clinic_id"):
        op.drop_index(
            f"ix_surgical_professional_privileges_{column}",
            table_name="surgical_professional_privileges",
        )
    op.drop_table("surgical_professional_privileges")
