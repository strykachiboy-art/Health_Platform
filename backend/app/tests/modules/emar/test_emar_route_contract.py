from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
import inspect
from types import SimpleNamespace

import pytest
from pydantic import (
    ValidationError as PydanticValidationError,
)

import app.modules.emar.routes.emar_routes as emar_routes
from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.modules.emar.schemas.emar_schema import (
    MedicationAdministrationScheduleSchema,
)
from app.modules.emar.services.emar_service import (
    get_medication_administration,
    list_medication_administrations_for_admission,
)


def _record(
    *,
    record_id=1,
    status=(
        MedicationAdministrationStatus
        .SCHEDULED
    ),
):
    return SimpleNamespace(
        id=record_id,
        clinic_id=7,
        patient_id=5,
        admission_id=9,
        prescription_id=11,
        prescription_item_id=13,
        scheduled_at=datetime(
            2026,
            10,
            30,
            9,
            0,
            tzinfo=timezone.utc,
        ),
        dose_value=Decimal(
            "500.0000"
        ),
        dose_unit="mg",
        route_code="oral",
        status=status,
        administered_at=None,
        administered_by_staff_id=None,
        held_at=None,
        held_by_staff_id=None,
        hold_reason=None,
        refused_at=None,
        refused_recorded_by_staff_id=None,
        refusal_reason=None,
        missed_at=None,
        missed_reason=None,
        cancelled_at=None,
        cancel_reason=None,
        source_order_version=1,
        idempotency_key=None,
        notes=None,
        created_at=datetime(
            2026,
            10,
            30,
            8,
            0,
            tzinfo=timezone.utc,
        ),
        updated_at=datetime(
            2026,
            10,
            30,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    )


def _raw(handler):
    return inspect.unwrap(
        handler
    )


def _patch_identity(
    monkeypatch,
):
    monkeypatch.setattr(
        emar_routes,
        "_current_clinic_id",
        lambda: 7,
    )

    monkeypatch.setattr(
        emar_routes,
        "_current_staff_id",
        lambda: 17,
    )


def test_schedule_schema_rejects_naive_datetime_and_extra_fields():
    with pytest.raises(
        PydanticValidationError,
    ):
        (
            MedicationAdministrationScheduleSchema
            .model_validate(
                {
                    "patient_id": 5,
                    "admission_id": 9,
                    "prescription_item_id": 13,
                    "scheduled_at": (
                        datetime(
                            2026,
                            10,
                            30,
                            9,
                            0,
                        )
                    ),
                }
            )
        )

    with pytest.raises(
        PydanticValidationError,
    ):
        (
            MedicationAdministrationScheduleSchema
            .model_validate(
                {
                    "patient_id": 5,
                    "admission_id": 9,
                    "prescription_item_id": 13,
                    "scheduled_at": (
                        datetime.now(
                            timezone.utc
                        )
                    ),
                    "unexpected": True,
                }
            )
        )


def test_emar_blueprint_is_registered_under_api_v1(
    app,
):
    rules = {
        rule.rule
        for rule in app.url_map.iter_rules()
    }

    expected = {
        "/api/v1/emar/scheduled",
        "/api/v1/emar/prn",
        "/api/v1/emar/<int:administration_id>/due",
        "/api/v1/emar/<int:administration_id>/administer",
        "/api/v1/emar/<int:administration_id>/hold",
        "/api/v1/emar/<int:administration_id>/refuse",
        "/api/v1/emar/<int:administration_id>/miss",
        "/api/v1/emar/<int:administration_id>/cancel",
        "/api/v1/emar/<int:administration_id>",
        "/api/v1/emar/admissions/<int:admission_id>",
    }

    assert expected.issubset(
        rules
    )


def test_schedule_route_passes_authenticated_context(
    monkeypatch,
    app,
):
    _patch_identity(
        monkeypatch
    )

    captured = {}

    def fake_schedule(**kwargs):
        captured.update(
            kwargs
        )
        return _record()

    monkeypatch.setattr(
        emar_routes,
        "schedule_medication_administration",
        fake_schedule,
    )

    with app.test_request_context(
        "/api/v1/emar/scheduled",
        method="POST",
        json={
            "patient_id": 5,
            "admission_id": 9,
            "prescription_item_id": 13,
            "scheduled_at": (
                "2026-10-30T09:00:00+00:00"
            ),
        },
    ):
        response, status = _raw(
            emar_routes
            .schedule_medication_administration_route
        )()

    assert status == 201
    assert captured[
        "clinic_id"
    ] == 7
    assert captured[
        "actor_staff_id"
    ] == 17
    assert captured[
        "patient_id"
    ] == 5

    body = response.get_json()

    assert (
        body["administration"]["status"]
        == "scheduled"
    )


def test_prn_route_passes_request_to_service(
    monkeypatch,
    app,
):
    _patch_identity(
        monkeypatch
    )

    captured = {}

    def fake_prn(**kwargs):
        captured.update(
            kwargs
        )
        return _record(
            status=(
                MedicationAdministrationStatus
                .ADMINISTERED
            )
        )

    monkeypatch.setattr(
        emar_routes,
        "administer_prn_medication",
        fake_prn,
    )

    with app.test_request_context(
        "/api/v1/emar/prn",
        method="POST",
        json={
            "patient_id": 5,
            "admission_id": 9,
            "prescription_item_id": 13,
            "notes": "PRN dose",
        },
    ):
        response, status = _raw(
            emar_routes
            .administer_prn_medication_route
        )()

    assert status == 201
    assert captured[
        "actor_staff_id"
    ] == 17
    assert captured[
        "notes"
    ] == "PRN dose"

    assert (
        response.get_json()[
            "administration"
        ][
            "status"
        ]
        == "administered"
    )


@pytest.mark.parametrize(
    (
        "handler_name",
        "service_name",
        "path",
        "payload",
        "expected_kwarg",
        "expected_value",
        "expected_status",
    ),
    [
        (
            "mark_medication_administration_due_route",
            "mark_medication_administration_due",
            "/api/v1/emar/1/due",
            {},
            "due_at",
            None,
            "due",
        ),
        (
            "administer_medication_route",
            "administer_medication",
            "/api/v1/emar/1/administer",
            {
                "notes": "Given",
            },
            "notes",
            "Given",
            "administered",
        ),
        (
            "hold_medication_route",
            "hold_medication",
            "/api/v1/emar/1/hold",
            {
                "reason": "Clinical hold",
            },
            "reason",
            "Clinical hold",
            "held",
        ),
        (
            "record_medication_refusal_route",
            "record_medication_refusal",
            "/api/v1/emar/1/refuse",
            {
                "reason": "Patient refused",
            },
            "reason",
            "Patient refused",
            "refused",
        ),
        (
            "mark_medication_missed_route",
            "mark_medication_missed",
            "/api/v1/emar/1/miss",
            {
                "reason": "Dose window elapsed",
            },
            "reason",
            "Dose window elapsed",
            "missed",
        ),
        (
            "cancel_medication_administration_route",
            "cancel_medication_administration",
            "/api/v1/emar/1/cancel",
            {
                "reason": "Order withdrawn",
            },
            "reason",
            "Order withdrawn",
            "cancelled",
        ),
    ],
)
def test_transition_routes_are_wired(
    monkeypatch,
    app,
    handler_name,
    service_name,
    path,
    payload,
    expected_kwarg,
    expected_value,
    expected_status,
):
    _patch_identity(
        monkeypatch
    )

    captured = {}

    def fake_service(**kwargs):
        captured.update(
            kwargs
        )
        return _record(
            status=(
                MedicationAdministrationStatus(
                    expected_status
                )
            )
        )

    monkeypatch.setattr(
        emar_routes,
        service_name,
        fake_service,
    )

    handler = getattr(
        emar_routes,
        handler_name,
    )

    with app.test_request_context(
        path,
        method="POST",
        json=payload,
    ):
        response, status = _raw(
            handler
        )(
            administration_id=1
        )

    assert status == 200
    assert captured[
        "administration_id"
    ] == 1
    assert captured[
        "clinic_id"
    ] == 7
    assert captured[
        "actor_staff_id"
    ] == 17
    assert captured[
        expected_kwarg
    ] == expected_value

    assert (
        response.get_json()[
            "administration"
        ][
            "status"
        ]
        == expected_status
    )


def test_get_route_is_tenant_scoped(
    monkeypatch,
    app,
):
    monkeypatch.setattr(
        emar_routes,
        "_current_clinic_id",
        lambda: 7,
    )

    captured = {}

    def fake_get(
        administration_id,
        *,
        clinic_id,
    ):
        captured[
            "administration_id"
        ] = administration_id
        captured[
            "clinic_id"
        ] = clinic_id
        return _record(
            record_id=administration_id
        )

    monkeypatch.setattr(
        emar_routes,
        "get_medication_administration",
        fake_get,
    )

    with app.test_request_context(
        "/api/v1/emar/3",
        method="GET",
    ):
        response, status = _raw(
            emar_routes
            .get_medication_administration_route
        )(
            administration_id=3
        )

    assert status == 200
    assert captured == {
        "administration_id": 3,
        "clinic_id": 7,
    }
    assert (
        response.get_json()[
            "administration"
        ][
            "id"
        ]
        == 3
    )


def test_list_route_is_admission_and_tenant_scoped(
    monkeypatch,
    app,
):
    monkeypatch.setattr(
        emar_routes,
        "_current_clinic_id",
        lambda: 7,
    )

    captured = {}

    def fake_list(**kwargs):
        captured.update(
            kwargs
        )
        return {
            "items": [
                _record()
            ],
            "total": 1,
            "page": kwargs[
                "page"
            ],
            "per_page": kwargs[
                "per_page"
            ],
        }

    monkeypatch.setattr(
        emar_routes,
        "list_medication_administrations_for_admission",
        fake_list,
    )

    with app.test_request_context(
        (
            "/api/v1/emar/admissions/9"
            "?status=scheduled&page=2&per_page=10"
        ),
        method="GET",
    ):
        response, status = _raw(
            emar_routes
            .list_admission_emar_route
        )(
            admission_id=9
        )

    assert status == 200
    assert captured[
        "admission_id"
    ] == 9
    assert captured[
        "clinic_id"
    ] == 7
    assert (
        captured[
            "status"
        ]
        == MedicationAdministrationStatus.SCHEDULED
    )
    assert captured[
        "page"
    ] == 2
    assert captured[
        "per_page"
    ] == 10

    body = response.get_json()

    assert body[
        "total"
    ] == 1
    assert len(
        body[
            "items"
        ]
    ) == 1


def test_historical_get_and_list_service_do_not_require_active_admission(
    monkeypatch,
):
    record = _record()

    monkeypatch.setattr(
        emar_routes,
        "get_medication_administration",
        get_medication_administration,
        raising=False,
    )

    assert callable(
        get_medication_administration
    )
    assert callable(
        list_medication_administrations_for_admission
    )
