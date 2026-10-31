from __future__ import annotations

from typing import Any

from app.core.audit.schema.audit_filter import AuditLogFilterSchema


def normalize_audit_filters(
    filters: AuditLogFilterSchema,
) -> dict[str, Any]:
    if not isinstance(
        filters,
        AuditLogFilterSchema,
    ):
        raise TypeError(
            "filters must be an AuditLogFilterSchema"
        )

    return {
        "user_id": filters.user_id,
        "patient_id": filters.patient_id,
        "action": filters.action,
        "entity_type": filters.entity_type,
        "entity_id": filters.entity_id,
    }


def get_audit_pagination(
    filters: AuditLogFilterSchema,
) -> tuple[int, int]:
    if not isinstance(
        filters,
        AuditLogFilterSchema,
    ):
        raise TypeError(
            "filters must be an AuditLogFilterSchema"
        )

    return (
        filters.page,
        filters.per_page,
    )


def split_audit_filters(
    filters: AuditLogFilterSchema,
) -> tuple[dict[str, Any], dict[str, int]]:
    return (
        normalize_audit_filters(filters),
        {
            "page": filters.page,
            "per_page": filters.per_page,
        },
    )
