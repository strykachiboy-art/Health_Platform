"""Create durable Super Admin TOTP enrollment and single-use recovery-code tables.

Revision ID: bc12026a0001
Revises: f7a9e20bd613

SECURITY: No provider registration or login/recovery policy activation here.
This revision must NOT be applied to production without separate approval.
"""

from alembic import op
import sqlalchemy as sa

revision = "bc12026a0001"
down_revision = "f7a9e20bd613"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "super_admin_mfa_enrollments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("encrypted_totp_secret", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.Integer(), nullable=False),
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accepted_step", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("encryption_key_version >= 1", name="ck_super_admin_mfa_key_version"),
    )
    op.create_index(
        "ix_super_admin_mfa_enrollments_user_id",
        "super_admin_mfa_enrollments", ["user_id"], unique=True,
    )
    op.create_table(
        "super_admin_mfa_recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enrollment_id", sa.Integer(), sa.ForeignKey(
            "super_admin_mfa_enrollments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False, unique=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_super_admin_mfa_recovery_codes_enrollment_id",
        "super_admin_mfa_recovery_codes", ["enrollment_id"], unique=False,
    )


def downgrade():
    # Never silently destroy enrolled factors or consumed-code evidence.
    raise NotImplementedError(
        "Super Admin MFA enrollment storage downgrade requires a separately approved "
        "credential rotation and revocation plan."
    )
