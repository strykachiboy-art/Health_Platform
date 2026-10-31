"""upgrade audit logs with immutable evidence metadata

Revision ID: a9b81c2d4f07
Revises: f8a70b1c3e96
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import Enum

from alembic import op
import sqlalchemy as sa


revision = "a9b81c2d4f07"
down_revision = "f8a70b1c3e96"
branch_labels = None
depends_on = None


def _action_value(value):
    if isinstance(value, Enum):
        return str(value.value)

    text = str(value)

    if "." in text:
        text = text.rsplit(".", 1)[-1]

    return text.lower()


def _datetime_value(value):
    if value is None:
        return None

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value

    if not isinstance(value, datetime):
        return str(value)

    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.isoformat(timespec="microseconds")


def _json_default(value):
    if isinstance(value, Enum):
        return value.value

    if isinstance(value, datetime):
        return _datetime_value(value)

    if isinstance(value, date):
        return value.isoformat()

    return str(value)


def _evidence_sha256(row) -> str:
    payload = {
        "user_id": row["user_id"],
        "clinic_id": row["clinic_id"],
        "action": _action_value(
            row["action"]
        ),
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "description": row["description"],
        "reason": row["reason"],
        "operation_id": row["operation_id"],
        "old_value": row["old_value"],
        "new_value": row["new_value"],
        "ip_address": row["ip_address"],
        "created_at": _datetime_value(
            row["created_at"]
        ),
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def upgrade():
    op.add_column(
        "audit_logs",
        sa.Column(
            "reason",
            sa.String(length=1000),
            nullable=True,
        ),
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "operation_id",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "audit_logs",
        sa.Column(
            "evidence_sha256",
            sa.String(length=64),
            nullable=True,
        ),
    )

    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            UPDATE audit_logs
            SET reason = description
            WHERE reason IS NULL
            """
        )
    )

    rows = bind.execute(
        sa.text(
            """
            SELECT
                id,
                user_id,
                clinic_id,
                action,
                entity_type,
                entity_id,
                description,
                reason,
                operation_id,
                old_value,
                new_value,
                ip_address,
                created_at
            FROM audit_logs
            ORDER BY id
            """
        )
    ).mappings()

    for row in rows:
        bind.execute(
            sa.text(
                """
                UPDATE audit_logs
                SET evidence_sha256 = :digest
                WHERE id = :audit_id
                """
            ),
            {
                "digest": _evidence_sha256(
                    row
                ),
                "audit_id": row["id"],
            },
        )

    op.alter_column(
        "audit_logs",
        "evidence_sha256",
        existing_type=sa.String(length=64),
        nullable=False,
    )

    op.create_index(
        "ix_audit_logs_operation_id",
        "audit_logs",
        ["operation_id"],
    )
    op.create_index(
        "ix_audit_logs_evidence_sha256",
        "audit_logs",
        ["evidence_sha256"],
    )
    op.create_index(
        "ix_audit_logs_clinic_operation_created_id",
        "audit_logs",
        [
            "clinic_id",
            "operation_id",
            "created_at",
            "id",
        ],
    )

    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION
            prevent_audit_logs_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION
                'audit_logs are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )

        op.execute(
            """
            CREATE TRIGGER audit_logs_no_update
            BEFORE UPDATE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION
            prevent_audit_logs_mutation()
            """
        )

        op.execute(
            """
            CREATE TRIGGER audit_logs_no_delete
            BEFORE DELETE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION
            prevent_audit_logs_mutation()
            """
        )

    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER audit_logs_no_update
            BEFORE UPDATE ON audit_logs
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'audit_logs are immutable'
                );
            END
            """
        )

        op.execute(
            """
            CREATE TRIGGER audit_logs_no_delete
            BEFORE DELETE ON audit_logs
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'audit_logs are immutable'
                );
            END
            """
        )


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "audit_logs_no_delete ON audit_logs"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "audit_logs_no_update ON audit_logs"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS "
            "prevent_audit_logs_mutation()"
        )

    elif dialect == "sqlite":
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "audit_logs_no_delete"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS "
            "audit_logs_no_update"
        )

    op.drop_index(
        "ix_audit_logs_clinic_operation_created_id",
        table_name="audit_logs",
    )
    op.drop_index(
        "ix_audit_logs_evidence_sha256",
        table_name="audit_logs",
    )
    op.drop_index(
        "ix_audit_logs_operation_id",
        table_name="audit_logs",
    )

    op.drop_column(
        "audit_logs",
        "evidence_sha256",
    )
    op.drop_column(
        "audit_logs",
        "operation_id",
    )
    op.drop_column(
        "audit_logs",
        "reason",
    )
