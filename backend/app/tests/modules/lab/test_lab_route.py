from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.lab_enums import (
    LabOrderStatus,
    LabResultFlag,
    SampleType,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)


@pytest.fixture
def lab_routes():
    import app.modules.lab.routes.lab_route as routes

    return routes


@pytest.fixture
def lab_admin_context(make_authenticated_staff, clinic):
    return make_authenticated_staff(
        clinic,
        Role.ADMIN,
    )


@pytest.fixture
def lab_admin_staff(lab_admin_context):
    staff, _ = lab_admin_context
    return staff


@pytest.fixture
def lab_admin_headers(lab_admin_context):
    _, headers = lab_admin_context
    return headers


@pytest.fixture
def lab_doctor_context(make_authenticated_staff, clinic):
    return make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )


@pytest.fixture
def lab_doctor_staff(lab_doctor_context):
    staff, _ = lab_doctor_context
    return staff


@pytest.fixture
def lab_doctor_headers(lab_doctor_context):
    _, headers = lab_doctor_context
    return headers


@pytest.fixture
def lab_nurse_context(make_authenticated_staff, clinic):
    return make_authenticated_staff(
        clinic,
        Role.NURSE,
    )


@pytest.fixture
def lab_nurse_headers(lab_nurse_context):
    _, headers = lab_nurse_context
    return headers


@pytest.fixture
def lab_technician_context(make_authenticated_staff, clinic):
    return make_authenticated_staff(
        clinic,
        Role.LAB_TECHNICIAN,
    )


@pytest.fixture
def lab_technician_staff(lab_technician_context):
    staff, _ = lab_technician_context
    return staff


@pytest.fixture
def lab_technician_headers(lab_technician_context):
    _, headers = lab_technician_context
    return headers


@pytest.fixture
def lab_receptionist_context(make_authenticated_staff, clinic):
    return make_authenticated_staff(
        clinic,
        Role.RECEPTIONIST,
    )


@pytest.fixture
def lab_receptionist_headers(lab_receptionist_context):
    _, headers = lab_receptionist_context
    return headers


def make_lab_test(**overrides):
    values = dict(
        id=1,
        clinic_id=1,
        loinc_code="12345-6",
        name="CBC",
        code="CBC",
        sample_type=SampleType.BLOOD,
        reference_range="5 - 20",
        unit="mg/dL",
        price=Decimal("25.00"),
        critical_low=Decimal("2.000"),
        critical_high=Decimal("40.000"),
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def make_lab_item(**overrides):
    test = overrides.pop(
        "test",
        make_lab_test(),
    )

    values = dict(
        id=1,
        order_id=1,
        test_id=test.id,
        test=test,
        result_value="10",
        flag=LabResultFlag.NORMAL,
        result_notes="Normal",
        result_file_url=None,
        resulted_at=datetime.now(timezone.utc),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def make_lab_order(**overrides):
    values = dict(
        id=1,
        clinic_id=1,
        patient_id=10,
        consultation_id=None,
        ordered_by_id=1,
        collected_by_id=2,
        processed_by_id=3,
        verified_by_id=None,
        status=LabOrderStatus.IN_PROGRESS,
        qr_code="LAB-123",
        sample_collected_at=datetime.now(timezone.utc),
        processed_at=datetime.now(timezone.utc),
        verified_at=None,
        completed_at=None,
        equipment_reference_id=None,
        cancellation_reason=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        items=[make_lab_item()],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def make_page(
    items,
    *,
    total=None,
    page=1,
    per_page=50,
):
    return SimpleNamespace(
        items=list(items),
        total=len(items) if total is None else total,
        page=page,
        per_page=per_page,
    )


def test_endpoints_require_auth(client):
    response = client.get("/api/v1/lab/tests")

    assert response.status_code in (401, 422)


def test_get_current_user_resolves_authenticated_user(
    lab_routes,
    user,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_routes,
        "get_jwt_identity",
        Mock(return_value=str(user.id)),
    )

    assert lab_routes._get_current_user() is user


def test_get_current_user_rejects_invalid_identity(
    lab_routes,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_routes,
        "get_jwt_identity",
        Mock(return_value="not-an-integer"),
    )

    with pytest.raises(
        ValidationError,
        match="Invalid authentication identity",
    ):
        lab_routes._get_current_user()


def test_get_current_user_rejects_missing_user(
    lab_routes,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_routes,
        "get_jwt_identity",
        Mock(return_value="999999"),
    )

    monkeypatch.setattr(
        lab_routes.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(
        ValidationError,
        match="Authenticated user could not be resolved",
    ):
        lab_routes._get_current_user()


def test_get_current_user_rejects_inactive_user(
    lab_routes,
    user,
    monkeypatch,
):
    user.is_active = False

    monkeypatch.setattr(
        lab_routes,
        "get_jwt_identity",
        Mock(return_value=str(user.id)),
    )

    with pytest.raises(
        ValidationError,
        match="User account is inactive",
    ):
        lab_routes._get_current_user()


def test_get_current_clinic_id_uses_central_resolver(
    lab_routes,
    monkeypatch,
):
    resolver = Mock(return_value=7)
    monkeypatch.setattr(
        lab_routes,
        "get_current_clinic_id",
        resolver,
    )

    assert lab_routes._get_current_clinic_id() == 7
    resolver.assert_called_once_with(required=True)


def test_get_current_staff_uses_modern_select(
    lab_routes,
    lab_admin_staff,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_routes,
        "_get_current_user",
        Mock(return_value=lab_admin_staff.user),
    )

    execute = Mock(
        return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(
                first=lambda: lab_admin_staff
            )
        )
    )

    monkeypatch.setattr(
        lab_routes.db.session,
        "execute",
        execute,
    )

    assert lab_routes._get_current_staff() is lab_admin_staff
    execute.assert_called_once()

    statement = execute.call_args.args[0]
    assert statement is not None


def test_get_current_staff_rejects_missing_staff(
    lab_routes,
    user,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_routes,
        "_get_current_user",
        Mock(return_value=user),
    )

    monkeypatch.setattr(
        lab_routes.db.session,
        "execute",
        Mock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    first=lambda: None
                )
            )
        ),
    )

    with pytest.raises(
        DomainError,
        match="not associated with a staff record",
    ):
        lab_routes._get_current_staff()


def test_get_current_staff_id_returns_staff_primary_key(
    lab_routes,
    lab_admin_staff,
    monkeypatch,
):
    monkeypatch.setattr(
        lab_routes,
        "_get_current_staff",
        Mock(return_value=lab_admin_staff),
    )

    assert (
        lab_routes._get_current_staff_id()
        == lab_admin_staff.id
    )


def test_serialize_lab_test_handles_values(
    lab_routes,
):
    result = lab_routes._serialize_lab_test(
        make_lab_test()
    )

    assert result["id"] == 1
    assert result["name"] == "CBC"
    assert result["clinic_id"] == 1
    assert result["sample_type"] == SampleType.BLOOD.value
    assert result["price"] == "25.00"
    assert result["critical_low"] == "2.000"
    assert result["critical_high"] == "40.000"
    assert result["is_active"] is True


def test_serialize_lab_test_handles_none_values(
    lab_routes,
):
    result = lab_routes._serialize_lab_test(
        make_lab_test(
            clinic_id=None,
            sample_type=None,
            price=None,
            critical_low=None,
            critical_high=None,
            created_at=None,
            updated_at=None,
        )
    )

    assert result["clinic_id"] is None
    assert result["sample_type"] is None
    assert result["price"] is None
    assert result["critical_low"] is None
    assert result["critical_high"] is None
    assert result["created_at"] is None
    assert result["updated_at"] is None


def test_serialize_lab_order_item(
    lab_routes,
):
    result = lab_routes._serialize_lab_order_item(
        make_lab_item()
    )

    assert result["id"] == 1
    assert result["order_id"] == 1
    assert result["test_id"] == 1
    assert result["test"]["name"] == "CBC"
    assert result["result_value"] == "10"
    assert result["flag"] == LabResultFlag.NORMAL.value


def test_serialize_lab_order(
    lab_routes,
):
    result = lab_routes._serialize_lab_order(
        make_lab_order()
    )

    assert result["id"] == 1
    assert result["clinic_id"] == 1
    assert result["patient_id"] == 10
    assert result["status"] == LabOrderStatus.IN_PROGRESS.value
    assert result["qr_code"] == "LAB-123"
    assert len(result["items"]) == 1


def test_create_test_is_admin_only(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/tests",
        headers=lab_doctor_headers,
        json={"name": "CBC"},
    )

    assert response.status_code == 403


def test_update_test_is_admin_only(
    client,
    lab_doctor_headers,
):
    response = client.patch(
        "/api/v1/lab/tests/1",
        headers=lab_doctor_headers,
        json={"name": "Updated CBC"},
    )

    assert response.status_code == 403


def test_create_order_rejects_receptionist(
    client,
    lab_receptionist_headers,
):
    response = client.post(
        "/api/v1/lab/orders",
        headers=lab_receptionist_headers,
        json={
            "patient_id": 10,
            "test_ids": [1],
        },
    )

    assert response.status_code == 403


def test_collect_sample_rejects_doctor(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/collect-sample",
        headers=lab_doctor_headers,
        json={},
    )

    assert response.status_code == 403


def test_process_sample_rejects_doctor(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/process",
        headers=lab_doctor_headers,
        json={},
    )

    assert response.status_code == 403


def test_verify_results_rejects_doctor(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/verify",
        headers=lab_doctor_headers,
        json={},
    )

    assert response.status_code == 403


def test_complete_order_rejects_doctor(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/complete",
        headers=lab_doctor_headers,
    )

    assert response.status_code == 403


def test_cancel_order_rejects_receptionist(
    client,
    lab_receptionist_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/cancel",
        headers=lab_receptionist_headers,
        json={"reason": "Cancel"},
    )

    assert response.status_code == 403


def test_create_lab_test_success(
    client,
    lab_admin_headers,
    lab_admin_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_test(
            clinic_id=lab_admin_staff.clinic_id
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "create_lab_test",
        service,
    )

    response = client.post(
        "/api/v1/lab/tests",
        headers=lab_admin_headers,
        json={
            "name": "CBC",
            "loinc_code": "12345-6",
            "code": "CBC",
            "sample_type": "blood",
            "reference_range": "5 - 20",
            "unit": "mg/dL",
            "price": "25.00",
            "critical_low": "2.000",
            "critical_high": "40.000",
            "is_active": True,
        },
    )

    assert response.status_code == 201

    body = response.get_json()
    assert body["success"] is True
    assert body["data"]["name"] == "CBC"

    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_admin_staff.clinic_id
    )


def test_create_lab_test_ignores_no_client_clinic_field(
    client,
    lab_admin_headers,
    lab_admin_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_test()
    )

    monkeypatch.setattr(
        lab_routes,
        "create_lab_test",
        service,
    )

    response = client.post(
        "/api/v1/lab/tests",
        headers=lab_admin_headers,
        json={
            "name": "CBC",
        },
    )

    assert response.status_code == 201
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_admin_staff.clinic_id
    )


def test_create_lab_test_rejects_client_clinic_id(
    client,
    lab_admin_headers,
    monkeypatch,
    lab_routes,
):
    service = Mock()

    monkeypatch.setattr(
        lab_routes,
        "create_lab_test",
        service,
    )

    response = client.post(
        "/api/v1/lab/tests",
        headers=lab_admin_headers,
        json={
            "name": "CBC",
            "clinic_id": 999,
        },
    )

    assert response.status_code == 422
    service.assert_not_called()


def test_create_lab_test_rejects_unknown_field(
    client,
    lab_admin_headers,
):
    response = client.post(
        "/api/v1/lab/tests",
        headers=lab_admin_headers,
        json={
            "name": "CBC",
            "unknown_field": "bad",
        },
    )

    assert response.status_code == 422


def test_create_lab_test_rejects_invalid_critical_range(
    client,
    lab_admin_headers,
):
    response = client.post(
        "/api/v1/lab/tests",
        headers=lab_admin_headers,
        json={
            "name": "CBC",
            "critical_low": "20",
            "critical_high": "10",
        },
    )

    assert response.status_code == 422


def test_create_lab_test_rejects_negative_price(
    client,
    lab_admin_headers,
):
    response = client.post(
        "/api/v1/lab/tests",
        headers=lab_admin_headers,
        json={
            "name": "CBC",
            "price": "-1",
        },
    )

    assert response.status_code == 422


def test_list_lab_tests_success(
    client,
    lab_admin_headers,
    lab_admin_staff,
    monkeypatch,
    lab_routes,
):
    page = make_page(
        [
            make_lab_test(clinic_id=None),
            make_lab_test(
                id=2,
                clinic_id=lab_admin_staff.clinic_id,
            ),
        ],
        total=2,
    )

    service = Mock(return_value=page)

    monkeypatch.setattr(
        lab_routes,
        "list_lab_tests",
        service,
    )

    response = client.get(
        "/api/v1/lab/tests",
        headers=lab_admin_headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["total"] == 2
    assert body["data"]["page"] == 1
    assert body["data"]["per_page"] == 50
    assert len(body["data"]["items"]) == 2

    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_admin_staff.clinic_id
    )
    assert (
        service.call_args.kwargs["active_only"]
        is True
    )
    assert service.call_args.kwargs["page"] == 1
    assert service.call_args.kwargs["per_page"] == 50


def test_list_lab_tests_forwards_filters_and_pagination(
    client,
    lab_admin_headers,
    lab_admin_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_page(
            [
                make_lab_test(
                    clinic_id=lab_admin_staff.clinic_id
                )
            ],
            total=21,
            page=2,
            per_page=10,
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "list_lab_tests",
        service,
    )

    response = client.get(
        "/api/v1/lab/tests?active_only=false&page=2&per_page=10",
        headers=lab_admin_headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["data"]["page"] == 2
    assert body["data"]["per_page"] == 10
    assert body["data"]["total"] == 21

    assert (
        service.call_args.kwargs["active_only"]
        is False
    )
    assert service.call_args.kwargs["page"] == 2
    assert service.call_args.kwargs["per_page"] == 10


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "per_page=0",
        "per_page=501",
        "per_page=-1",
    ],
)
def test_list_lab_tests_rejects_invalid_pagination(
    client,
    lab_admin_headers,
    query,
):
    response = client.get(
        f"/api/v1/lab/tests?{query}",
        headers=lab_admin_headers,
    )

    assert response.status_code == 422


def test_list_lab_tests_rejects_unknown_query(
    client,
    lab_admin_headers,
):
    response = client.get(
        "/api/v1/lab/tests?unknown=x",
        headers=lab_admin_headers,
    )

    assert response.status_code == 422


def test_get_lab_test_success(
    client,
    lab_admin_headers,
    lab_admin_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_test()
    )

    monkeypatch.setattr(
        lab_routes,
        "get_lab_test",
        service,
    )

    response = client.get(
        "/api/v1/lab/tests/1",
        headers=lab_admin_headers,
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["name"] == "CBC"
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_admin_staff.clinic_id
    )


def test_get_lab_test_maps_not_found(
    client,
    lab_admin_headers,
    monkeypatch,
    lab_routes,
):
    monkeypatch.setattr(
        lab_routes,
        "get_lab_test",
        Mock(
            side_effect=NotFoundError(
                "Lab test not found"
            )
        ),
    )

    response = client.get(
        "/api/v1/lab/tests/999",
        headers=lab_admin_headers,
    )

    assert response.status_code == 404
    assert (
        response.get_json()["error"]
        == "Lab test not found"
    )


def test_update_lab_test_success(
    client,
    lab_admin_headers,
    lab_admin_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_test(
            name="Updated CBC"
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "update_lab_test",
        service,
    )

    response = client.patch(
        "/api/v1/lab/tests/1",
        headers=lab_admin_headers,
        json={
            "name": "Updated CBC",
        },
    )

    assert response.status_code == 200
    assert (
        service.call_args.kwargs["name"]
        == "Updated CBC"
    )
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_admin_staff.clinic_id
    )


def test_update_lab_test_allows_partial_fields(
    client,
    lab_admin_headers,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_test()
    )

    monkeypatch.setattr(
        lab_routes,
        "update_lab_test",
        service,
    )

    response = client.patch(
        "/api/v1/lab/tests/1",
        headers=lab_admin_headers,
        json={
            "critical_low": "3.000",
        },
    )

    assert response.status_code == 200
    assert (
        service.call_args.kwargs["critical_low"]
        == Decimal("3.000")
    )


def test_update_lab_test_rejects_client_clinic(
    client,
    lab_admin_headers,
):
    response = client.patch(
        "/api/v1/lab/tests/1",
        headers=lab_admin_headers,
        json={
            "name": "Updated",
            "clinic_id": 999,
        },
    )

    assert response.status_code == 422


def test_create_lab_order_success(
    client,
    lab_doctor_headers,
    lab_doctor_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_order(
            clinic_id=lab_doctor_staff.clinic_id
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "create_lab_order",
        service,
    )

    response = client.post(
        "/api/v1/lab/orders",
        headers=lab_doctor_headers,
        json={
            "patient_id": 10,
            "test_ids": [1, 2],
            "consultation_id": 5,
        },
    )

    assert response.status_code == 201
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_doctor_staff.clinic_id
    )
    assert (
        service.call_args.kwargs["ordered_by_id"]
        == lab_doctor_staff.id
    )
    assert service.call_args.kwargs["patient_id"] == 10
    assert service.call_args.kwargs["test_ids"] == [1, 2]


def test_create_lab_order_rejects_client_clinic(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders",
        headers=lab_doctor_headers,
        json={
            "patient_id": 10,
            "test_ids": [1],
            "clinic_id": 999,
        },
    )

    assert response.status_code == 422


def test_create_lab_order_rejects_client_actor(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders",
        headers=lab_doctor_headers,
        json={
            "patient_id": 10,
            "test_ids": [1],
            "ordered_by_id": 999,
        },
    )

    assert response.status_code == 422


def test_create_lab_order_rejects_duplicate_test_ids(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders",
        headers=lab_doctor_headers,
        json={
            "patient_id": 10,
            "test_ids": [1, 1],
        },
    )

    assert response.status_code == 422


def test_create_lab_order_rejects_invalid_test_id(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders",
        headers=lab_doctor_headers,
        json={
            "patient_id": 10,
            "test_ids": [0],
        },
    )

    assert response.status_code == 422


def test_get_lab_order_success(
    client,
    lab_admin_headers,
    lab_admin_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_order()
    )

    monkeypatch.setattr(
        lab_routes,
        "get_lab_order",
        service,
    )

    response = client.get(
        "/api/v1/lab/orders/1",
        headers=lab_admin_headers,
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["id"] == 1
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_admin_staff.clinic_id
    )


def test_list_orders_for_patient_success(
    client,
    lab_admin_headers,
    lab_admin_staff,
    monkeypatch,
    lab_routes,
):
    page = make_page(
        [make_lab_order()],
        total=1,
    )

    service = Mock(return_value=page)

    monkeypatch.setattr(
        lab_routes,
        "list_orders_for_patient",
        service,
    )

    response = client.get(
        "/api/v1/lab/orders?patient_id=10",
        headers=lab_admin_headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["total"] == 1
    assert body["data"]["page"] == 1
    assert body["data"]["per_page"] == 50
    assert body["data"]["items"][0]["patient_id"] == 10

    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_admin_staff.clinic_id
    )
    assert service.call_args.kwargs["patient_id"] == 10
    assert service.call_args.kwargs["page"] == 1
    assert service.call_args.kwargs["per_page"] == 50


def test_list_orders_for_patient_forwards_pagination(
    client,
    lab_admin_headers,
    lab_admin_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_page(
            [make_lab_order()],
            total=25,
            page=3,
            per_page=10,
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "list_orders_for_patient",
        service,
    )

    response = client.get(
        "/api/v1/lab/orders?patient_id=10&page=3&per_page=10",
        headers=lab_admin_headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["data"]["page"] == 3
    assert body["data"]["per_page"] == 10
    assert body["data"]["total"] == 25

    assert service.call_args.kwargs["patient_id"] == 10
    assert service.call_args.kwargs["page"] == 3
    assert service.call_args.kwargs["per_page"] == 10


@pytest.mark.parametrize(
    "query",
    [
        "patient_id=10&page=0",
        "patient_id=10&page=-1",
        "patient_id=10&per_page=0",
        "patient_id=10&per_page=501",
    ],
)
def test_list_orders_for_patient_rejects_invalid_pagination(
    client,
    lab_admin_headers,
    query,
):
    response = client.get(
        f"/api/v1/lab/orders?{query}",
        headers=lab_admin_headers,
    )

    assert response.status_code == 422


def test_list_orders_for_patient_requires_patient_id(
    client,
    lab_admin_headers,
):
    response = client.get(
        "/api/v1/lab/orders",
        headers=lab_admin_headers,
    )

    assert response.status_code == 422


def test_list_orders_for_patient_rejects_unknown_query(
    client,
    lab_admin_headers,
):
    response = client.get(
        "/api/v1/lab/orders?patient_id=10&unknown=x",
        headers=lab_admin_headers,
    )

    assert response.status_code == 422


def test_get_lab_order_maps_not_found(
    client,
    lab_admin_headers,
    monkeypatch,
    lab_routes,
):
    monkeypatch.setattr(
        lab_routes,
        "get_lab_order",
        Mock(
            side_effect=NotFoundError(
                "Order not found"
            )
        ),
    )

    response = client.get(
        "/api/v1/lab/orders/999",
        headers=lab_admin_headers,
    )

    assert response.status_code == 404
    assert (
        response.get_json()["error"]
        == "Order not found"
    )


def test_collect_sample_success(
    client,
    lab_technician_headers,
    lab_technician_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_order(
            status=LabOrderStatus.SAMPLE_COLLECTED,
            collected_by_id=lab_technician_staff.id,
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "collect_sample",
        service,
    )

    response = client.post(
        "/api/v1/lab/orders/1/collect-sample",
        headers=lab_technician_headers,
        json={
            "scanned_qr_code": "LAB-123",
        },
    )

    assert response.status_code == 200
    assert (
        service.call_args.kwargs["collected_by_id"]
        == lab_technician_staff.id
    )
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_technician_staff.clinic_id
    )
    assert (
        service.call_args.kwargs["scanned_qr_code"]
        == "LAB-123"
    )


def test_collect_sample_rejects_client_actor(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/collect-sample",
        headers=lab_technician_headers,
        json={
            "scanned_qr_code": "LAB-123",
            "collected_by_id": 999,
        },
    )

    assert response.status_code == 422


def test_collect_sample_accepts_empty_body(
    client,
    lab_technician_headers,
    monkeypatch,
    lab_routes,
):
    monkeypatch.setattr(
        lab_routes,
        "collect_sample",
        Mock(return_value=make_lab_order()),
    )

    response = client.post(
        "/api/v1/lab/orders/1/collect-sample",
        headers=lab_technician_headers,
        json={},
    )

    assert response.status_code == 200


def test_collect_sample_maps_conflict(
    client,
    lab_technician_headers,
    monkeypatch,
    lab_routes,
):
    monkeypatch.setattr(
        lab_routes,
        "collect_sample",
        Mock(
            side_effect=ConflictError(
                "Scanned QR code does not match"
            )
        ),
    )

    response = client.post(
        "/api/v1/lab/orders/1/collect-sample",
        headers=lab_technician_headers,
        json={
            "scanned_qr_code": "BAD",
        },
    )

    assert response.status_code == 409
    assert (
        response.get_json()["error"]
        == "Scanned QR code does not match"
    )


def test_link_equipment_success(
    client,
    lab_technician_headers,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_order(
            equipment_reference_id="EQ-100",
            status=LabOrderStatus.IN_PROGRESS,
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "link_equipment",
        service,
    )

    response = client.post(
        "/api/v1/lab/orders/1/equipment",
        headers=lab_technician_headers,
        json={
            "equipment_reference_id": "EQ-100",
        },
    )

    assert response.status_code == 200
    assert (
        service.call_args.kwargs["equipment_reference_id"]
        == "EQ-100"
    )


def test_link_equipment_rejects_missing_reference(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/equipment",
        headers=lab_technician_headers,
        json={},
    )

    assert response.status_code == 422


def test_link_equipment_rejects_unknown_field(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/equipment",
        headers=lab_technician_headers,
        json={
            "equipment_reference_id": "EQ-100",
            "processed_by_id": 999,
        },
    )

    assert response.status_code == 422


def test_process_sample_success(
    client,
    lab_technician_headers,
    lab_technician_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_order(
            status=LabOrderStatus.IN_PROGRESS,
            processed_by_id=lab_technician_staff.id,
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "process_sample",
        service,
    )

    response = client.post(
        "/api/v1/lab/orders/1/process",
        headers=lab_technician_headers,
        json={
            "equipment_reference_id": "EQ-100",
        },
    )

    assert response.status_code == 200
    assert (
        service.call_args.kwargs["processed_by_id"]
        == lab_technician_staff.id
    )
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_technician_staff.clinic_id
    )
    assert (
        service.call_args.kwargs["equipment_reference_id"]
        == "EQ-100"
    )


def test_process_sample_rejects_client_actor(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/process",
        headers=lab_technician_headers,
        json={
            "equipment_reference_id": "EQ-100",
            "processed_by_id": 999,
        },
    )

    assert response.status_code == 422


def test_enter_result_success(
    client,
    lab_technician_headers,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_item(
            result_value="10",
            flag=LabResultFlag.NORMAL,
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "enter_result",
        service,
    )

    response = client.post(
        "/api/v1/lab/order-items/1/result",
        headers=lab_technician_headers,
        json={
            "result_value": "10",
            "flag": "critical",
            "result_notes": "Normal",
            "result_file_url": "report.pdf",
        },
    )

    assert response.status_code == 200
    assert (
        service.call_args.kwargs["result_value"]
        == "10"
    )
    assert (
        service.call_args.kwargs["flag"]
        == LabResultFlag.CRITICAL
    )
    assert (
        service.call_args.kwargs["result_notes"]
        == "Normal"
    )
    assert (
        service.call_args.kwargs["result_file_url"]
        == "report.pdf"
    )


def test_enter_result_rejects_client_actor(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/order-items/1/result",
        headers=lab_technician_headers,
        json={
            "result_value": "10",
            "performed_by_id": 999,
        },
    )

    assert response.status_code == 422


def test_enter_result_rejects_empty_value(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/order-items/1/result",
        headers=lab_technician_headers,
        json={
            "result_value": "",
        },
    )

    assert response.status_code == 422


def test_enter_result_rejects_unknown_field(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/order-items/1/result",
        headers=lab_technician_headers,
        json={
            "result_value": "10",
            "actor_id": 999,
        },
    )

    assert response.status_code == 422


def test_enter_result_maps_conflict(
    client,
    lab_technician_headers,
    monkeypatch,
    lab_routes,
):
    monkeypatch.setattr(
        lab_routes,
        "enter_result",
        Mock(
            side_effect=ConflictError(
                "Cannot modify a result after verification"
            )
        ),
    )

    response = client.post(
        "/api/v1/lab/order-items/1/result",
        headers=lab_technician_headers,
        json={
            "result_value": "10",
        },
    )

    assert response.status_code == 409


def test_verify_results_success(
    client,
    lab_technician_headers,
    lab_technician_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_order(
            verified_by_id=lab_technician_staff.id,
            verified_at=datetime.now(timezone.utc),
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "verify_results",
        service,
    )

    response = client.post(
        "/api/v1/lab/orders/1/verify",
        headers=lab_technician_headers,
        json={},
    )

    assert response.status_code == 200
    assert (
        service.call_args.kwargs["verified_by_id"]
        == lab_technician_staff.id
    )
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_technician_staff.clinic_id
    )


def test_verify_results_rejects_client_actor(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/verify",
        headers=lab_technician_headers,
        json={
            "verified_by_id": 999,
        },
    )

    assert response.status_code == 422


def test_verify_results_accepts_empty_body(
    client,
    lab_technician_headers,
    monkeypatch,
    lab_routes,
):
    monkeypatch.setattr(
        lab_routes,
        "verify_results",
        Mock(return_value=make_lab_order()),
    )

    response = client.post(
        "/api/v1/lab/orders/1/verify",
        headers=lab_technician_headers,
        json={},
    )

    assert response.status_code == 200


def test_complete_order_success(
    client,
    lab_technician_headers,
    lab_technician_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_order(
            status=LabOrderStatus.COMPLETED,
            verified_by_id=lab_technician_staff.id,
            verified_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "complete_order",
        service,
    )

    response = client.post(
        "/api/v1/lab/orders/1/complete",
        headers=lab_technician_headers,
    )

    assert response.status_code == 200
    assert (
        response.get_json()["data"]["status"]
        == LabOrderStatus.COMPLETED.value
    )
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_technician_staff.clinic_id
    )


def test_complete_order_does_not_accept_actor_id(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/complete",
        headers=lab_technician_headers,
        json={
            "verified_by_id": 999,
        },
    )

    assert response.status_code != 422


def test_cancel_order_success(
    client,
    lab_doctor_headers,
    lab_doctor_staff,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_order(
            status=LabOrderStatus.CANCELLED,
            cancellation_reason="Patient request",
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "cancel_order",
        service,
    )

    response = client.post(
        "/api/v1/lab/orders/1/cancel",
        headers=lab_doctor_headers,
        json={
            "reason": "Patient request",
        },
    )

    assert response.status_code == 200
    assert (
        service.call_args.kwargs["cancelled_by_id"]
        == lab_doctor_staff.id
    )
    assert (
        service.call_args.kwargs["clinic_id"]
        == lab_doctor_staff.clinic_id
    )
    assert (
        service.call_args.kwargs["reason"]
        == "Patient request"
    )


def test_cancel_order_rejects_client_actor(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/cancel",
        headers=lab_doctor_headers,
        json={
            "reason": "Cancel",
            "cancelled_by_id": 999,
        },
    )

    assert response.status_code == 422


def test_cancel_order_allows_empty_body(
    client,
    lab_doctor_headers,
    monkeypatch,
    lab_routes,
):
    monkeypatch.setattr(
        lab_routes,
        "cancel_order",
        Mock(
            return_value=make_lab_order(
                status=LabOrderStatus.CANCELLED
            )
        ),
    )

    response = client.post(
        "/api/v1/lab/orders/1/cancel",
        headers=lab_doctor_headers,
        json={},
    )

    assert response.status_code == 200


def test_cancel_order_rejects_long_reason(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/cancel",
        headers=lab_doctor_headers,
        json={
            "reason": "X" * 256,
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "exception,status_code",
    [
        (NotFoundError("not found"), 404),
        (ConflictError("conflict"), 409),
        (ValidationError("validation"), 422),
    ],
)
def test_get_lab_order_maps_domain_errors(
    client,
    lab_admin_headers,
    monkeypatch,
    lab_routes,
    exception,
    status_code,
):
    monkeypatch.setattr(
        lab_routes,
        "get_lab_order",
        Mock(side_effect=exception),
    )

    response = client.get(
        "/api/v1/lab/orders/1",
        headers=lab_admin_headers,
    )

    assert response.status_code == status_code
    assert (
        response.get_json()["error"]
        == str(exception)
    )
    assert response.get_json()["success"] is False


def test_unexpected_exception_is_not_exposed(
    client,
    lab_admin_headers,
    monkeypatch,
    lab_routes,
):
    monkeypatch.setattr(
        lab_routes,
        "get_lab_order",
        Mock(
            side_effect=RuntimeError(
                "SECRET INTERNAL DETAIL"
            )
        ),
    )

    response = client.get(
        "/api/v1/lab/orders/1",
        headers=lab_admin_headers,
    )

    assert response.status_code == 400

    body = response.get_json()

    assert body["success"] is False
    assert (
        body["error"]
        == "An unexpected error occurred"
    )
    assert "SECRET INTERNAL DETAIL" not in response.text


def test_pydantic_validation_returns_consistent_shape(
    client,
    lab_admin_headers,
):
    response = client.post(
        "/api/v1/lab/tests",
        headers=lab_admin_headers,
        json={
            "name": "",
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Validation failed"
    assert isinstance(body["details"], list)
    assert body["details"]


def test_create_order_has_no_client_controlled_staff_identity(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders",
        headers=lab_doctor_headers,
        json={
            "patient_id": 10,
            "test_ids": [1],
            "ordered_by_id": 999999,
        },
    )

    assert response.status_code == 422


def test_collect_sample_has_no_client_controlled_staff_identity(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/collect-sample",
        headers=lab_technician_headers,
        json={
            "collected_by_id": 999999,
        },
    )

    assert response.status_code == 422


def test_process_sample_has_no_client_controlled_staff_identity(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/process",
        headers=lab_technician_headers,
        json={
            "processed_by_id": 999999,
        },
    )

    assert response.status_code == 422


def test_verify_results_has_no_client_controlled_staff_identity(
    client,
    lab_technician_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/verify",
        headers=lab_technician_headers,
        json={
            "verified_by_id": 999999,
        },
    )

    assert response.status_code == 422


def test_cancel_order_has_no_client_controlled_staff_identity(
    client,
    lab_doctor_headers,
):
    response = client.post(
        "/api/v1/lab/orders/1/cancel",
        headers=lab_doctor_headers,
        json={
            "cancelled_by_id": 999999,
        },
    )

    assert response.status_code == 422


def test_create_lab_test_passes_all_schema_fields(
    client,
    lab_admin_headers,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_test()
    )

    monkeypatch.setattr(
        lab_routes,
        "create_lab_test",
        service,
    )

    response = client.post(
        "/api/v1/lab/tests",
        headers=lab_admin_headers,
        json={
            "name": "CBC",
            "loinc_code": "12345-6",
            "code": "CBC",
            "sample_type": "blood",
            "reference_range": "5 - 20",
            "unit": "mg/dL",
            "price": "25.00",
            "critical_low": "2.000",
            "critical_high": "40.000",
            "is_active": True,
        },
    )

    assert response.status_code == 201

    kwargs = service.call_args.kwargs

    assert kwargs["name"] == "CBC"
    assert kwargs["loinc_code"] == "12345-6"
    assert kwargs["code"] == "CBC"
    assert kwargs["sample_type"] == SampleType.BLOOD
    assert kwargs["reference_range"] == "5 - 20"
    assert kwargs["unit"] == "mg/dL"
    assert kwargs["price"] == Decimal("25.00")
    assert kwargs["critical_low"] == Decimal("2.000")
    assert kwargs["critical_high"] == Decimal("40.000")
    assert kwargs["is_active"] is True


def test_update_lab_test_uses_exclude_unset(
    client,
    lab_admin_headers,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_test()
    )

    monkeypatch.setattr(
        lab_routes,
        "update_lab_test",
        service,
    )

    response = client.patch(
        "/api/v1/lab/tests/1",
        headers=lab_admin_headers,
        json={
            "name": "Updated CBC",
        },
    )

    assert response.status_code == 200

    kwargs = service.call_args.kwargs

    assert kwargs["name"] == "Updated CBC"
    assert "price" not in kwargs
    assert "critical_low" not in kwargs
    assert "critical_high" not in kwargs


def test_enter_result_converts_enum_input(
    client,
    lab_technician_headers,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_item()
    )

    monkeypatch.setattr(
        lab_routes,
        "enter_result",
        service,
    )

    response = client.post(
        "/api/v1/lab/order-items/1/result",
        headers=lab_technician_headers,
        json={
            "result_value": "10",
            "flag": "critical",
        },
    )

    assert response.status_code == 200
    assert (
        service.call_args.kwargs["flag"]
        == LabResultFlag.CRITICAL
    )


def test_cancel_order_passes_optional_reason(
    client,
    lab_doctor_headers,
    monkeypatch,
    lab_routes,
):
    service = Mock(
        return_value=make_lab_order(
            status=LabOrderStatus.CANCELLED
        )
    )

    monkeypatch.setattr(
        lab_routes,
        "cancel_order",
        service,
    )

    response = client.post(
        "/api/v1/lab/orders/1/cancel",
        headers=lab_doctor_headers,
        json={},
    )

    assert response.status_code == 200
    assert service.call_args.kwargs["reason"] is None
