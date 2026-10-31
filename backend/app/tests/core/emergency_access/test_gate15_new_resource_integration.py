from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.emergency_access.services import (
    emergency_access_service,
)
from app.core.enums.emergency_access_enums import (
    EmergencyAccessStatus,
)
from app.core.enums.role_enums import Role
from app.extensions import db

from app.modules.emar.routes import emar_routes
from app.modules.medication_reconciliation.routes import (
    medication_reconciliation_route,
)
from app.modules.nursing.routes import nursing_route
from app.modules.ward.routes import ward_route


NEW_RESOURCE_TYPES = (
    "nursing_note",
    "care_plan",
    "medication_administration",
    "medication_reconciliation",
    "discharge_summary",
)


def _break_glass_headers(
    auth_headers_for,
    staff,
):
    headers = dict(
        auth_headers_for(
            staff.user,
            role=Role.PARAMEDIC,
        )
    )
    headers[
        "X-Emergency-Access-ID"
    ] = "77"
    return headers


def test_new_sensitive_resource_types_are_explicit():
    assert set(
        emergency_access_service
        .EMERGENCY_PATIENT_RESOURCE_MODELS
    ) >= set(
        NEW_RESOURCE_TYPES
    )


@pytest.mark.parametrize(
    "resource_type",
    NEW_RESOURCE_TYPES,
)
def test_new_resource_ownership_is_checked_by_existing_grant(
    monkeypatch,
    clinic,
    emergency_patient,
    emergency_staff,
    emergency_reviewer,
    make_emergency_grant,
    emergency_audit_mock,
    resource_type,
):
    now = datetime.now(
        timezone.utc
    )
    resource_id = 7001

    grant = make_emergency_grant(
        clinic=clinic,
        patient=emergency_patient,
        requester_user=emergency_staff.user,
        requester_role=Role.DOCTOR.value,
        scope=[
            f"{resource_type}:{resource_id}:read"
        ],
        status=EmergencyAccessStatus.ACTIVE,
        granted_at=(
            now
            - timedelta(minutes=1)
        ),
        expires_at=(
            now
            + timedelta(minutes=30)
        ),
        reviewed_at=(
            now
            - timedelta(minutes=1)
        ),
        reviewed_by_user_id=(
            emergency_reviewer.id
        ),
    )

    monkeypatch.setattr(
        emergency_access_service,
        "_get_user",
        lambda actor_id: emergency_staff.user,
    )
    monkeypatch.setattr(
        emergency_access_service,
        "_get_staff_for_user",
        lambda user: emergency_staff,
    )
    monkeypatch.setattr(
        emergency_access_service,
        "_get_user_clinic_id",
        lambda user: clinic.id,
    )
    monkeypatch.setattr(
        emergency_access_service,
        "_ensure_active_clinic",
        lambda clinic_id: clinic,
    )
    monkeypatch.setattr(
        emergency_access_service,
        "_get_patient",
        lambda patient_id: emergency_patient,
    )

    original_get = db.session.get
    target_model = (
        emergency_access_service
        .EMERGENCY_PATIENT_RESOURCE_MODELS[
            resource_type
        ]
    )

    def fake_get(model, primary_key, *args, **kwargs):
        if (
            model is target_model
            and primary_key == resource_id
        ):
            return SimpleNamespace(
                id=resource_id,
                clinic_id=clinic.id,
                patient_id=emergency_patient.id,
            )

        return original_get(
            model,
            primary_key,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        db.session,
        "get",
        fake_get,
    )

    assert emergency_access_service.assert_emergency_access(
        actor_id=emergency_staff.user.id,
        patient_id=emergency_patient.id,
        resource_type=resource_type,
        resource_id=resource_id,
        action="read",
        emergency_access_id=grant.id,
    ) is True

    emergency_audit_mock.assert_called_once()

    audit_payload = (
        emergency_audit_mock
        .call_args
        .kwargs["new_value"]
    )

    assert audit_payload[
        "resource_type"
    ] == resource_type
    assert audit_payload[
        "resource_id"
    ] == resource_id
    assert audit_payload[
        "patient_id"
    ] == emergency_patient.id


def test_new_resource_patient_mismatch_fails_closed(
    monkeypatch,
    clinic,
    emergency_patient,
    emergency_staff,
    emergency_reviewer,
    make_emergency_grant,
):
    now = datetime.now(
        timezone.utc
    )
    resource_id = 7011
    resource_type = "nursing_note"

    grant = make_emergency_grant(
        clinic=clinic,
        patient=emergency_patient,
        requester_user=emergency_staff.user,
        requester_role=Role.DOCTOR.value,
        scope=[
            f"{resource_type}:{resource_id}:read"
        ],
        status=EmergencyAccessStatus.ACTIVE,
        granted_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=30),
        reviewed_at=now - timedelta(minutes=1),
        reviewed_by_user_id=emergency_reviewer.id,
    )

    monkeypatch.setattr(
        emergency_access_service,
        "_get_user",
        lambda actor_id: emergency_staff.user,
    )
    monkeypatch.setattr(
        emergency_access_service,
        "_get_staff_for_user",
        lambda user: emergency_staff,
    )
    monkeypatch.setattr(
        emergency_access_service,
        "_get_user_clinic_id",
        lambda user: clinic.id,
    )
    monkeypatch.setattr(
        emergency_access_service,
        "_ensure_active_clinic",
        lambda clinic_id: clinic,
    )
    monkeypatch.setattr(
        emergency_access_service,
        "_get_patient",
        lambda patient_id: emergency_patient,
    )

    original_get = db.session.get
    target_model = (
        emergency_access_service
        .EMERGENCY_PATIENT_RESOURCE_MODELS[
            resource_type
        ]
    )

    def fake_get(model, primary_key, *args, **kwargs):
        if model is target_model:
            return SimpleNamespace(
                id=resource_id,
                clinic_id=clinic.id,
                patient_id=emergency_patient.id + 999,
            )

        return original_get(
            model,
            primary_key,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        db.session,
        "get",
        fake_get,
    )

    assert emergency_access_service.assert_emergency_access(
        actor_id=emergency_staff.user.id,
        patient_id=emergency_patient.id,
        resource_type=resource_type,
        resource_id=resource_id,
        action="read",
        emergency_access_id=grant.id,
    ) is False


def test_nursing_note_break_glass_read_surface(
    monkeypatch,
    client,
    clinic,
    emergency_patient,
    make_emergency_staff,
    auth_headers_for,
):
    staff = make_emergency_staff(
        clinic,
        role=Role.PARAMEDIC,
    )
    note = SimpleNamespace(
        id=801,
        clinic_id=clinic.id,
        patient_id=emergency_patient.id,
    )

    monkeypatch.setattr(
        nursing_route,
        "get_nursing_note",
        lambda *args, **kwargs: note,
    )
    monkeypatch.setattr(
        nursing_route,
        "_serialize",
        lambda *args, **kwargs: {
            "id": note.id,
        },
    )

    calls = []

    def authorize(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        nursing_route,
        "authorize_emergency_read_if_requested",
        authorize,
    )

    response = client.get(
        f"/api/v1/nursing/notes/{note.id}",
        headers=_break_glass_headers(
            auth_headers_for,
            staff,
        ),
    )

    assert response.status_code == 200
    assert calls == [{
        "patient_id": emergency_patient.id,
        "resource_type": "nursing_note",
        "resource_id": note.id,
    }]


def test_care_plan_break_glass_read_surface(
    monkeypatch,
    client,
    clinic,
    emergency_patient,
    make_emergency_staff,
    auth_headers_for,
):
    staff = make_emergency_staff(
        clinic,
        role=Role.PARAMEDIC,
    )
    plan = SimpleNamespace(
        id=802,
        clinic_id=clinic.id,
        patient_id=emergency_patient.id,
    )

    monkeypatch.setattr(
        nursing_route,
        "get_care_plan",
        lambda *args, **kwargs: plan,
    )
    monkeypatch.setattr(
        nursing_route,
        "_serialize",
        lambda *args, **kwargs: {
            "id": plan.id,
        },
    )

    calls = []

    def authorize(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        nursing_route,
        "authorize_emergency_read_if_requested",
        authorize,
    )

    response = client.get(
        f"/api/v1/nursing/care-plans/{plan.id}",
        headers=_break_glass_headers(
            auth_headers_for,
            staff,
        ),
    )

    assert response.status_code == 200
    assert calls == [{
        "patient_id": emergency_patient.id,
        "resource_type": "care_plan",
        "resource_id": plan.id,
    }]


def test_medication_administration_break_glass_read_surface(
    monkeypatch,
    client,
    clinic,
    emergency_patient,
    make_emergency_staff,
    auth_headers_for,
):
    staff = make_emergency_staff(
        clinic,
        role=Role.PARAMEDIC,
    )
    record = SimpleNamespace(
        id=803,
        clinic_id=clinic.id,
        patient_id=emergency_patient.id,
    )

    monkeypatch.setattr(
        emar_routes,
        "get_medication_administration",
        lambda *args, **kwargs: record,
    )
    monkeypatch.setattr(
        emar_routes,
        "_serialize",
        lambda *args, **kwargs: {
            "id": record.id,
        },
    )

    calls = []

    def authorize(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        emar_routes,
        "authorize_emergency_read_if_requested",
        authorize,
    )

    response = client.get(
        f"/api/v1/emar/{record.id}",
        headers=_break_glass_headers(
            auth_headers_for,
            staff,
        ),
    )

    assert response.status_code == 200
    assert calls == [{
        "patient_id": emergency_patient.id,
        "resource_type": "medication_administration",
        "resource_id": record.id,
    }]


def test_medication_reconciliation_break_glass_read_surface(
    monkeypatch,
    client,
    clinic,
    emergency_patient,
    make_emergency_staff,
    auth_headers_for,
):
    staff = make_emergency_staff(
        clinic,
        role=Role.PARAMEDIC,
    )
    record = SimpleNamespace(
        id=804,
        clinic_id=clinic.id,
        patient_id=emergency_patient.id,
    )

    monkeypatch.setattr(
        medication_reconciliation_route,
        "get_medication_reconciliation",
        lambda *args, **kwargs: record,
    )
    monkeypatch.setattr(
        medication_reconciliation_route,
        "_serialize",
        lambda *args, **kwargs: {
            "id": record.id,
        },
    )

    calls = []

    def authorize(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        medication_reconciliation_route,
        "authorize_emergency_read_if_requested",
        authorize,
    )

    response = client.get(
        f"/api/v1/medication-reconciliations/{record.id}",
        headers=_break_glass_headers(
            auth_headers_for,
            staff,
        ),
    )

    assert response.status_code == 200
    assert calls == [{
        "patient_id": emergency_patient.id,
        "resource_type": "medication_reconciliation",
        "resource_id": record.id,
    }]


def test_discharge_summary_break_glass_read_surface(
    monkeypatch,
    client,
    clinic,
    emergency_patient,
    make_emergency_staff,
    auth_headers_for,
):
    staff = make_emergency_staff(
        clinic,
        role=Role.PARAMEDIC,
    )
    summary = SimpleNamespace(
        id=805,
        admission_id=905,
        clinic_id=clinic.id,
        patient_id=emergency_patient.id,
    )

    monkeypatch.setattr(
        ward_route,
        "get_discharge_summary_for_admission",
        lambda *args, **kwargs: summary,
    )
    monkeypatch.setattr(
        ward_route,
        "_serialize_discharge_summary",
        lambda *args, **kwargs: {
            "id": summary.id,
        },
    )

    calls = []

    def authorize(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(
        ward_route,
        "authorize_emergency_read_if_requested",
        authorize,
    )

    response = client.get(
        (
            "/api/v1/wards/admissions/"
            f"{summary.admission_id}/discharge-summary"
        ),
        headers=_break_glass_headers(
            auth_headers_for,
            staff,
        ),
    )

    assert response.status_code == 200
    assert calls == [{
        "patient_id": emergency_patient.id,
        "resource_type": "discharge_summary",
        "resource_id": summary.id,
    }]


def test_emergency_header_does_not_bypass_clinical_write_roles(
    client,
    clinic,
    make_emergency_staff,
    auth_headers_for,
):
    staff = make_emergency_staff(
        clinic,
        role=Role.PARAMEDIC,
    )
    headers = _break_glass_headers(
        auth_headers_for,
        staff,
    )

    probes = (
        (
            "/api/v1/emar/scheduled",
            {},
        ),
        (
            "/api/v1/medication-reconciliations",
            {},
        ),
        (
            "/api/v1/nursing/admissions/1/care-plans",
            {},
        ),
        (
            "/api/v1/wards/admissions/1/discharge",
            {},
        ),
    )

    for url, payload in probes:
        response = client.post(
            url,
            headers=headers,
            json=payload,
        )

        assert response.status_code == 403, (
            url,
            response.status_code,
            response.get_json(),
        )
