"""Clinical alert list query contract and tenant safety regression.

The real route/service tests use the project's testing configuration and
in-memory SQLite database. No dev or production database is accessed.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.clinical_safety.models.clinical_alert_model import ClinicalAlert
from app.core.clinical_safety.schemas.clinical_alert_schema import (
    ClinicalAlertListQuerySchema,
)
from app.core.clinical_safety.services.alert_service import list_clinical_alerts
from app.core.enums.clinical_safety_enums import (
    ClinicalAlertStatus,
    ClinicalRuleAction,
    ClinicalRuleSeverity,
)
from app.core.enums.role_enums import Role


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("page", "2", 2),
        ("per_page", "25", 25),
        ("patient_id", "101", 101),
        ("rule_id", "102", 102),
        ("source_id", "103", 103),
        ("page", "0002", 2),
    ],
)
def test_schema_accepts_decimal_http_query_values(field, value, expected):
    parsed = ClinicalAlertListQuerySchema.model_validate({field: value})
    assert getattr(parsed, field) == expected
    assert isinstance(getattr(parsed, field), int)


def test_schema_preserves_defaults_and_native_int_contract():
    parsed = ClinicalAlertListQuerySchema.model_validate({})
    assert parsed.page == 1
    assert parsed.per_page == 50
    assert parsed.patient_id is None
    assert parsed.rule_id is None
    assert parsed.source_id is None
    parsed = ClinicalAlertListQuerySchema.model_validate(
        {"page": 3, "per_page": 5, "source_id": None}
    )
    assert (parsed.page, parsed.per_page, parsed.source_id) == (3, 5, None)


@pytest.mark.parametrize("field", ["page", "per_page", "patient_id", "rule_id", "source_id"])
@pytest.mark.parametrize("value", ["", "-1", "+1", " 1", "1 ", "1.0", "1e0", "1_0", "１", "١", "1\n", "a", "9" * 101, True, 1.5, ["1"]])
def test_schema_rejects_noncanonical_http_integer_values(field, value):
    with pytest.raises(PydanticValidationError):
        ClinicalAlertListQuerySchema.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page", "0"),
        ("per_page", "0"),
        ("per_page", "501"),
        ("patient_id", "0"),
        ("rule_id", "0"),
        ("source_id", "0"),
        ("unknown", "123"),
        ("severity", "not-a-severity"),
        ("action", "not-an-action"),
        ("status", "not-a-status"),
    ],
)
def test_schema_rejects_out_of_range_and_unrecognized_values(field, value):
    with pytest.raises(PydanticValidationError):
        ClinicalAlertListQuerySchema.model_validate({field: value})


def test_get_route_passes_every_advertised_filter_to_service(
    client, auth_headers_for, clinical_safety_admin, clinic, monkeypatch
):
    service = Mock(return_value={"items": [], "total": 0, "page": 2, "per_page": 3})
    monkeypatch.setattr(
        "app.core.clinical_safety.routes.clinical_safety_routes.list_clinical_alerts",
        service,
    )
    response = client.get(
        "/api/v1/clinical-safety/alerts"
        "?page=2&per_page=3&patient_id=11&rule_id=12&source_id=13"
        "&severity=high&action=block&status=open&source_type=prescription",
        headers=auth_headers_for(clinical_safety_admin, role=Role.ADMIN),
    )
    assert response.status_code == 200
    assert response.get_json() == {"items": [], "total": 0, "page": 2, "per_page": 3}
    assert service.call_count == 1
    assert service.call_args.kwargs == {
        "clinic_id": clinic.id,
        "page": 2,
        "per_page": 3,
        "patient_id": 11,
        "rule_id": 12,
        "source_id": 13,
        "severity": ClinicalRuleSeverity.HIGH,
        "action": ClinicalRuleAction.BLOCK,
        "status": ClinicalAlertStatus.OPEN,
        "source_type": "prescription",
    }


@pytest.mark.parametrize(
    "query",
    [
        "page=-1",
        "page=1.5",
        "page=%2B1",
        "page=1%20",
        "page=%EF%BC%91",
        "per_page=501",
        "patient_id=abc",
        "rule_id=0",
        "source_id=1.25",
        "action=unsupported",
        "severity=unsupported",
        "status=unsupported",
        "clinic_id=999",
        "source_id=" + "9" * 101,
    ],
)
def test_invalid_route_query_fails_before_service(
    query, client, auth_headers_for, clinical_safety_admin, monkeypatch
):
    service = Mock()
    monkeypatch.setattr(
        "app.core.clinical_safety.routes.clinical_safety_routes.list_clinical_alerts",
        service,
    )
    response = client.get(
        "/api/v1/clinical-safety/alerts?" + query,
        headers=auth_headers_for(clinical_safety_admin, role=Role.ADMIN),
    )
    assert response.status_code == 422
    service.assert_not_called()


def test_route_clinic_is_from_authenticated_context_not_query(
    client, auth_headers_for, clinical_safety_other_admin,
    clinical_safety_other_clinic, monkeypatch
):
    service = Mock(return_value={"items": [], "total": 0, "page": 1, "per_page": 50})
    monkeypatch.setattr(
        "app.core.clinical_safety.routes.clinical_safety_routes.list_clinical_alerts",
        service,
    )
    response = client.get(
        "/api/v1/clinical-safety/alerts?source_id=111",
        headers=auth_headers_for(clinical_safety_other_admin, role=Role.ADMIN),
    )
    assert response.status_code == 200
    assert service.call_args.kwargs["clinic_id"] == clinical_safety_other_clinic.id
    assert service.call_args.kwargs["source_id"] == 111


def _add_alert(session, *, clinic, patient, rule, severity, action, source_type, source_id):
    row = ClinicalAlert(
        clinic_id=clinic.id,
        patient_id=patient.id,
        rule_id=rule.id,
        rule_version=rule.version,
        severity=severity,
        action=action,
        status=ClinicalAlertStatus.OPEN,
        title=f"Clinical safety row {source_id}",
        message="Synthetic clinical alert fixture",
        context={},
        source_type=source_type,
        source_id=source_id,
        deduplication_key=f"query-contract-{clinic.id}-{source_id}",
    )
    session.add(row)
    session.flush()
    return row


def test_service_filters_are_effective_paginated_and_clinic_scoped(
    db_session, clinic, patient, clinical_rule, make_clinical_rule,
    clinical_safety_admin, clinical_safety_other_clinic,
    clinical_safety_other_admin, make_patient
):
    second_rule = make_clinical_rule(
        clinic=clinic,
        rule_code="FILTER_SECOND_RULE",
        created_by_user=clinical_safety_admin,
        updated_by_user=clinical_safety_admin,
    )
    other_patient = make_patient(clinical_safety_other_clinic)
    other_rule = make_clinical_rule(
        clinic=clinical_safety_other_clinic,
        rule_code="FILTER_OTHER_TENANT_RULE",
        created_by_user=clinical_safety_other_admin,
        updated_by_user=clinical_safety_other_admin,
    )
    first = _add_alert(
        db_session, clinic=clinic, patient=patient, rule=clinical_rule,
        severity=ClinicalRuleSeverity.HIGH,
        action=ClinicalRuleAction.BLOCK,
        source_type="prescription", source_id=101,
    )
    second = _add_alert(
        db_session, clinic=clinic, patient=patient, rule=clinical_rule,
        severity=ClinicalRuleSeverity.MODERATE,
        action=ClinicalRuleAction.ALERT,
        source_type="laboratory", source_id=102,
    )
    third = _add_alert(
        db_session, clinic=clinic, patient=patient, rule=second_rule,
        severity=ClinicalRuleSeverity.HIGH,
        action=ClinicalRuleAction.ALERT,
        source_type="prescription", source_id=103,
    )
    _add_alert(
        db_session, clinic=clinical_safety_other_clinic, patient=other_patient,
        rule=other_rule, severity=ClinicalRuleSeverity.HIGH,
        action=ClinicalRuleAction.BLOCK,
        source_type="prescription", source_id=101,
    )
    expected = [
        ({"source_id": 101}, [first.id]),
        ({"source_type": "laboratory"}, [second.id]),
        ({"source_type": " PRESCRIPTION ", "severity": "high", "action": "block"}, [first.id]),
        ({"severity": ClinicalRuleSeverity.HIGH}, [first.id, third.id]),
        ({"action": ClinicalRuleAction.ALERT}, [second.id, third.id]),
        ({"rule_id": second_rule.id}, [third.id]),
        ({"patient_id": patient.id}, [first.id, second.id, third.id]),
        ({"status": ClinicalAlertStatus.OPEN}, [first.id, second.id, third.id]),
    ]
    for filters, ids in expected:
        page = list_clinical_alerts(clinic_id=clinic.id, **filters)
        assert page["total"] == len(ids)
        assert {row.id for row in page["items"]} == set(ids)
        assert all(row.clinic_id == clinic.id for row in page["items"])
    only_tenant = list_clinical_alerts(
        clinic_id=clinic.id, severity=ClinicalRuleSeverity.HIGH,
        action=ClinicalRuleAction.BLOCK, source_type="prescription", source_id=101,
    )
    assert only_tenant["total"] == 1
    assert [row.id for row in only_tenant["items"]] == [first.id]
    assert list_clinical_alerts(clinic_id=clinical_safety_other_clinic.id)["total"] == 1
    page_one = list_clinical_alerts(clinic_id=clinic.id, page=1, per_page=2)
    page_two = list_clinical_alerts(clinic_id=clinic.id, page=2, per_page=2)
    assert (page_one["total"], page_two["total"]) == (3, 3)
    assert len(page_one["items"]) == 2
    assert len(page_two["items"]) == 1
    assert set(row.id for row in page_one["items"]).isdisjoint(
        row.id for row in page_two["items"]
    )
