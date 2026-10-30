from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from app.core.enums.prescription_enums import (
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.modules.prescription.routes import (
    prescription_routes,
)


def _headers(
    make_authenticated_staff,
    clinic,
):
    staff, headers = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )

    result = dict(
        headers
    )

    result[
        "Content-Type"
    ] = "application/json"

    return result, staff


def _legacy_item(
    *,
    item_id=10,
    prescription_id=1,
    drug_id=7,
):
    return SimpleNamespace(
        id=item_id,
        prescription_id=prescription_id,
        drug_id=drug_id,
        dosage="500 mg",
        frequency="twice daily",
        duration="7 days",
        quantity=14,
        instructions="Take after meals",
    )


def _structured_item(
    *,
    item_id=10,
    prescription_id=1,
    drug_id=7,
):
    return SimpleNamespace(
        id=item_id,
        prescription_id=prescription_id,
        drug_id=drug_id,
        dosage="500 mg",
        frequency="every 8 hours",
        duration="7 days",
        quantity=21,
        instructions="Take after meals",
        dose_value=Decimal(
            "500.0000"
        ),
        dose_unit="mg",
        route_code="oral",
        route_display="Oral",
        frequency_code="q8h",
        frequency_interval=8,
        frequency_interval_unit="hour",
        scheduled_times=[
            "06:00",
            "14:00",
            "22:00",
        ],
        start_at=datetime(
            2026,
            10,
            30,
            7,
            0,
            tzinfo=timezone.utc,
        ),
        end_at=datetime(
            2026,
            11,
            6,
            7,
            0,
            tzinfo=timezone.utc,
        ),
        is_prn=False,
        prn_reason=None,
        max_single_dose=Decimal(
            "500.0000"
        ),
        max_daily_dose=Decimal(
            "1500.0000"
        ),
        administration_instructions=(
            "Give after food"
        ),
        order_priority="urgent",
        order_type="inpatient",
        ordered_at=datetime(
            2026,
            10,
            30,
            6,
            59,
            tzinfo=timezone.utc,
        ),
    )


def _prescription(
    *,
    clinic_id,
    prescribed_by_id,
    item,
):
    return SimpleNamespace(
        id=1,
        clinic_id=clinic_id,
        patient_id=2,
        consultation_id=None,
        prescribed_by_id=prescribed_by_id,
        status=PrescriptionStatus.ACTIVE,
        notes=None,
        issued_at=datetime(
            2026,
            10,
            30,
            6,
            59,
            tzinfo=timezone.utc,
        ),
        expires_at=None,
        items=[
            item
        ],
    )


def test_legacy_serializer_is_backward_compatible():
    payload = (
        prescription_routes
        ._serialize_prescription_item(
            _legacy_item()
        )
    )

    assert (
        payload["dosage"]
        == "500 mg"
    )
    assert (
        payload["dose_value"]
        is None
    )
    assert (
        payload["dose_unit"]
        is None
    )
    assert (
        payload["scheduled_times"]
        == []
    )
    assert (
        payload["is_prn"]
        is False
    )
    assert (
        payload["order_priority"]
        == "routine"
    )
    assert (
        payload["order_type"]
        == "outpatient"
    )
    assert (
        payload["ordered_at"]
        is None
    )


def test_legacy_create_route_preserves_old_item_payload_shape(
    client,
    clinic,
    make_authenticated_staff,
    monkeypatch,
):
    headers, staff = _headers(
        make_authenticated_staff,
        clinic,
    )

    prescription = _prescription(
        clinic_id=clinic.id,
        prescribed_by_id=staff.id,
        item=_legacy_item(),
    )

    create_mock = Mock(
        return_value=(
            prescription,
            [],
        )
    )

    monkeypatch.setattr(
        prescription_routes,
        "create_prescription",
        create_mock,
    )

    response = client.post(
        "/api/v1/prescriptions",
        json={
            "patient_id": 2,
            "items": [
                {
                    "drug_id": 7,
                    "dosage": "500 mg",
                    "frequency": "twice daily",
                    "duration": "7 days",
                    "quantity": 14,
                    "instructions": (
                        "Take after meals"
                    ),
                }
            ],
        },
        headers=headers,
    )

    assert (
        response.status_code
        == 201
    )

    kwargs = (
        create_mock
        .call_args
        .kwargs
    )

    assert kwargs["items"] == [
        {
            "drug_id": 7,
            "dosage": "500 mg",
            "frequency": "twice daily",
            "duration": "7 days",
            "quantity": 14,
            "instructions": (
                "Take after meals"
            ),
        }
    ]


def test_structured_create_route_forwards_and_serializes_contract(
    client,
    clinic,
    make_authenticated_staff,
    monkeypatch,
):
    headers, staff = _headers(
        make_authenticated_staff,
        clinic,
    )

    structured_item = (
        _structured_item()
    )

    prescription = _prescription(
        clinic_id=clinic.id,
        prescribed_by_id=staff.id,
        item=structured_item,
    )

    create_mock = Mock(
        return_value=(
            prescription,
            [],
        )
    )

    monkeypatch.setattr(
        prescription_routes,
        "create_prescription",
        create_mock,
    )

    response = client.post(
        "/api/v1/prescriptions",
        json={
            "patient_id": 2,
            "items": [
                {
                    "drug_id": 7,
                    "dosage": "500 mg",
                    "frequency": "every 8 hours",
                    "duration": "7 days",
                    "quantity": 21,
                    "instructions": (
                        "Take after meals"
                    ),
                    "dose_value": 500,
                    "dose_unit": "mg",
                    "route_code": "oral",
                    "route_display": "Oral",
                    "frequency_code": "q8h",
                    "frequency_interval": 8,
                    "frequency_interval_unit": (
                        "hour"
                    ),
                    "scheduled_times": [
                        "06:00",
                        "14:00",
                        "22:00",
                    ],
                    "start_at": (
                        "2026-10-30T08:00:00+01:00"
                    ),
                    "end_at": (
                        "2026-11-06T08:00:00+01:00"
                    ),
                    "max_single_dose": 500,
                    "max_daily_dose": 1500,
                    "administration_instructions": (
                        "Give after food"
                    ),
                    "order_priority": "urgent",
                    "order_type": "inpatient",
                }
            ],
        },
        headers=headers,
    )

    assert (
        response.status_code
        == 201
    )

    kwargs = (
        create_mock
        .call_args
        .kwargs
    )

    item = (
        kwargs["items"][0]
    )

    assert (
        item["dose_value"]
        == 500
    )
    assert (
        item["dose_unit"]
        == "mg"
    )
    assert (
        item["route_code"]
        == "oral"
    )
    assert (
        item["frequency_interval"]
        == 8
    )
    assert (
        item["frequency_interval_unit"]
        == "hour"
    )
    assert (
        item["scheduled_times"]
        == [
            "06:00",
            "14:00",
            "22:00",
        ]
    )
    assert (
        item["order_priority"]
        == "urgent"
    )
    assert (
        item["order_type"]
        == "inpatient"
    )

    assert (
        item["start_at"]
        == datetime(
            2026,
            10,
            30,
            7,
            0,
            tzinfo=timezone.utc,
        )
    )

    body = (
        response.get_json()
    )

    response_item = (
        body["data"]["items"][0]
    )

    assert (
        response_item["dose_value"]
        == 500.0
    )
    assert (
        response_item["dose_unit"]
        == "mg"
    )
    assert (
        response_item["route_code"]
        == "oral"
    )
    assert (
        response_item["frequency_interval"]
        == 8
    )
    assert (
        response_item["scheduled_times"]
        == [
            "06:00",
            "14:00",
            "22:00",
        ]
    )
    assert (
        response_item["max_daily_dose"]
        == 1500.0
    )
    assert (
        response_item["order_priority"]
        == "urgent"
    )
    assert (
        response_item["order_type"]
        == "inpatient"
    )
    assert (
        response_item["ordered_at"]
        == "2026-10-30T06:59:00+00:00"
    )


def test_route_rejects_client_controlled_ordered_at(
    client,
    clinic,
    make_authenticated_staff,
    monkeypatch,
):
    headers, _ = _headers(
        make_authenticated_staff,
        clinic,
    )

    create_mock = Mock()

    monkeypatch.setattr(
        prescription_routes,
        "create_prescription",
        create_mock,
    )

    response = client.post(
        "/api/v1/prescriptions",
        json={
            "patient_id": 2,
            "items": [
                {
                    "drug_id": 7,
                    "ordered_at": (
                        "2026-10-30T08:00:00+01:00"
                    ),
                }
            ],
        },
        headers=headers,
    )

    assert (
        response.status_code
        == 422
    )

    create_mock.assert_not_called()


def test_route_rejects_prn_without_reason(
    client,
    clinic,
    make_authenticated_staff,
    monkeypatch,
):
    headers, _ = _headers(
        make_authenticated_staff,
        clinic,
    )

    create_mock = Mock()

    monkeypatch.setattr(
        prescription_routes,
        "create_prescription",
        create_mock,
    )

    response = client.post(
        "/api/v1/prescriptions",
        json={
            "patient_id": 2,
            "items": [
                {
                    "drug_id": 7,
                    "is_prn": True,
                }
            ],
        },
        headers=headers,
    )

    assert (
        response.status_code
        == 422
    )

    create_mock.assert_not_called()


def test_route_accepts_valid_prn_contract(
    client,
    clinic,
    make_authenticated_staff,
    monkeypatch,
):
    headers, staff = _headers(
        make_authenticated_staff,
        clinic,
    )

    item = _structured_item()

    item.is_prn = True
    item.prn_reason = (
        "Breakthrough pain"
    )
    item.scheduled_times = []

    prescription = _prescription(
        clinic_id=clinic.id,
        prescribed_by_id=staff.id,
        item=item,
    )

    create_mock = Mock(
        return_value=(
            prescription,
            [],
        )
    )

    monkeypatch.setattr(
        prescription_routes,
        "create_prescription",
        create_mock,
    )

    response = client.post(
        "/api/v1/prescriptions",
        json={
            "patient_id": 2,
            "items": [
                {
                    "drug_id": 7,
                    "dose_value": 500,
                    "dose_unit": "mg",
                    "route_code": "oral",
                    "is_prn": True,
                    "prn_reason": (
                        "Breakthrough pain"
                    ),
                }
            ],
        },
        headers=headers,
    )

    assert (
        response.status_code
        == 201
    )

    sent = (
        create_mock
        .call_args
        .kwargs["items"][0]
    )

    assert (
        sent["is_prn"]
        is True
    )
    assert (
        sent["prn_reason"]
        == "Breakthrough pain"
    )
    assert (
        "scheduled_times"
        not in sent
    )
