"""add patient-scoped audit evidence hardening

Revision ID: b0c92d3e5f18
Revises: a9b81c2d4f07
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import Enum

from alembic import op
import sqlalchemy as sa


revision = "b0c92d3e5f18"
down_revision = "a9b81c2d4f07"
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


def _evidence_sha256(
    row,
    *,
    include_patient: bool,
) -> str:
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

    if include_patient:
        payload[
            "patient_id"
        ] = row["patient_id"]

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


def _drop_immutable_guards(
    dialect: str,
) -> None:
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


def _create_immutable_guards(
    dialect: str,
) -> None:
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


def _recompute_hashes(
    bind,
    *,
    include_patient: bool,
) -> None:
    patient_select = (
        "patient_id,"
        if include_patient
        else ""
    )

    rows = bind.execute(
        sa.text(
            f"""
            SELECT
                id,
                user_id,
                clinic_id,
                {patient_select}
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
                    row,
                    include_patient=(
                        include_patient
                    ),
                ),
                "audit_id": row["id"],
            },
        )


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    _drop_immutable_guards(
        dialect
    )

    if dialect == "sqlite":
        with op.batch_alter_table(
            "audit_logs"
        ) as batch:
            batch.add_column(
                sa.Column(
                    "patient_id",
                    sa.Integer(),
                    nullable=True,
                )
            )
            batch.create_foreign_key(
                "fk_audit_logs_patient_id_patients",
                "patients",
                ["patient_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    else:
        op.add_column(
            "audit_logs",
            sa.Column(
                "patient_id",
                sa.Integer(),
                nullable=True,
            ),
        )
        op.create_foreign_key(
            "fk_audit_logs_patient_id_patients",
            "audit_logs",
            "patients",
            ["patient_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_index(
        "ix_audit_logs_patient_id",
        "audit_logs",
        ["patient_id"],
    )

    op.create_index(
        "ix_audit_logs_clinic_patient_created_id",
        "audit_logs",
        [
            "clinic_id",
            "patient_id",
            "created_at",
            "id",
        ],
    )

    # Historical rows remain patient_id=NULL unless patient
    # attribution was actually captured at write time. Never
    # manufacture clinical attribution during migration.
    _recompute_hashes(
        bind,
        include_patient=True,
    )

    _create_immutable_guards(
        dialect
    )


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    _drop_immutable_guards(
        dialect
    )

    _recompute_hashes(
        bind,
        include_patient=False,
    )

    op.drop_index(
        "ix_audit_logs_clinic_patient_created_id",
        table_name="audit_logs",
    )
    op.drop_index(
        "ix_audit_logs_patient_id",
        table_name="audit_logs",
    )

    if dialect == "sqlite":
        with op.batch_alter_table(
            "audit_logs"
        ) as batch:
            batch.drop_constraint(
                "fk_audit_logs_patient_id_patients",
                type_="foreignkey",
            )
            batch.drop_column(
                "patient_id"
            )
    else:
        op.drop_constraint(
            "fk_audit_logs_patient_id_patients",
            "audit_logs",
            type_="foreignkey",
        )
        op.drop_column(
            "audit_logs",
            "patient_id",
        )

    _create_immutable_guards(
        dialect
    )
