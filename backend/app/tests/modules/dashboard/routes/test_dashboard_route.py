from __future__ import annotations

from datetime import date

import pytest
from flask_jwt_extended import create_access_token
from unittest.mock import Mock

import app.modules.dashboard.routes.dashboard_route as dashboard_routes

from app.core.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardQuerySchema,
)


# ============================================================================
# GAP-C: legitimate signed tenant + clinic test fixture
# ============================================================================

@pytest.fixture(autouse=True)
def _gapc_revocation_store_for_legacy_dashboard(app, monkeypatch):
    from app import extensions

    class Store:
        def exists(self, key):
            return 0
        def setex(self, key, ttl, value):
            return True

    monkeypatch.setattr(extensions, 'redis_client', Store())


@pytest.fixture()
def clinic(db, make_clinic):
    # Synthetic tenancy must exist before the child clinic INSERT.
    from uuid import uuid4
    from app.modules.clinic.models.organization_model import HospitalOrganization

    organization = HospitalOrganization(
        id=uuid4(), name='GAPC dashboard legacy fixture organization',
    )
    db.session.add(organization)
    db.session.flush()
    return make_clinic(
        name='GAPC legacy dashboard root',
        organization_id=organization.id,
        is_headquarters=True,
    )


@pytest.fixture()
def make_auth_headers(app, db, clinic):
    # Build real clinic-bound JWTs for existing route assertions.
    from flask_jwt_extended import create_access_token
    from app.modules.clinic.models.organization_model import (
        HospitalOrganization, TenantMembership, ClinicMembership,
    )

    organization = db.session.get(HospitalOrganization, clinic.organization_id)
    if organization is None:
        raise AssertionError('Synthetic clinic organization is missing')

    def _make(user, role=None):
        if user.clinic_id != clinic.id:
            raise AssertionError('Legacy dashboard actor has unexpected clinic')
        existing = db.session.query(TenantMembership).filter_by(
            organization_id=organization.id, user_id=user.id,
        ).one_or_none()
        if existing is None:
            db.session.add(TenantMembership(
                organization_id=organization.id, user_id=user.id,
                role='member', status='active', is_active=True,
            ))
            # Materialize the composite FK parent before inserting its child.
            # GAP-B explicitly requires (organization_id, user_id) to exist.
            db.session.flush()
            db.session.add(ClinicMembership(
                organization_id=organization.id, clinic_id=clinic.id,
                user_id=user.id, role=user.role.value, is_active=True,
            ))
            db.session.commit()
        claimed_role = role if role is not None else user.role
        claimed_role = getattr(claimed_role, 'value', claimed_role)
        with app.test_request_context():
            token = create_access_token(
                identity=str(user.id),
                additional_claims={
                    'role': claimed_role,
                    'token_version': user.token_version,
                    'tenant_id': str(organization.id),
                    'clinic_context_id': clinic.id,
                },
            )
        return {'Authorization': 'Bearer ' + token}

    return _make


# ============================================================================
# TEST HELPERS
# ============================================================================


class FakeDashboardResult:
    def __init__(self, payload):
        self.payload = payload
        self.dump_mode = None

    def model_dump(self, *, mode):
        self.dump_mode = mode
        return self.payload


def make_dashboard_result():
    return FakeDashboardResult(
        {
            "context": {
                "role": "admin",
                "scope": "clinic",
                "clinic_id": 1,
                "generated_at": "2026-09-19T12:00:00Z",
            },
            "period": {
                "date_from": "2026-09-01",
                "date_to": "2026-09-19",
            },
            "metrics": [],
            "alerts": [],
            "activity": [],
            "chat": {
                "unread_messages": 0,
                "unread_conversations": 0,
                "mentions": 0,
                "priority_messages": 0,
                "recent_messages": 0,
            },
        }
    )


# ============================================================================
# ROUTE REGISTRATION
# ============================================================================


def test_dashboard_route_is_registered(app):
    rules = {
        rule.rule
        for rule in app.url_map.iter_rules()
    }

    assert "/api/v1/dashboard" in rules


# ============================================================================
# AUTHENTICATION
# ============================================================================


def test_get_dashboard_requires_authentication(
    client,
):
    response = client.get(
        "/api/v1/dashboard",
    )

    assert response.status_code == 401


# ============================================================================
# SUCCESS
# ============================================================================


def test_get_dashboard_success(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    result = make_dashboard_result()

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
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"] == result.payload

    service.assert_called_once()

    kwargs = service.call_args.kwargs

    assert kwargs["actor_id"] == user.id
    assert isinstance(
        kwargs["query"],
        DashboardQuerySchema,
    )
    assert kwargs["query"].date_from is None
    assert kwargs["query"].date_to is None

    assert result.dump_mode == "json"


def test_get_dashboard_forwards_date_filters(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    result = make_dashboard_result()

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
            "date_from": "2026-09-01",
            "date_to": "2026-09-19",
        },
    )

    assert response.status_code == 200

    kwargs = service.call_args.kwargs
    query = kwargs["query"]

    assert kwargs["actor_id"] == user.id

    assert isinstance(
        query,
        DashboardQuerySchema,
    )

    assert query.date_from == date(
        2026,
        9,
        1,
    )

    assert query.date_to == date(
        2026,
        9,
        19,
    )


def test_get_dashboard_forwards_only_known_query_parameters(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    result = make_dashboard_result()

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
            "date_from": "2026-09-01",
            "unexpected": "ignored",
        },
    )

    assert response.status_code == 200

    query = service.call_args.kwargs["query"]

    assert query.date_from == date(
        2026,
        9,
        1,
    )

    assert query.date_to is None


# ============================================================================
# VALIDATION
# ============================================================================


def test_get_dashboard_rejects_invalid_date_from(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    service = Mock(
        return_value=make_dashboard_result(),
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
            "date_from": "not-a-date",
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Validation failed"
    assert body["details"]

    service.assert_not_called()


def test_get_dashboard_rejects_invalid_date_to(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    service = Mock(
        return_value=make_dashboard_result(),
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
            "date_to": "invalid-date",
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Validation failed"
    assert body["details"]

    service.assert_not_called()


def test_get_dashboard_rejects_invalid_date_range(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    service = Mock(
        return_value=make_dashboard_result(),
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
            "date_from": "2026-09-20",
            "date_to": "2026-09-01",
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Validation failed"

    service.assert_not_called()


# ============================================================================
# AUTHENTICATION IDENTITY VALIDATION
# ============================================================================


@pytest.mark.parametrize(
    "invalid_identity",
    [
        True,
        False,
        0,
        -1,
        "",
        "abc",
        None,
    ],
    ids=[
        "bool_true",
        "bool_false",
        "zero",
        "negative",
        "empty_string",
        "non_numeric",
        "none",
    ],
)
def test_get_dashboard_rejects_invalid_authentication_identity(
    client,
    user,
    make_auth_headers,
    monkeypatch,
    invalid_identity,
):
    service = Mock(
        return_value=make_dashboard_result(),
    )

    monkeypatch.setattr(
        dashboard_routes,
        "get_dashboard",
        service,
    )

    monkeypatch.setattr(
        dashboard_routes,
        "get_jwt_identity",
        lambda: invalid_identity,
    )

    response = client.get(
        "/api/v1/dashboard",
        headers=make_auth_headers(user),
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid authentication identity"

    service.assert_not_called()


def test_get_dashboard_converts_string_identity_to_integer(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    result = make_dashboard_result()

    service = Mock(
        return_value=result,
    )

    monkeypatch.setattr(
        dashboard_routes,
        "get_dashboard",
        service,
    )

    monkeypatch.setattr(
        dashboard_routes,
        "get_jwt_identity",
        lambda: str(user.id),
    )

    response = client.get(
        "/api/v1/dashboard",
        headers=make_auth_headers(user),
    )

    assert response.status_code == 200

    assert service.call_args.kwargs["actor_id"] == user.id
    assert isinstance(
        service.call_args.kwargs["actor_id"],
        int,
    )


# ============================================================================
# DOMAIN ERROR MAPPING
# ============================================================================


@pytest.mark.parametrize(
    "error_cls,status_code,message",
    [
        (
            DomainError,
            400,
            "Dashboard unavailable",
        ),
        (
            NotFoundError,
            404,
            "User not found",
        ),
        (
            ConflictError,
            409,
            "Dashboard conflict",
        ),
        (
            ValidationError,
            422,
            "Invalid dashboard request",
        ),
    ],
)
def test_get_dashboard_maps_domain_errors(
    client,
    user,
    make_auth_headers,
    monkeypatch,
    error_cls,
    status_code,
    message,
):
    service = Mock(
        side_effect=error_cls(message),
    )

    monkeypatch.setattr(
        dashboard_routes,
        "get_dashboard",
        service,
    )

    response = client.get(
        "/api/v1/dashboard",
        headers=make_auth_headers(user),
    )

    assert response.status_code == status_code

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == message


def test_get_dashboard_does_not_call_service_when_query_validation_fails(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    service = Mock(
        return_value=make_dashboard_result(),
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
            "date_to": "2026-09-01",
        },
    )

    assert response.status_code == 422
    service.assert_not_called()


# ============================================================================
# RESPONSE CONTRACT
# ============================================================================


def test_get_dashboard_returns_success_and_data(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    result = make_dashboard_result()

    monkeypatch.setattr(
        dashboard_routes,
        "get_dashboard",
        Mock(return_value=result),
    )

    response = client.get(
        "/api/v1/dashboard",
        headers=make_auth_headers(user),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert set(body.keys()) == {
        "success",
        "data",
    }

    assert body["success"] is True
    assert body["data"] == result.payload


def test_get_dashboard_serializes_result_using_json_mode(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    result = make_dashboard_result()

    monkeypatch.setattr(
        dashboard_routes,
        "get_dashboard",
        Mock(return_value=result),
    )

    response = client.get(
        "/api/v1/dashboard",
        headers=make_auth_headers(user),
    )

    assert response.status_code == 200
    assert result.dump_mode == "json"


# ============================================================================
# QUERY MODEL CONTRACT
# ============================================================================


def test_get_dashboard_passes_dashboard_query_schema_instance(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    result = make_dashboard_result()

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
            "date_from": "2026-09-05",
            "date_to": "2026-09-10",
        },
    )

    assert response.status_code == 200

    query = service.call_args.kwargs["query"]

    assert isinstance(
        query,
        DashboardQuerySchema,
    )

    assert query.model_dump() == {
        "date_from": date(
            2026,
            9,
            5,
        ),
        "date_to": date(
            2026,
            9,
            10,
        ),
    }
