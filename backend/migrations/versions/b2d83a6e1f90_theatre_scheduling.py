"""CE1-S02 theatre resource and booking constraints.

Revision ID: b2d83a6e1f90
Revises: a19f7c3e2d4b

Source-only migration; NOT applied to any application database by S02.
PostgreSQL exclusion constraint requires btree_gist and protects direct SQL
writers in addition to service-level theatre-row locking.
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d83a6e1f90"
down_revision = "a19f7c3e2d4b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operating_theatres",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("clinic_id", "code", name="uq_operating_theatres_clinic_code"),
        sa.CheckConstraint("length(code) > 0", name="ck_operating_theatres_code_nonempty"),
    )
    op.create_index("ix_operating_theatres_clinic_id", "operating_theatres", ["clinic_id"])
    op.create_table(
        "theatre_bookings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clinic_id", sa.Integer(), sa.ForeignKey("clinics.id"), nullable=False),
        sa.Column("surgical_case_id", sa.Integer(), sa.ForeignKey("surgical_cases.id"), nullable=False),
        sa.Column("theatre_id", sa.Integer(), sa.ForeignKey("operating_theatres.id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scheduled_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("cancelled_by_staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("starts_at < ends_at", name="ck_theatre_bookings_interval"),
        sa.CheckConstraint("status IN ('scheduled', 'cancelled')", name="ck_theatre_bookings_status"),
        sa.CheckConstraint("version >= 1", name="ck_theatre_bookings_version"),
        sa.CheckConstraint(
            "status != 'cancelled' OR (cancelled_at IS NOT NULL AND "
            "cancelled_by_staff_id IS NOT NULL AND cancellation_reason IS NOT NULL)",
            name="ck_theatre_bookings_cancelled_evidence",
        ),
    )
    for column in ("clinic_id", "surgical_case_id", "theatre_id"):
        op.create_index(f"ix_theatre_bookings_{column}", "theatre_bookings", [column])
    op.create_index(
        "uq_theatre_bookings_active_case", "theatre_bookings", ["surgical_case_id"],
        unique=True, postgresql_where=sa.text("status = 'scheduled'"),
        sqlite_where=sa.text("status = 'scheduled'"),
    )
    op.create_index(
        "ix_theatre_bookings_room_schedule", "theatre_bookings",
        ["theatre_id", "status", "starts_at", "ends_at"],
    )
    op.create_index(
        "ix_theatre_bookings_clinic_case", "theatre_bookings",
        ["clinic_id", "surgical_case_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        op.execute(
            "ALTER TABLE theatre_bookings ADD CONSTRAINT "
            "ex_theatre_bookings_room_overlap "
            "EXCLUDE USING gist (theatre_id WITH =, "
            "tsrange(starts_at, ends_at, '[)') WITH &&) "
            "WHERE (status = 'scheduled')"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE theatre_bookings DROP CONSTRAINT IF EXISTS ex_theatre_bookings_room_overlap")
    op.drop_index("ix_theatre_bookings_clinic_case", table_name="theatre_bookings")
    op.drop_index("ix_theatre_bookings_room_schedule", table_name="theatre_bookings")
    op.drop_index("uq_theatre_bookings_active_case", table_name="theatre_bookings")
    for column in ("theatre_id", "surgical_case_id", "clinic_id"):
        op.drop_index(f"ix_theatre_bookings_{column}", table_name="theatre_bookings")
    op.drop_table("theatre_bookings")
    op.drop_index("ix_operating_theatres_clinic_id", table_name="operating_theatres")
    op.drop_table("operating_theatres")
