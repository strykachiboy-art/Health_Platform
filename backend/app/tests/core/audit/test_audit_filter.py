import pytest

from app.core.audit.filters.audit_filters import (
    get_audit_pagination,
    normalize_audit_filters,
    split_audit_filters,
)
from app.core.audit.schema.audit_filter import (
    AuditLogFilterSchema,
)
from app.core.enums.audit_enums import AuditAction


def test_normalize_audit_filters_returns_query_arguments():
    filters = AuditLogFilterSchema(
        user_id=10,
        action=AuditAction.LOGIN,
        entity_type="User",
        entity_id=20,
    )

    result = normalize_audit_filters(filters)

    assert result == {

        "patient_id": None,
        "user_id": 10,
        "action": AuditAction.LOGIN,
        "entity_type": "User",
        "entity_id": 20,
    }


def test_normalize_audit_filters_preserves_optional_none_values():
    filters = AuditLogFilterSchema()

    result = normalize_audit_filters(filters)

    assert result == {

        "patient_id": None,
        "user_id": None,
        "action": None,
        "entity_type": None,
        "entity_id": None,
    }


def test_normalize_audit_filters_uses_schema_normalization():
    filters = AuditLogFilterSchema(
        entity_type="  Patient  ",
    )

    result = normalize_audit_filters(filters)

    assert result["entity_type"] == "Patient"


def test_normalize_audit_filters_rejects_wrong_type():
    with pytest.raises(
        TypeError,
        match="filters must be an AuditLogFilterSchema",
    ):
        normalize_audit_filters(
            {
                "user_id": 10,
            }
        )


def test_get_audit_pagination_returns_page_and_per_page():
    filters = AuditLogFilterSchema(
        page=3,
        per_page=50,
    )

    assert get_audit_pagination(filters) == (
        3,
        50,
    )


def test_get_audit_pagination_uses_schema_defaults():
    filters = AuditLogFilterSchema()

    assert get_audit_pagination(filters) == (
        1,
        20,
    )


def test_get_audit_pagination_rejects_wrong_type():
    with pytest.raises(
        TypeError,
        match="filters must be an AuditLogFilterSchema",
    ):
        get_audit_pagination(None)


def test_split_audit_filters_separates_query_and_pagination():
    filters = AuditLogFilterSchema(
        user_id=10,
        action=AuditAction.LOGOUT,
        entity_type="User",
        entity_id=25,
        page=2,
        per_page=10,
    )

    query_filters, pagination = split_audit_filters(
        filters
    )

    assert query_filters == {

        "patient_id": None,
        "user_id": 10,
        "action": AuditAction.LOGOUT,
        "entity_type": "User",
        "entity_id": 25,
    }

    assert pagination == {
        "page": 2,
        "per_page": 10,
    }


def test_split_audit_filters_preserves_defaults():
    filters = AuditLogFilterSchema()

    query_filters, pagination = split_audit_filters(
        filters
    )

    assert query_filters == {

        "patient_id": None,
        "user_id": None,
        "action": None,
        "entity_type": None,
        "entity_id": None,
    }

    assert pagination == {
        "page": 1,
        "per_page": 20,
    }


def test_split_audit_filters_rejects_wrong_type():
    with pytest.raises(
        TypeError,
        match="filters must be an AuditLogFilterSchema",
    ):
        split_audit_filters("invalid")


@pytest.mark.parametrize(
    "payload",
    [
        {"page": 0},
        {"page": -1},
        {"per_page": 0},
        {"per_page": 101},
        {"entity_type": ""},
        {"entity_type": "   "},
    ],
)
def test_filter_schema_rejects_invalid_filter_values(
    payload,
):
    with pytest.raises(ValueError):
        AuditLogFilterSchema(**payload)


def test_filter_schema_rejects_unknown_fields():
    with pytest.raises(ValueError):
        AuditLogFilterSchema(
            unknown_field="value"
        )


def test_filter_schema_rejects_string_user_id():
    with pytest.raises(ValueError):
        AuditLogFilterSchema(
            user_id="10"
        )


def test_filter_schema_rejects_string_entity_id():
    with pytest.raises(ValueError):
        AuditLogFilterSchema(
            entity_id="10"
        )