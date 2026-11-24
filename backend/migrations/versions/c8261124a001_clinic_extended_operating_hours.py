"""Allow explicit 24/7 or overnight schedules using existing clinic time columns.

Revision ID: c8261124a001
Revises: bc12026a0001

NOT FOR PRODUCTION APPLICATION WITHOUT SEPARATE SCHEMA APPROVAL.
No new table or hours column. Existing rows are not rewritten.
"""
from alembic import op
import sqlalchemy as sa

revision = "c8261124a001"
down_revision = "bc12026a0001"
branch_labels = None
depends_on = None

_TABLE = "clinics"
_CHECK = "ck_clinics_valid_opening_hours"
_OLD = "opening_time IS NULL OR closing_time IS NULL OR opening_time < closing_time"
# The existing time columns are canonical. 00:00/00:00 is the ONLY 24/7
# sentinel. CAST handles both PostgreSQL TIME and SQLite TIME formats.
_NEW = (
    "opening_time IS NULL OR closing_time IS NULL "
    "OR opening_time <> closing_time "
    "OR (CAST(opening_time AS VARCHAR) IN ('00:00:00', '00:00:00.000000') "
    "AND CAST(closing_time AS VARCHAR) IN ('00:00:00', '00:00:00.000000'))"
)


def upgrade():
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, _NEW)


def downgrade():
    # Never restore the old constraint while it would reject valid 24/7 or
    # overnight records. No automatic data changes or deletion.
    connection = op.get_bind()
    incompatible = connection.execute(sa.text(
        "SELECT COUNT(*) FROM clinics WHERE opening_time IS NOT NULL "
        "AND closing_time IS NOT NULL AND opening_time >= closing_time"
    )).scalar_one()
    if incompatible:
        raise RuntimeError("Clinic operating schedules require manual downgrade approval")
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, _OLD)
