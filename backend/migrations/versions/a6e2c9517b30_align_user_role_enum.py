"""Align the PostgreSQL user role enum with application role members.

Revision ID: a6e2c9517b30
Revises: f19b3a7d6c42
Create Date: 2026-10-18
"""

from alembic import op


revision = "a6e2c9517b30"
down_revision = "f19b3a7d6c42"
branch_labels = None
depends_on = None


def upgrade():
    # Preserve the legacy enum's existing values while renaming LAB_TECH.
    # Fail closed if both spellings exist or neither spelling exists.
    op.execute(
        """
        DO $role_enum_migration$
        DECLARE
            has_legacy BOOLEAN;
            has_target BOOLEAN;
        BEGIN
            SELECT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE n.nspname = 'public'
                  AND t.typname = 'role'
                  AND e.enumlabel = 'LAB_TECH'
            ) INTO has_legacy;

            SELECT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE n.nspname = 'public'
                  AND t.typname = 'role'
                  AND e.enumlabel = 'LAB_TECHNICIAN'
            ) INTO has_target;

            IF has_legacy AND has_target THEN
                RAISE EXCEPTION
                    'Cannot align public.role: both LAB_TECH and LAB_TECHNICIAN exist';
            ELSIF has_legacy THEN
                EXECUTE
                    'ALTER TYPE public.role '
                    'RENAME VALUE ''LAB_TECH'' TO ''LAB_TECHNICIAN''';
            ELSIF NOT has_target THEN
                RAISE EXCEPTION
                    'Cannot align public.role: neither LAB_TECH nor LAB_TECHNICIAN exists';
            END IF;
        END;
        $role_enum_migration$;
        """
    )

    # Add the remaining application enum members idempotently.
    # These new labels are not used until this migration transaction commits.
    for label in (
        "SUPER_ADMIN",
        "PARAMEDIC",
        "EMT",
        "DRIVER",
        "AMBULANCE_DISPATCHER",
        "AMBULANCE_COORDINATOR",
        "OTHER",
    ):
        op.execute(
            f"ALTER TYPE public.role ADD VALUE IF NOT EXISTS '{label}'"
        )


def downgrade():
    raise NotImplementedError(
        "Downgrade is intentionally unsupported: PostgreSQL enum labels "
        "cannot be safely removed after application data may use them."
    )