from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import pytest

import app.modules.dashboard.routes.dashboard_route as dashboard_routes
import app.modules.dashboard.services.dashboard_service as dashboard_service

from app.core.enums.role_enums import Role
from app.modules.dashboard.schemas.clinical_dashboard_schema import (
    ClinicalDashboardSchema,
)
from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardQuerySchema,
)
from app.modules.dashboard.schemas.management_dashboard_schema import (
    ManagementDashboardSchema,
)
from app.modules.dashboard.schemas.operations_dashboard_schema import (
    OperationsDashboardSchema,
)


CLINICAL_LEGACY_FIELDS = {
    "context",
    "period",
    "overview",
    "my_departments",
    "ai",
    "chat",
    "metrics",
    "alerts",
    "recent_activity",
}

MANAGEMENT_LEGACY_FIELDS = {
    "context",
    "period",
    "overview",
    "departments",
    "ai",
    "chat",
    "metrics",
    "alerts",
    "recent_activity",
}

OPERATIONS_LEGACY_FIELDS = {
    "context",
    "period",
    "overview",
    "chat",
    "metrics",
    "alerts",
    "recent_activity",
}


class _FakeDashboardResult:
    def __init__(self):
        self.dump_mode = None

    def model_dump(self, *, mode):
        self.dump_mode = mode

        return {
            "context": {
                "role": "admin",
                "scope": "clinic",
                "clinic_id": 1,
                "generated_at": "2026-10-31T18:00:00Z",
            },
            "period": {
                "date_from": "2026-10-01",
                "date_to": "2026-10-31",
            },
            "overview": {},
            "hospital": {
                "admissions": {
                    "active_admissions": 0,
                    "total_beds": 0,
                    "occupied_beds": 0,
                    "available_beds": 0,
                }
            },
            "metrics": [],
            "alerts": [],
            "recent_activity": [],
        }


def test_dashboard_public_route_and_method_are_preserved(app):
    matching = [
        rule
        for rule in app.url_map.iter_rules()
        if rule.rule == "/api/v1/dashboard"
    ]

    assert len(matching) == 1

    rule = matching[0]

    assert "GET" in rule.methods


def test_dashboard_query_contract_is_unchanged_and_strict():
    assert set(DashboardQuerySchema.model_fields) == {
        "date_from",
        "date_to",
    }

    query = DashboardQuerySchema.model_validate(
        {
            "date_from": "2026-10-01",
            "date_to": "2026-10-31",
        }
    )

    assert query.date_from == date(
        2026,
        10,
        1,
    )
    assert query.date_to == date(
        2026,
        10,
        31,
    )

    with pytest.raises(Exception):
        DashboardQuerySchema.model_validate(
            {
                "date_from": "2026-10-01",
                "clinic_id": 99,
            }
        )


def test_dashboard_route_preserves_envelope_filters_and_json_serialization(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    result = _FakeDashboardResult()

    service = Mock(
        return_value=result,
    )

    monkeypatch.setattr(
        dashboard_routes,
        "get_dashboard",
        service,
    )

    response = client.get(
        "/api/v1/dashboard",
        headers=make_auth_headers(user),
        query_string={
            "date_from": "2026-10-01",
            "date_to": "2026-10-31",
            "clinic_id": "999",
            "role": Role.SUPER_ADMIN.value,
            "unexpected": "ignored",
        },
    )

    assert response.status_code == 200

    body = response.get_json()

    assert set(body) == {
        "success",
        "data",
    }
    assert body["success"] is True
    assert body["data"]["hospital"] == {
        "admissions": {
            "active_admissions": 0,
            "total_beds": 0,
            "occupied_beds": 0,
            "available_beds": 0,
        }
    }

    kwargs = service.call_args.kwargs

    assert kwargs["actor_id"] == user.id

    query = kwargs["query"]

    assert isinstance(
        query,
        DashboardQuerySchema,
    )
    assert query.model_dump() == {
        "date_from": date(
            2026,
            10,
            1,
        ),
        "date_to": date(
            2026,
            10,
            31,
        ),
    }

    assert result.dump_mode == "json"


@pytest.mark.parametrize(
    "role,target_name",
    [
        (
            Role.SUPER_ADMIN,
            "get_super_admin_dashboard",
        ),
        (
            Role.ADMIN,
            "get_management_dashboard",
        ),
        (
            Role.DOCTOR,
            "get_clinical_dashboard",
        ),
        (
            Role.NURSE,
            "get_clinical_dashboard",
        ),
        (
            Role.PHARMACIST,
            "get_clinical_dashboard",
        ),
        (
            Role.LAB_TECHNICIAN,
            "get_clinical_dashboard",
        ),
        (
            Role.PARAMEDIC,
            "get_clinical_dashboard",
        ),
        (
            Role.EMT,
            "get_clinical_dashboard",
        ),
        (
            Role.RECEPTIONIST,
            "get_operations_dashboard",
        ),
        (
            Role.DRIVER,
            "get_operations_dashboard",
        ),
        (
            Role.AMBULANCE_DISPATCHER,
            "get_operations_dashboard",
        ),
        (
            Role.AMBULANCE_COORDINATOR,
            "get_operations_dashboard",
        ),
        (
            Role.OTHER,
            "get_operations_dashboard",
        ),
        (
            Role.ACCOUNTANT,
            "get_finance_dashboard",
        ),
        (
            Role.PATIENT,
            "get_patient_dashboard",
        ),
    ],
)
def test_dashboard_role_dispatcher_contract_is_preserved(
    clinic,
    make_user,
    monkeypatch,
    role,
    target_name,
):
    actor = make_user(
        None
        if role is Role.SUPER_ADMIN
        else clinic,
        role=role,
    )

    query = DashboardQuerySchema(
        date_from=date(
            2026,
            10,
            1,
        ),
        date_to=date(
            2026,
            10,
            31,
        ),
    )

    sentinel = object()

    targets = {
        "get_super_admin_dashboard",
        "get_management_dashboard",
        "get_clinical_dashboard",
        "get_operations_dashboard",
        "get_finance_dashboard",
        "get_patient_dashboard",
    }

    mocks = {}

    for name in targets:
        mocked = Mock(
            return_value=sentinel
            if name == target_name
            else object(),
        )

        monkeypatch.setattr(
            dashboard_service,
            name,
            mocked,
        )

        mocks[name] = mocked

    result = dashboard_service.get_dashboard(
        actor_id=actor.id,
        query=query,
    )

    assert result is sentinel

    selected = mocks[target_name]

    selected.assert_called_once_with(
        actor=actor,
        query=query,
    )

    for name, mocked in mocks.items():
        if name != target_name:
            mocked.assert_not_called()


def test_clinical_schema_is_backward_compatible_and_hospital_is_additive():
    current = set(
        ClinicalDashboardSchema.model_fields
    )

    assert CLINICAL_LEGACY_FIELDS.issubset(
        current
    )
    assert current == (
        CLINICAL_LEGACY_FIELDS
        | {
            "hospital",
        }
    )

    hospital_field = (
        ClinicalDashboardSchema
        .model_fields["hospital"]
    )

    assert hospital_field.default is None


def test_management_schema_is_backward_compatible_and_hospital_is_additive():
    current = set(
        ManagementDashboardSchema.model_fields
    )

    assert MANAGEMENT_LEGACY_FIELDS.issubset(
        current
    )
    assert current == (
        MANAGEMENT_LEGACY_FIELDS
        | {
            "hospital",
        }
    )


def test_operations_schema_is_backward_compatible_and_capacity_is_additive():
    current = set(
        OperationsDashboardSchema.model_fields
    )

    assert OPERATIONS_LEGACY_FIELDS.issubset(
        current
    )
    assert current == (
        OPERATIONS_LEGACY_FIELDS
        | {
            "hospital_capacity",
        }
    )

    capacity_field = (
        OperationsDashboardSchema
        .model_fields["hospital_capacity"]
    )

    assert capacity_field.default is None
