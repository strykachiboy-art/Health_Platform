from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.core.enums.appointment_enums import (
    AppointmentStatus,
    AppointmentType,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
)
from app.modules.appointment.routes import appointment_route
from app.modules.appointment.schemas.appointment_schema import (
    AppointmentCancelSchema,
    AppointmentCompleteSchema,
    AppointmentCreateSchema,
    AppointmentRescheduleSchema,
    AppointmentStaffScheduleQuerySchema,
)


# ============================================================================
# Helpers
# ============================================================================


def make_appointment(
    appointment_id=1,
    clinic_id=10,
    patient_id=20,
    staff_id=30,
    scheduled_start=None,
    scheduled_end=None,
    status=AppointmentStatus.SCHEDULED,
    appointment_type=AppointmentType.IN_PERSON,
    reason="Routine consultation",
    notes=None,
    google_calendar_event_id=None,
    reminder_sent=False,
    created_at=None,
    updated_at=None,
    cancelled_at=None,
    cancellation_reason=None,
):
    class Appointment:
        pass

    appointment = Appointment()

    appointment.id = appointment_id
    appointment.clinic_id = clinic_id
    appointment.patient_id = patient_id
    appointment.staff_id = staff_id

    appointment.scheduled_start = (
        scheduled_start
        or datetime(2026, 9, 8, 10, 0)
    )

    appointment.scheduled_end = (
        scheduled_end
        or datetime(2026, 9, 8, 10, 30)
    )

    appointment.status = status
    appointment.appointment_type = appointment_type
    appointment.reason = reason
    appointment.notes = notes
    appointment.google_calendar_event_id = (
        google_calendar_event_id
    )
    appointment.reminder_sent = reminder_sent
    appointment.created_at = created_at
    appointment.updated_at = updated_at
    appointment.cancelled_at = cancelled_at
    appointment.cancellation_reason = (
        cancellation_reason
    )

    return appointment


def make_page(
    items,
    page=1,
    per_page=50,
    total=None,
):
    total = len(items) if total is None else total

    pages = (
        0
        if total == 0
        else (total + per_page - 1) // per_page
    )

    return SimpleNamespace(
        items=items,
        page=page,
        per_page=per_page,
        total=total,
        pages=pages,
        has_next=page < pages,
        has_prev=page > 1,
    )


def admin_headers(
    clinic,
    make_user,
    auth_headers_for,
):
    user = make_user(
        clinic=clinic,
        role=Role.ADMIN,
    )

    return auth_headers_for(user)


# ============================================================================
# Payload validation
# ============================================================================


def test_payload_accepts_valid_create_payload(app):
    start = datetime(2026, 9, 8, 10, 0)
    end = datetime(2026, 9, 8, 10, 30)

    with app.test_request_context(
        "/api/v1/appointments/",
        method="POST",
        json={
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": start.isoformat(),
            "scheduled_end": end.isoformat(),
            "appointment_type": AppointmentType.IN_PERSON.value,
            "reason": "Routine consultation",
            "notes": "Initial visit",
        },
    ):
        result = appointment_route._payload(
            AppointmentCreateSchema,
        )

    assert isinstance(
        result,
        AppointmentCreateSchema,
    )

    assert result.patient_id == 20
    assert result.staff_id == 30
    assert result.scheduled_start == start
    assert result.scheduled_end == end
    assert (
        result.appointment_type
        == AppointmentType.IN_PERSON
    )
    assert result.reason == "Routine consultation"
    assert result.notes == "Initial visit"


def test_payload_rejects_invalid_create_payload(app):
    with app.test_request_context(
        "/api/v1/appointments/",
        method="POST",
        json={
            "patient_id": 0,
            "staff_id": 0,
        },
    ):
        result = appointment_route._payload(
            AppointmentCreateSchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid request payload"


def test_payload_rejects_unknown_create_field(app):
    with app.test_request_context(
        "/api/v1/appointments/",
        method="POST",
        json={
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": (
                "2026-09-08T10:00:00"
            ),
            "scheduled_end": (
                "2026-09-08T10:30:00"
            ),
            "unexpected": "blocked",
        },
    ):
        result = appointment_route._payload(
            AppointmentCreateSchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid request payload"


def test_payload_rejects_client_clinic_id(app):
    with app.test_request_context(
        "/api/v1/appointments/",
        method="POST",
        json={
            "clinic_id": 999,
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": (
                "2026-09-08T10:00:00"
            ),
            "scheduled_end": (
                "2026-09-08T10:30:00"
            ),
        },
    ):
        result = appointment_route._payload(
            AppointmentCreateSchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid request payload"


def test_payload_rejects_non_object_json(app):
    with app.test_request_context(
        "/api/v1/appointments/",
        method="POST",
        json=[],
    ):
        result = appointment_route._payload(
            AppointmentCreateSchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Request body must be a JSON object"
    )


def test_payload_accepts_empty_cancel_payload(app):
    with app.test_request_context(
        "/api/v1/appointments/1/cancel",
        method="POST",
        json={},
    ):
        result = appointment_route._payload(
            AppointmentCancelSchema,
        )

    assert isinstance(
        result,
        AppointmentCancelSchema,
    )

    assert result.cancellation_reason is None


def test_payload_accepts_empty_complete_payload(app):
    with app.test_request_context(
        "/api/v1/appointments/1/complete",
        method="POST",
        json={},
    ):
        result = appointment_route._payload(
            AppointmentCompleteSchema,
        )

    assert isinstance(
        result,
        AppointmentCompleteSchema,
    )

    assert result.notes is None


def test_payload_rejects_invalid_reschedule_payload(app):
    with app.test_request_context(
        "/api/v1/appointments/1/reschedule",
        method="POST",
        json={
            "scheduled_start": "not-a-date",
            "scheduled_end": "not-a-date",
        },
    ):
        result = appointment_route._payload(
            AppointmentRescheduleSchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid request payload"


def test_payload_rejects_unknown_reschedule_field(app):
    with app.test_request_context(
        "/api/v1/appointments/1/reschedule",
        method="POST",
        json={
            "scheduled_start": (
                "2026-09-08T10:00:00"
            ),
            "scheduled_end": (
                "2026-09-08T10:30:00"
            ),
            "unexpected": True,
        },
    ):
        result = appointment_route._payload(
            AppointmentRescheduleSchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid request payload"


def test_query_payload_accepts_date_and_pagination(app):
    with app.test_request_context(
        "/api/v1/appointments/staff/30",
        method="GET",
        query_string={
            "date": "2026-09-08",
            "page": "2",
            "per_page": "100",
        },
    ):
        result = appointment_route._query_payload(
            AppointmentStaffScheduleQuerySchema,
        )

    assert isinstance(
        result,
        AppointmentStaffScheduleQuerySchema,
    )

    assert result.date_ == date(
        2026,
        9,
        8,
    )

    assert result.page == 2
    assert result.per_page == 100


def test_query_payload_uses_pagination_defaults(app):
    with app.test_request_context(
        "/api/v1/appointments/staff/30",
        method="GET",
    ):
        result = appointment_route._query_payload(
            AppointmentStaffScheduleQuerySchema,
        )

    assert isinstance(
        result,
        AppointmentStaffScheduleQuerySchema,
    )

    assert result.date_ is None
    assert result.page == 1
    assert result.per_page == 50


def test_query_payload_rejects_invalid_date(app):
    with app.test_request_context(
        "/api/v1/appointments/staff/30",
        method="GET",
        query_string={
            "date": "not-a-date",
        },
    ):
        result = appointment_route._query_payload(
            AppointmentStaffScheduleQuerySchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid query parameters"


def test_query_payload_rejects_invalid_page(app):
    with app.test_request_context(
        "/api/v1/appointments/staff/30",
        method="GET",
        query_string={
            "page": "0",
        },
    ):
        result = appointment_route._query_payload(
            AppointmentStaffScheduleQuerySchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid query parameters"


def test_query_payload_rejects_invalid_per_page(app):
    with app.test_request_context(
        "/api/v1/appointments/staff/30",
        method="GET",
        query_string={
            "per_page": "501",
        },
    ):
        result = appointment_route._query_payload(
            AppointmentStaffScheduleQuerySchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid query parameters"


def test_query_payload_rejects_unknown_field(app):
    with app.test_request_context(
        "/api/v1/appointments/staff/30",
        method="GET",
        query_string={
            "page": "1",
            "per_page": "50",
            "unknown": "blocked",
        },
    ):
        result = appointment_route._query_payload(
            AppointmentStaffScheduleQuerySchema,
        )

    assert isinstance(result, tuple)

    response, status_code = result

    assert status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid query parameters"


# ============================================================================
# Serialization
# ============================================================================


def test_serialize_appointment():
    created_at = datetime(
        2026,
        9,
        7,
        8,
        0,
    )

    updated_at = datetime(
        2026,
        9,
        7,
        9,
        0,
    )

    cancelled_at = datetime(
        2026,
        9,
        7,
        9,
        30,
    )

    appointment = make_appointment(
        appointment_id=7,
        clinic_id=10,
        patient_id=20,
        staff_id=30,
        status=AppointmentStatus.CANCELLED,
        appointment_type=AppointmentType.IN_PERSON,
        reason="Follow-up",
        notes="Patient requested follow-up",
        google_calendar_event_id="google-event-7",
        reminder_sent=True,
        created_at=created_at,
        updated_at=updated_at,
        cancelled_at=cancelled_at,
        cancellation_reason="Patient unavailable",
    )

    data = appointment_route._serialize_appointment(
        appointment
    )

    assert data == {
        "id": 7,
        "clinic_id": 10,
        "patient_id": 20,
        "staff_id": 30,
        "scheduled_start": (
            appointment.scheduled_start.isoformat()
        ),
        "scheduled_end": (
            appointment.scheduled_end.isoformat()
        ),
        "status": (
            AppointmentStatus.CANCELLED.value
        ),
        "appointment_type": (
            AppointmentType.IN_PERSON.value
        ),
        "reason": "Follow-up",
        "notes": "Patient requested follow-up",
        "google_calendar_event_id": "google-event-7",
        "reminder_sent": True,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "cancelled_at": cancelled_at.isoformat(),
        "cancellation_reason": (
            "Patient unavailable"
        ),
    }


def test_serialize_appointment_handles_nullable_fields():
    appointment = make_appointment(
        reason=None,
        notes=None,
        google_calendar_event_id=None,
        created_at=None,
        updated_at=None,
        cancelled_at=None,
        cancellation_reason=None,
    )

    data = appointment_route._serialize_appointment(
        appointment
    )

    assert data["reason"] is None
    assert data["notes"] is None
    assert data["google_calendar_event_id"] is None
    assert data["created_at"] is None
    assert data["updated_at"] is None
    assert data["cancelled_at"] is None
    assert data["cancellation_reason"] is None


def test_serialize_page():
    appointments = [
        make_appointment(
            appointment_id=1,
        ),
        make_appointment(
            appointment_id=2,
        ),
    ]

    page = make_page(
        appointments,
        page=2,
        per_page=2,
        total=5,
    )

    data = appointment_route._serialize_page(
        page
    )

    assert len(data["items"]) == 2
    assert data["items"][0]["id"] == 1
    assert data["items"][1]["id"] == 2
    assert data["page"] == 2
    assert data["per_page"] == 2
    assert data["total"] == 5
    assert data["pages"] == 3
    assert data["has_next"] is True
    assert data["has_prev"] is True


def test_serialize_empty_page():
    page = make_page(
        [],
        page=1,
        per_page=50,
        total=0,
    )

    data = appointment_route._serialize_page(
        page
    )

    assert data["items"] == []
    assert data["page"] == 1
    assert data["per_page"] == 50
    assert data["total"] == 0
    assert data["pages"] == 0
    assert data["has_next"] is False
    assert data["has_prev"] is False


# ============================================================================
# Create appointment
# ============================================================================


def test_create_appointment_success(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointment = make_appointment(
        appointment_id=7,
        clinic_id=clinic.id,
        patient_id=20,
        staff_id=30,
        appointment_type=AppointmentType.IN_PERSON,
        reason="Routine consultation",
        notes="Initial visit",
    )

    captured = {}

    def fake_create_appointment(
        clinic_id,
        patient_id,
        staff_id,
        scheduled_start,
        scheduled_end,
        appointment_type,
        reason,
        notes,
    ):
        captured["clinic_id"] = clinic_id
        captured["patient_id"] = patient_id
        captured["staff_id"] = staff_id
        captured["scheduled_start"] = scheduled_start
        captured["scheduled_end"] = scheduled_end
        captured["appointment_type"] = appointment_type
        captured["reason"] = reason
        captured["notes"] = notes

        return appointment

    monkeypatch.setattr(
        appointment_route,
        "create_appointment",
        fake_create_appointment,
    )

    response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": (
                "2026-09-08T10:00:00"
            ),
            "scheduled_end": (
                "2026-09-08T10:30:00"
            ),
            "appointment_type": (
                AppointmentType.IN_PERSON.value
            ),
            "reason": "Routine consultation",
            "notes": "Initial visit",
        },
        headers=headers,
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == 7
    assert body["data"]["clinic_id"] == clinic.id
    assert body["data"]["patient_id"] == 20
    assert body["data"]["staff_id"] == 30
    assert body["data"]["notes"] == "Initial visit"

    assert captured["clinic_id"] == clinic.id
    assert captured["patient_id"] == 20
    assert captured["staff_id"] == 30
    assert (
        captured["appointment_type"]
        == AppointmentType.IN_PERSON
    )
    assert captured["reason"] == (
        "Routine consultation"
    )
    assert captured["notes"] == "Initial visit"


def test_create_appointment_does_not_accept_client_clinic_id(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    called = False

    def fake_create_appointment(**kwargs):
        nonlocal called
        called = True

        return make_appointment(
            clinic_id=clinic.id,
        )

    monkeypatch.setattr(
        appointment_route,
        "create_appointment",
        fake_create_appointment,
    )

    response = client.post(
        "/api/v1/appointments/",
        json={
            "clinic_id": 999999,
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": (
                "2026-09-08T10:00:00"
            ),
            "scheduled_end": (
                "2026-09-08T10:30:00"
            ),
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert called is False


def test_create_appointment_domain_error(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    def raise_conflict(**kwargs):
        raise ConflictError(
            "Appointment overlaps an existing appointment",
            code="APPOINTMENT_PATIENT_SCHEDULE_CONFLICT",
        )

    monkeypatch.setattr(
        appointment_route,
        "create_appointment",
        raise_conflict,
    )

    response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": (
                "2026-09-08T10:00:00"
            ),
            "scheduled_end": (
                "2026-09-08T10:30:00"
            ),
        },
        headers=headers,
    )

    assert response.status_code == 409

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Appointment overlaps an existing appointment"
    )
    assert body["code"] == "APPOINTMENT_PATIENT_SCHEDULE_CONFLICT"


def test_create_appointment_invalid_payload_does_not_call_service(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    called = False

    def fake_create_appointment(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(
        appointment_route,
        "create_appointment",
        fake_create_appointment,
    )

    response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": 0,
            "staff_id": 0,
        },
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Invalid request payload"
    )
    assert called is False


# ============================================================================
# Reschedule
# ============================================================================


def test_reschedule_appointment_success(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointment = make_appointment(
        appointment_id=8,
        clinic_id=clinic.id,
    )

    captured = {}

    def fake_reschedule_appointment(
        appointment_id,
        clinic_id,
        new_start,
        new_end,
    ):
        captured["appointment_id"] = appointment_id
        captured["clinic_id"] = clinic_id
        captured["new_start"] = new_start
        captured["new_end"] = new_end

        appointment.scheduled_start = new_start
        appointment.scheduled_end = new_end

        return appointment

    monkeypatch.setattr(
        appointment_route,
        "reschedule_appointment",
        fake_reschedule_appointment,
    )

    response = client.post(
        "/api/v1/appointments/8/reschedule",
        json={
            "scheduled_start": (
                "2026-09-09T11:00:00"
            ),
            "scheduled_end": (
                "2026-09-09T11:30:00"
            ),
        },
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == 8
    assert body["data"]["scheduled_start"] == (
        "2026-09-09T11:00:00"
    )
    assert body["data"]["scheduled_end"] == (
        "2026-09-09T11:30:00"
    )

    assert captured["appointment_id"] == 8
    assert captured["clinic_id"] == clinic.id


def test_reschedule_appointment_domain_error(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    def raise_not_found(**kwargs):
        raise NotFoundError(
            "Appointment 999 not found"
        )

    monkeypatch.setattr(
        appointment_route,
        "reschedule_appointment",
        raise_not_found,
    )

    response = client.post(
        "/api/v1/appointments/999/reschedule",
        json={
            "scheduled_start": (
                "2026-09-09T11:00:00"
            ),
            "scheduled_end": (
                "2026-09-09T11:30:00"
            ),
        },
        headers=headers,
    )

    assert response.status_code == 404

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Appointment 999 not found"
    )


def test_reschedule_appointment_invalid_payload(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    called = False

    def fake_reschedule(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(
        appointment_route,
        "reschedule_appointment",
        fake_reschedule,
    )

    response = client.post(
        "/api/v1/appointments/1/reschedule",
        json={
            "scheduled_start": "invalid",
            "scheduled_end": "invalid",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert called is False


# ============================================================================
# Confirm
# ============================================================================


def test_confirm_appointment_success(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointment = make_appointment(
        appointment_id=9,
        clinic_id=clinic.id,
        status=AppointmentStatus.CONFIRMED,
    )

    captured = {}

    def fake_confirm_appointment(
        appointment_id,
        clinic_id,
    ):
        captured["appointment_id"] = appointment_id
        captured["clinic_id"] = clinic_id
        return appointment

    monkeypatch.setattr(
        appointment_route,
        "confirm_appointment",
        fake_confirm_appointment,
    )

    response = client.post(
        "/api/v1/appointments/9/confirm",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == 9
    assert body["data"]["status"] == (
        AppointmentStatus.CONFIRMED.value
    )

    assert captured["appointment_id"] == 9
    assert captured["clinic_id"] == clinic.id


def test_confirm_appointment_domain_error(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    def raise_conflict(**kwargs):
        raise ConflictError(
            "Appointment cannot be confirmed"
        )

    monkeypatch.setattr(
        appointment_route,
        "confirm_appointment",
        raise_conflict,
    )

    response = client.post(
        "/api/v1/appointments/1/confirm",
        headers=headers,
    )

    assert response.status_code == 409

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Appointment cannot be confirmed"
    )


# ============================================================================
# Cancel
# ============================================================================


def test_cancel_appointment_success(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointment = make_appointment(
        appointment_id=10,
        clinic_id=clinic.id,
        status=AppointmentStatus.CANCELLED,
        cancellation_reason="Patient unavailable",
    )

    captured = {}

    def fake_cancel_appointment(
        appointment_id,
        clinic_id,
        reason,
    ):
        captured["appointment_id"] = appointment_id
        captured["clinic_id"] = clinic_id
        captured["reason"] = reason
        return appointment

    monkeypatch.setattr(
        appointment_route,
        "cancel_appointment",
        fake_cancel_appointment,
    )

    response = client.post(
        "/api/v1/appointments/10/cancel",
        json={
            "cancellation_reason": (
                "Patient unavailable"
            ),
        },
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == 10
    assert body["data"]["cancellation_reason"] == (
        "Patient unavailable"
    )

    assert captured["appointment_id"] == 10
    assert captured["clinic_id"] == clinic.id
    assert captured["reason"] == (
        "Patient unavailable"
    )


def test_cancel_appointment_accepts_empty_payload(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointment = make_appointment(
        appointment_id=11,
        clinic_id=clinic.id,
        status=AppointmentStatus.CANCELLED,
    )

    captured = {}

    def fake_cancel_appointment(
        appointment_id,
        clinic_id,
        reason,
    ):
        captured["reason"] = reason
        return appointment

    monkeypatch.setattr(
        appointment_route,
        "cancel_appointment",
        fake_cancel_appointment,
    )

    response = client.post(
        "/api/v1/appointments/11/cancel",
        json={},
        headers=headers,
    )

    assert response.status_code == 200
    assert captured["reason"] is None


def test_cancel_appointment_domain_error(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    def raise_conflict(**kwargs):
        raise ConflictError(
            "Appointment cannot be cancelled"
        )

    monkeypatch.setattr(
        appointment_route,
        "cancel_appointment",
        raise_conflict,
    )

    response = client.post(
        "/api/v1/appointments/1/cancel",
        json={},
        headers=headers,
    )

    assert response.status_code == 409

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Appointment cannot be cancelled"
    )


# ============================================================================
# Complete
# ============================================================================


def test_complete_appointment_success(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointment = make_appointment(
        appointment_id=12,
        clinic_id=clinic.id,
        status=AppointmentStatus.COMPLETED,
        notes="Consultation completed",
    )

    captured = {}

    def fake_complete_appointment(
        appointment_id,
        clinic_id,
        notes,
    ):
        captured["appointment_id"] = appointment_id
        captured["clinic_id"] = clinic_id
        captured["notes"] = notes
        return appointment

    monkeypatch.setattr(
        appointment_route,
        "complete_appointment",
        fake_complete_appointment,
    )

    response = client.post(
        "/api/v1/appointments/12/complete",
        json={
            "notes": "Consultation completed",
        },
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == 12
    assert body["data"]["status"] == (
        AppointmentStatus.COMPLETED.value
    )

    assert captured["appointment_id"] == 12
    assert captured["clinic_id"] == clinic.id
    assert captured["notes"] == (
        "Consultation completed"
    )


def test_complete_appointment_accepts_empty_payload(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointment = make_appointment(
        appointment_id=13,
        clinic_id=clinic.id,
        status=AppointmentStatus.COMPLETED,
    )

    captured = {}

    def fake_complete_appointment(
        appointment_id,
        clinic_id,
        notes,
    ):
        captured["notes"] = notes
        return appointment

    monkeypatch.setattr(
        appointment_route,
        "complete_appointment",
        fake_complete_appointment,
    )

    response = client.post(
        "/api/v1/appointments/13/complete",
        json={},
        headers=headers,
    )

    assert response.status_code == 200
    assert captured["notes"] is None


def test_complete_appointment_domain_error(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    def raise_conflict(**kwargs):
        raise ConflictError(
            "Appointment cannot be completed"
        )

    monkeypatch.setattr(
        appointment_route,
        "complete_appointment",
        raise_conflict,
    )

    response = client.post(
        "/api/v1/appointments/1/complete",
        json={},
        headers=headers,
    )

    assert response.status_code == 409

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Appointment cannot be completed"
    )


# ============================================================================
# No-show
# ============================================================================


def test_mark_appointment_no_show_success(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointment = make_appointment(
        appointment_id=14,
        clinic_id=clinic.id,
        status=AppointmentStatus.NO_SHOW,
    )

    captured = {}

    def fake_mark_no_show(
        appointment_id,
        clinic_id,
    ):
        captured["appointment_id"] = appointment_id
        captured["clinic_id"] = clinic_id
        return appointment

    monkeypatch.setattr(
        appointment_route,
        "mark_no_show",
        fake_mark_no_show,
    )

    response = client.post(
        "/api/v1/appointments/14/no-show",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == 14
    assert body["data"]["status"] == (
        AppointmentStatus.NO_SHOW.value
    )

    assert captured["appointment_id"] == 14
    assert captured["clinic_id"] == clinic.id


def test_mark_appointment_no_show_domain_error(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    def raise_conflict(**kwargs):
        raise ConflictError(
            "Appointment cannot be marked as no-show"
        )

    monkeypatch.setattr(
        appointment_route,
        "mark_no_show",
        raise_conflict,
    )

    response = client.post(
        "/api/v1/appointments/1/no-show",
        headers=headers,
    )

    assert response.status_code == 409

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Appointment cannot be marked as no-show"
    )


# ============================================================================
# Patient appointment history
# ============================================================================


def test_get_patient_appointments_success(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointments = [
        make_appointment(
            appointment_id=1,
            clinic_id=clinic.id,
            patient_id=20,
        ),
        make_appointment(
            appointment_id=2,
            clinic_id=clinic.id,
            patient_id=20,
        ),
    ]

    captured = {}

    def fake_get_appointments_for_patient(
        patient_id,
        clinic_id,
        page,
        per_page,
    ):
        captured["patient_id"] = patient_id
        captured["clinic_id"] = clinic_id
        captured["page"] = page
        captured["per_page"] = per_page

        return make_page(
            appointments,
            page=page,
            per_page=per_page,
            total=2,
        )

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_patient",
        fake_get_appointments_for_patient,
    )

    response = client.get(
        "/api/v1/appointments/patient/20",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert len(body["data"]["items"]) == 2
    assert body["data"]["items"][0]["id"] == 1
    assert body["data"]["items"][1]["id"] == 2

    assert body["data"]["page"] == 1
    assert body["data"]["per_page"] == 50
    assert body["data"]["total"] == 2
    assert body["data"]["pages"] == 1
    assert body["data"]["has_next"] is False
    assert body["data"]["has_prev"] is False

    assert captured["patient_id"] == 20
    assert captured["clinic_id"] == clinic.id
    assert captured["page"] == 1
    assert captured["per_page"] == 50


def test_get_patient_appointments_accepts_pagination(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    captured = {}

    def fake_get_appointments_for_patient(
        patient_id,
        clinic_id,
        page,
        per_page,
    ):
        captured["patient_id"] = patient_id
        captured["clinic_id"] = clinic_id
        captured["page"] = page
        captured["per_page"] = per_page

        return make_page(
            [
                make_appointment(
                    appointment_id=101,
                    clinic_id=clinic.id,
                    patient_id=patient_id,
                )
            ],
            page=page,
            per_page=per_page,
            total=101,
        )

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_patient",
        fake_get_appointments_for_patient,
    )

    response = client.get(
        "/api/v1/appointments/patient/20",
        query_string={
            "page": "3",
            "per_page": "25",
        },
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["page"] == 3
    assert body["data"]["per_page"] == 25
    assert body["data"]["total"] == 101
    assert body["data"]["pages"] == 5

    assert captured["patient_id"] == 20
    assert captured["clinic_id"] == clinic.id
    assert captured["page"] == 3
    assert captured["per_page"] == 25


def test_get_patient_appointments_rejects_invalid_pagination(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    called = False

    def fake_get_appointments_for_patient(**kwargs):
        nonlocal called
        called = True
        return make_page([])

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_patient",
        fake_get_appointments_for_patient,
    )

    response = client.get(
        "/api/v1/appointments/patient/20",
        query_string={
            "page": "0",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert called is False


def test_get_patient_appointments_not_found(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    def raise_not_found(**kwargs):
        raise NotFoundError(
            "Patient 999 not found"
        )

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_patient",
        raise_not_found,
    )

    response = client.get(
        "/api/v1/appointments/patient/999",
        headers=headers,
    )

    assert response.status_code == 404

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Patient 999 not found"
    )


# ============================================================================
# Staff appointment schedule
# ============================================================================


def test_get_staff_appointments_success(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    appointments = [
        make_appointment(
            appointment_id=20,
            clinic_id=clinic.id,
            staff_id=30,
        ),
        make_appointment(
            appointment_id=21,
            clinic_id=clinic.id,
            staff_id=30,
        ),
    ]

    captured = {}

    def fake_get_appointments_for_staff(
        clinic_id,
        staff_id,
        date_,
        page,
        per_page,
    ):
        captured["clinic_id"] = clinic_id
        captured["staff_id"] = staff_id
        captured["date_"] = date_
        captured["page"] = page
        captured["per_page"] = per_page

        return make_page(
            appointments,
            page=page,
            per_page=per_page,
            total=2,
        )

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_staff",
        fake_get_appointments_for_staff,
    )

    response = client.get(
        "/api/v1/appointments/staff/30",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert len(body["data"]["items"]) == 2
    assert body["data"]["items"][0]["id"] == 20
    assert body["data"]["items"][1]["id"] == 21

    assert body["data"]["page"] == 1
    assert body["data"]["per_page"] == 50
    assert body["data"]["total"] == 2

    assert captured["clinic_id"] == clinic.id
    assert captured["staff_id"] == 30
    assert captured["date_"] is None
    assert captured["page"] == 1
    assert captured["per_page"] == 50


def test_get_staff_appointments_with_date_filter(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    captured = {}

    def fake_get_appointments_for_staff(
        clinic_id,
        staff_id,
        date_,
        page,
        per_page,
    ):
        captured["clinic_id"] = clinic_id
        captured["staff_id"] = staff_id
        captured["date_"] = date_
        captured["page"] = page
        captured["per_page"] = per_page

        return make_page(
            [
                make_appointment(
                    appointment_id=22,
                    clinic_id=clinic.id,
                    staff_id=staff_id,
                )
            ],
            page=page,
            per_page=per_page,
            total=26,
        )

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_staff",
        fake_get_appointments_for_staff,
    )

    response = client.get(
        "/api/v1/appointments/staff/30",
        query_string={
            "date": "2026-09-08",
            "page": "2",
            "per_page": "25",
        },
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert len(body["data"]["items"]) == 1
    assert body["data"]["page"] == 2
    assert body["data"]["per_page"] == 25
    assert body["data"]["total"] == 26
    assert body["data"]["pages"] == 2

    assert captured["clinic_id"] == clinic.id
    assert captured["staff_id"] == 30
    assert captured["date_"] == date(
        2026,
        9,
        8,
    )
    assert captured["page"] == 2
    assert captured["per_page"] == 25


def test_get_staff_appointments_rejects_invalid_date(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    called = False

    def fake_get_appointments_for_staff(**kwargs):
        nonlocal called
        called = True
        return make_page([])

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_staff",
        fake_get_appointments_for_staff,
    )

    response = client.get(
        "/api/v1/appointments/staff/30",
        query_string={
            "date": "not-a-date",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert called is False


def test_get_staff_appointments_rejects_invalid_pagination(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    called = False

    def fake_get_appointments_for_staff(**kwargs):
        nonlocal called
        called = True
        return make_page([])

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_staff",
        fake_get_appointments_for_staff,
    )

    response = client.get(
        "/api/v1/appointments/staff/30",
        query_string={
            "per_page": "501",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert called is False


def test_get_staff_appointments_rejects_unknown_query_field(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    called = False

    def fake_get_appointments_for_staff(**kwargs):
        nonlocal called
        called = True
        return make_page([])

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_staff",
        fake_get_appointments_for_staff,
    )

    response = client.get(
        "/api/v1/appointments/staff/30",
        query_string={
            "unknown": "blocked",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert called is False


def test_get_staff_appointments_domain_error(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    headers = admin_headers(
        clinic,
        make_user,
        auth_headers_for,
    )

    def raise_not_found(**kwargs):
        raise NotFoundError(
            "Staff 999 not found"
        )

    monkeypatch.setattr(
        appointment_route,
        "get_appointments_for_staff",
        raise_not_found,
    )

    response = client.get(
        "/api/v1/appointments/staff/999",
        headers=headers,
    )

    assert response.status_code == 404

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Staff 999 not found"
    )


# ============================================================================
# Authentication / authorization
# ============================================================================


@pytest.mark.parametrize(
    "role",
    [
        Role.ADMIN,
        Role.DOCTOR,
        Role.NURSE,
        Role.RECEPTIONIST,
    ],
)
def test_appointment_routes_allow_configured_roles(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
    role,
):
    user = make_user(
        clinic=clinic,
        role=role,
    )

    headers = auth_headers_for(user)

    appointment = make_appointment(
        clinic_id=clinic.id,
    )

    monkeypatch.setattr(
        appointment_route,
        "confirm_appointment",
        lambda **kwargs: appointment,
    )

    response = client.post(
        "/api/v1/appointments/1/confirm",
        headers=headers,
    )

    assert response.status_code == 200


def test_appointment_route_rejects_unauthorized_role(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
):
    user = make_user(
        clinic=clinic,
        role=Role.PHARMACIST,
    )

    headers = auth_headers_for(user)

    response = client.post(
        "/api/v1/appointments/1/confirm",
        headers=headers,
    )

    assert response.status_code in (401, 403)


def test_create_appointment_rejects_inactive_user(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
):
    user = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        is_active=False,
    )

    headers = auth_headers_for(user)

    response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": (
                "2026-09-08T10:00:00"
            ),
            "scheduled_end": (
                "2026-09-08T10:30:00"
            ),
        },
        headers=headers,
    )

    assert response.status_code == 401


def test_appointment_route_rejects_user_without_clinic(
    app,
    client,
    make_user,
    auth_headers_for,
):
    user = make_user(
        role=Role.ADMIN,
    )

    headers = auth_headers_for(user)

    response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": (
                "2026-09-08T10:00:00"
            ),
            "scheduled_end": (
                "2026-09-08T10:30:00"
            ),
        },
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False


def test_appointment_route_rejects_unauthenticated_request(
    app,
    client,
):
    response = client.post(
        "/api/v1/appointments/1/confirm",
    )

    assert response.status_code in (
        401,
        403,
    )


# ============================================================================
# Route registration
# ============================================================================


def test_appointment_routes_are_registered(app):
    rules = {
        rule.rule
        for rule in app.url_map.iter_rules()
    }

    assert "/api/v1/appointments/" in rules

    assert (
        "/api/v1/appointments/"
        "<int:appointment_id>/reschedule"
    ) in rules

    assert (
        "/api/v1/appointments/"
        "<int:appointment_id>/confirm"
    ) in rules

    assert (
        "/api/v1/appointments/"
        "<int:appointment_id>/cancel"
    ) in rules

    assert (
        "/api/v1/appointments/"
        "<int:appointment_id>/complete"
    ) in rules

    assert (
        "/api/v1/appointments/"
        "<int:appointment_id>/no-show"
    ) in rules

    assert (
        "/api/v1/appointments/"
        "patient/<int:patient_id>"
    ) in rules

    assert (
        "/api/v1/appointments/"
        "staff/<int:staff_id>"
    ) in rules

# ============================================================================
# Phase 9 Gate 3 — idempotency
# ============================================================================


def test_create_appointment_forwards_idempotency_header(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    user = make_user(
        clinic=clinic,
        role=Role.ADMIN,
    )

    headers = auth_headers_for(user)
    headers["Idempotency-Key"] = "appointment-route-001"

    appointment = make_appointment(
        appointment_id=77,
        clinic_id=clinic.id,
        patient_id=20,
        staff_id=30,
    )

    captured = {}

    def fake_create_appointment(**kwargs):
        captured.update(kwargs)
        return appointment

    monkeypatch.setattr(
        appointment_route,
        "create_appointment",
        fake_create_appointment,
    )

    response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": "2026-09-08T11:00:00",
            "scheduled_end": "2026-09-08T11:30:00",
            "appointment_type": AppointmentType.EMERGENCY.value,
            "reason": "Priority appointment",
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert captured["idempotency_key"] == (
        "appointment-route-001"
    )
    assert captured["idempotency_user_id"] == user.id


def test_create_appointment_rejects_blank_idempotency_header(
    app,
    client,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    user = make_user(
        clinic=clinic,
        role=Role.ADMIN,
    )

    headers = auth_headers_for(user)
    headers["Idempotency-Key"] = "   "

    called = False

    def fake_create_appointment(**kwargs):
        nonlocal called
        called = True
        return make_appointment(
            clinic_id=clinic.id,
        )

    monkeypatch.setattr(
        appointment_route,
        "create_appointment",
        fake_create_appointment,
    )

    response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": 20,
            "staff_id": 30,
            "scheduled_start": "2026-09-08T11:00:00",
            "scheduled_end": "2026-09-08T11:30:00",
        },
        headers=headers,
    )

    assert response.status_code == 400

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Idempotency-Key cannot be blank"
    )
    assert called is False
