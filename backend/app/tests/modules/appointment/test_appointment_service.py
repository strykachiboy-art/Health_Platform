# app/tests/modules/appointment/test_appointment_service.py

from datetime import date, datetime, timedelta

import pytest

from app.core.enums.appointment_enums import (
    AppointmentStatus,
    AppointmentType,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.modules.appointment.services import (
    appointment_service,
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
    reminder_sent=False,
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
        or datetime(
            2026,
            9,
            8,
            10,
            0,
        )
    )

    appointment.scheduled_end = (
        scheduled_end
        or datetime(
            2026,
            9,
            8,
            10,
            30,
        )
    )

    appointment.status = status
    appointment.appointment_type = appointment_type
    appointment.reason = reason
    appointment.notes = notes
    appointment.reminder_sent = reminder_sent
    appointment.cancelled_at = cancelled_at
    appointment.cancellation_reason = (
        cancellation_reason
    )

    return appointment


def make_pagination(
    items,
    *,
    page=1,
    per_page=50,
    total=None,
):
    total = len(items) if total is None else total

    pages = (
        (total + per_page - 1) // per_page
        if total
        else 0
    )

    return type(
        "Pagination",
        (),
        {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "has_next": page < pages,
            "has_prev": page > 1,
        },
    )()


# ============================================================================
# Validation helpers
# ============================================================================


def test_validate_positive_id_accepts_valid_id():
    assert (
        appointment_service._validate_positive_id(
            10,
            "Appointment ID",
        )
        is None
    )


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        True,
        False,
        None,
        "1",
    ],
)
def test_validate_positive_id_rejects_invalid_ids(
    value,
):
    with pytest.raises(
        ValidationError,
        match="Appointment ID must be a positive integer",
    ):
        appointment_service._validate_positive_id(
            value,
            "Appointment ID",
        )


def test_validate_pagination_accepts_valid_values():
    assert (
        appointment_service._validate_pagination(
            1,
            50,
        )
        is None
    )


@pytest.mark.parametrize(
    "page,per_page",
    [
        (0, 50),
        (-1, 50),
        (True, 50),
        (1, 0),
        (1, -1),
        (1, True),
        (1, 501),
    ],
)
def test_validate_pagination_rejects_invalid_values(
    page,
    per_page,
):
    with pytest.raises(ValidationError):
        appointment_service._validate_pagination(
            page,
            per_page,
        )


@pytest.mark.parametrize(
    "value,expected",
    [
        (
            AppointmentType.IN_PERSON,
            AppointmentType.IN_PERSON,
        ),
        (
            AppointmentType.IN_PERSON.value,
            AppointmentType.IN_PERSON,
        ),
    ],
)
def test_normalize_appointment_type(
    value,
    expected,
):
    assert (
        appointment_service._normalize_appointment_type(
            value,
        )
        == expected
    )


def test_normalize_appointment_type_rejects_invalid_value():
    with pytest.raises(
        ValidationError,
        match="Invalid appointment type",
    ):
        appointment_service._normalize_appointment_type(
            "not-valid",
        )


# ============================================================================
# _utcnow
# ============================================================================


def test_utcnow_returns_timezone_aware_utc_datetime():
    result = appointment_service._utcnow()

    assert isinstance(result, datetime)
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


# ============================================================================
# _validate_schedule_times
# ============================================================================


def test_validate_schedule_times_accepts_valid_times():
    start = datetime(
        2026,
        9,
        8,
        10,
        0,
    )

    end = datetime(
        2026,
        9,
        8,
        10,
        30,
    )

    result = appointment_service._validate_schedule_times(
        start,
        end,
    )

    assert result is None


@pytest.mark.parametrize(
    "scheduled_start,scheduled_end",
    [
        (
            None,
            datetime(2026, 9, 8, 10, 30),
        ),
        (
            datetime(2026, 9, 8, 10, 0),
            None,
        ),
        (
            None,
            None,
        ),
    ],
)
def test_validate_schedule_times_requires_both_values(
    scheduled_start,
    scheduled_end,
):
    with pytest.raises(
        ValidationError,
        match=(
            "scheduled_start and scheduled_end are required"
        ),
    ):
        appointment_service._validate_schedule_times(
            scheduled_start,
            scheduled_end,
        )


def test_validate_schedule_times_rejects_equal_times():
    start = datetime(
        2026,
        9,
        8,
        10,
        0,
    )

    with pytest.raises(
        ValidationError,
        match=(
            "scheduled_end must be later than "
            "scheduled_start"
        ),
    ):
        appointment_service._validate_schedule_times(
            start,
            start,
        )


def test_validate_schedule_times_rejects_end_before_start():
    start = datetime(
        2026,
        9,
        8,
        11,
        0,
    )

    end = datetime(
        2026,
        9,
        8,
        10,
        0,
    )

    with pytest.raises(
        ValidationError,
        match=(
            "scheduled_end must be later than "
            "scheduled_start"
        ),
    ):
        appointment_service._validate_schedule_times(
            start,
            end,
        )


# ============================================================================
# _validate_reschedule_times
# ============================================================================


def test_validate_reschedule_times_accepts_valid_times():
    start = datetime(
        2026,
        9,
        9,
        11,
        0,
    )

    end = datetime(
        2026,
        9,
        9,
        11,
        30,
    )

    result = appointment_service._validate_reschedule_times(
        start,
        end,
    )

    assert result is None


@pytest.mark.parametrize(
    "new_start,new_end",
    [
        (
            None,
            datetime(2026, 9, 9, 11, 30),
        ),
        (
            datetime(2026, 9, 9, 11, 0),
            None,
        ),
        (
            None,
            None,
        ),
    ],
)
def test_validate_reschedule_times_requires_both_values(
    new_start,
    new_end,
):
    with pytest.raises(
        ValidationError,
        match="new_start and new_end are required",
    ):
        appointment_service._validate_reschedule_times(
            new_start,
            new_end,
        )


def test_validate_reschedule_times_rejects_equal_times():
    start = datetime(
        2026,
        9,
        9,
        11,
        0,
    )

    with pytest.raises(
        ValidationError,
        match="new_end must be later than new_start",
    ):
        appointment_service._validate_reschedule_times(
            start,
            start,
        )


def test_validate_reschedule_times_rejects_end_before_start():
    start = datetime(
        2026,
        9,
        9,
        12,
        0,
    )

    end = datetime(
        2026,
        9,
        9,
        11,
        0,
    )

    with pytest.raises(
        ValidationError,
        match="new_end must be later than new_start",
    ):
        appointment_service._validate_reschedule_times(
            start,
            end,
        )


# ============================================================================
# _ensure_status
# ============================================================================


def test_ensure_status_accepts_allowed_status():
    appointment = make_appointment(
        status=AppointmentStatus.SCHEDULED,
    )

    result = appointment_service._ensure_status(
        appointment,
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CONFIRMED,
    )

    assert result is None


def test_ensure_status_rejects_disallowed_status():
    appointment = make_appointment(
        appointment_id=7,
        status=AppointmentStatus.CANCELLED,
    )

    with pytest.raises(
        ConflictError,
        match="cannot perform this action",
    ):
        appointment_service._ensure_status(
            appointment,
            AppointmentStatus.SCHEDULED,
            AppointmentStatus.CONFIRMED,
        )


# ============================================================================
# _get_appointment
# ============================================================================


def test_get_appointment_returns_appointment(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=7,
    )

    class FakeQuery:
        def filter(self, *args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return appointment

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service._get_appointment(
        appointment_id=7,
    )

    assert result is appointment


def test_get_appointment_raises_not_found(
    app,
    monkeypatch,
):
    class FakeQuery:
        def filter(self, *args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return None

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    with pytest.raises(
        NotFoundError,
        match="Appointment 999 not found",
    ):
        appointment_service._get_appointment(
            appointment_id=999,
        )


def test_get_appointment_supports_clinic_filter(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=7,
        clinic_id=10,
    )

    filters = []

    class FakeQuery:
        def filter(self, *args):
            filters.extend(args)
            return self

        def first(self):
            return appointment

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service._get_appointment(
        appointment_id=7,
        clinic_id=10,
    )

    assert result is appointment
    assert len(filters) == 2


def test_get_appointment_uses_row_lock(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=7,
    )

    locked = False

    class FakeQuery:
        def filter(self, *args):
            return self

        def with_for_update(self):
            nonlocal locked
            locked = True
            return self

        def first(self):
            return appointment

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service._get_appointment(
        appointment_id=7,
        lock=True,
    )

    assert result is appointment
    assert locked is True


# ============================================================================
# _lock_clinic
# ============================================================================


def test_lock_clinic_returns_clinic(
    app,
    monkeypatch,
):
    clinic = type(
        "Clinic",
        (),
        {"id": 10},
    )()

    locked = False

    class FakeQuery:
        def filter(self, *args):
            return self

        def with_for_update(self):
            nonlocal locked
            locked = True
            return self

        def first(self):
            return clinic

    monkeypatch.setattr(
        appointment_service.Clinic,
        "query",
        FakeQuery(),
    )

    result = appointment_service._lock_clinic(
        10,
    )

    assert result is clinic
    assert locked is True


def test_lock_clinic_raises_not_found(
    app,
    monkeypatch,
):
    class FakeQuery:
        def filter(self, *args):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return None

    monkeypatch.setattr(
        appointment_service.Clinic,
        "query",
        FakeQuery(),
    )

    with pytest.raises(
        NotFoundError,
        match="Clinic 999 not found",
    ):
        appointment_service._lock_clinic(999)


# ============================================================================
# _find_patient_overlap
# ============================================================================


def test_find_patient_overlap_returns_matching_appointment(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=7,
        patient_id=20,
    )

    class FakeQuery:
        def filter(self, *args):
            return self

        def first(self):
            return appointment

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service._find_patient_overlap(
        patient_id=20,
        scheduled_start=datetime(
            2026,
            9,
            8,
            10,
            0,
        ),
        scheduled_end=datetime(
            2026,
            9,
            8,
            10,
            30,
        ),
    )

    assert result is appointment


def test_find_patient_overlap_returns_none_when_no_match(
    app,
    monkeypatch,
):
    class FakeQuery:
        def filter(self, *args):
            return self

        def first(self):
            return None

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service._find_patient_overlap(
        patient_id=20,
        scheduled_start=datetime(
            2026,
            9,
            8,
            10,
            0,
        ),
        scheduled_end=datetime(
            2026,
            9,
            8,
            10,
            30,
        ),
    )

    assert result is None


# ============================================================================
# _find_staff_overlap
# ============================================================================


def test_find_staff_overlap_returns_matching_appointment(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=8,
        staff_id=30,
    )

    class FakeQuery:
        def filter(self, *args):
            return self

        def first(self):
            return appointment

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service._find_staff_overlap(
        staff_id=30,
        scheduled_start=datetime(
            2026,
            9,
            8,
            10,
            0,
        ),
        scheduled_end=datetime(
            2026,
            9,
            8,
            10,
            30,
        ),
    )

    assert result is appointment


def test_find_staff_overlap_returns_none_when_no_match(
    app,
    monkeypatch,
):
    class FakeQuery:
        def filter(self, *args):
            return self

        def first(self):
            return None

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service._find_staff_overlap(
        staff_id=30,
        scheduled_start=datetime(
            2026,
            9,
            8,
            10,
            0,
        ),
        scheduled_end=datetime(
            2026,
            9,
            8,
            10,
            30,
        ),
    )

    assert result is None


# ============================================================================
# _ensure_no_schedule_conflict
# ============================================================================


def test_ensure_no_schedule_conflict_accepts_available_period(
    monkeypatch,
):
    monkeypatch.setattr(
        appointment_service,
        "_find_patient_overlap",
        lambda **kwargs: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "_find_staff_overlap",
        lambda **kwargs: None,
    )

    result = appointment_service._ensure_no_schedule_conflict(
        patient_id=20,
        staff_id=30,
        scheduled_start=datetime(
            2026,
            9,
            8,
            10,
            0,
        ),
        scheduled_end=datetime(
            2026,
            9,
            8,
            10,
            30,
        ),
    )

    assert result is None


def test_ensure_no_schedule_conflict_rejects_patient_conflict(
    monkeypatch,
):
    conflict = make_appointment(
        appointment_id=55,
        patient_id=20,
    )

    monkeypatch.setattr(
        appointment_service,
        "_find_patient_overlap",
        lambda **kwargs: conflict,
    )

    with pytest.raises(
        ConflictError,
        match="Patient 20 already has an appointment",
    ):
        appointment_service._ensure_no_schedule_conflict(
            patient_id=20,
            staff_id=30,
            scheduled_start=datetime(
                2026,
                9,
                8,
                10,
                0,
            ),
            scheduled_end=datetime(
                2026,
                9,
                8,
                10,
                30,
            ),
        )


def test_ensure_no_schedule_conflict_rejects_staff_conflict(
    monkeypatch,
):
    conflict = make_appointment(
        appointment_id=56,
        staff_id=30,
    )

    monkeypatch.setattr(
        appointment_service,
        "_find_patient_overlap",
        lambda **kwargs: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "_find_staff_overlap",
        lambda **kwargs: conflict,
    )

    with pytest.raises(
        ConflictError,
        match="Staff 30 already has an appointment",
    ):
        appointment_service._ensure_no_schedule_conflict(
            patient_id=20,
            staff_id=30,
            scheduled_start=datetime(
                2026,
                9,
                8,
                10,
                0,
            ),
            scheduled_end=datetime(
                2026,
                9,
                8,
                10,
                30,
            ),
        )


# ============================================================================
# create_appointment
# ============================================================================


def test_create_appointment_success(
    app,
    monkeypatch,
):
    clinic = type(
        "Clinic",
        (),
        {"id": 10},
    )()

    patient = type(
        "Patient",
        (),
        {"clinic_id": 10, "is_active": True},
    )()

    staff = type(
        "Staff",
        (),
        {"id": 30},
    )()

    monkeypatch.setattr(
        appointment_service,
        "_lock_clinic",
        lambda clinic_id: clinic,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_clinic",
        lambda clinic_id: clinic,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: patient,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_staff",
        lambda staff_id, clinic_id: staff,
    )

    monkeypatch.setattr(
        appointment_service,
        "_ensure_no_schedule_conflict",
        lambda **kwargs: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    created = {}

    real_session = appointment_service.db.session

    def fake_add(appointment):
        created["appointment"] = appointment

    def fake_flush():
        created["appointment"].id = 101

    monkeypatch.setattr(
        real_session,
        "add",
        fake_add,
    )

    monkeypatch.setattr(
        real_session,
        "flush",
        fake_flush,
    )

    start = datetime(
        2026,
        9,
        8,
        10,
        0,
    )

    end = datetime(
        2026,
        9,
        8,
        10,
        30,
    )

    result = appointment_service.create_appointment(
        clinic_id=10,
        patient_id=20,
        staff_id=30,
        scheduled_start=start,
        scheduled_end=end,
        appointment_type=AppointmentType.IN_PERSON,
        reason="Routine consultation",
        notes="Initial visit",
    )

    assert result.id == 101
    assert result.clinic_id == 10
    assert result.patient_id == 20
    assert result.staff_id == 30
    assert result.scheduled_start == start
    assert result.scheduled_end == end
    assert result.appointment_type == (
        AppointmentType.IN_PERSON
    )
    assert result.status == AppointmentStatus.SCHEDULED
    assert result.reason == "Routine consultation"
    assert result.notes == "Initial visit"


def test_create_appointment_rejects_inactive_patient(
    app,
    monkeypatch,
):
    clinic = type("Clinic", (), {"id": 10})()
    patient = type(
        "Patient",
        (),
        {"clinic_id": 10, "is_active": False},
    )()
    staff = type("Staff", (), {"id": 30})()

    monkeypatch.setattr(
        appointment_service, "_lock_clinic", lambda clinic_id: clinic
    )
    monkeypatch.setattr(
        appointment_service, "ensure_clinic_active", lambda clinic_id: None
    )
    monkeypatch.setattr(
        appointment_service, "get_clinic", lambda clinic_id: clinic
    )
    monkeypatch.setattr(
        appointment_service, "get_patient", lambda patient_id: patient
    )
    monkeypatch.setattr(
        appointment_service,
        "get_staff",
        lambda staff_id, clinic_id: staff,
    )
    monkeypatch.setattr(
        appointment_service, "_ensure_no_schedule_conflict",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        appointment_service, "create_audit_log", lambda **kwargs: None
    )

    created = {}
    session = appointment_service.db.session

    def fake_add(appointment):
        created["appointment"] = appointment

    def fake_flush():
        created["appointment"].id = 101

    monkeypatch.setattr(session, "add", fake_add)
    monkeypatch.setattr(session, "flush", fake_flush)

    with pytest.raises(ValidationError, match="inactive"):
        appointment_service.create_appointment(
            clinic_id=10,
            patient_id=20,
            staff_id=30,
            scheduled_start=datetime(2026, 9, 8, 10, 0),
            scheduled_end=datetime(2026, 9, 8, 10, 30),
        )


def test_create_appointment_accepts_string_appointment_type(
    app,
    monkeypatch,
):
    clinic = type(
        "Clinic",
        (),
        {"id": 10},
    )()

    patient = type(
        "Patient",
        (),
        {"clinic_id": 10, "is_active": True},
    )()

    staff = type(
        "Staff",
        (),
        {"id": 30},
    )()

    monkeypatch.setattr(
        appointment_service,
        "_lock_clinic",
        lambda clinic_id: clinic,
    )
    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )
    monkeypatch.setattr(
        appointment_service,
        "get_clinic",
        lambda clinic_id: clinic,
    )
    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: patient,
    )
    monkeypatch.setattr(
        appointment_service,
        "get_staff",
        lambda staff_id, clinic_id: staff,
    )
    monkeypatch.setattr(
        appointment_service,
        "_ensure_no_schedule_conflict",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    created = {}

    monkeypatch.setattr(
        appointment_service.db.session,
        "add",
        lambda appointment: created.update(
            appointment=appointment
        ),
    )

    monkeypatch.setattr(
        appointment_service.db.session,
        "flush",
        lambda: setattr(
            created["appointment"],
            "id",
            102,
        ),
    )

    result = appointment_service.create_appointment(
        clinic_id=10,
        patient_id=20,
        staff_id=30,
        scheduled_start=datetime(
            2026,
            9,
            8,
            10,
            0,
        ),
        scheduled_end=datetime(
            2026,
            9,
            8,
            10,
            30,
        ),
        appointment_type=AppointmentType.IN_PERSON.value,
    )

    assert result.appointment_type == (
        AppointmentType.IN_PERSON
    )


def test_create_appointment_rejects_invalid_schedule(app):
    with pytest.raises(
        ValidationError,
        match="scheduled_end must be later than scheduled_start",
    ):
        appointment_service.create_appointment(
            clinic_id=10,
            patient_id=20,
            staff_id=30,
            scheduled_start=datetime(
                2026,
                9,
                8,
                11,
                0,
            ),
            scheduled_end=datetime(
                2026,
                9,
                8,
                10,
                0,
            ),
        )


def test_create_appointment_rejects_invalid_ids(
    app,
):
    with pytest.raises(
        ValidationError,
        match="Clinic ID must be a positive integer",
    ):
        appointment_service.create_appointment(
            clinic_id=0,
            patient_id=20,
            staff_id=30,
            scheduled_start=datetime(
                2026,
                9,
                8,
                10,
                0,
            ),
            scheduled_end=datetime(
                2026,
                9,
                8,
                10,
                30,
            ),
        )


def test_create_appointment_rejects_patient_clinic_mismatch(
    app,
    monkeypatch,
):
    clinic = type(
        "Clinic",
        (),
        {"id": 10},
    )()

    patient = type(
        "Patient",
        (),
        {"clinic_id": 99},
    )()

    staff = type(
        "Staff",
        (),
        {"id": 30},
    )()

    monkeypatch.setattr(
        appointment_service,
        "_lock_clinic",
        lambda clinic_id: clinic,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_clinic",
        lambda clinic_id: clinic,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: patient,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_staff",
        lambda staff_id, clinic_id: staff,
    )

    with pytest.raises(
        ConflictError,
        match="does not belong to clinic 10",
    ):
        appointment_service.create_appointment(
            clinic_id=10,
            patient_id=20,
            staff_id=30,
            scheduled_start=datetime(
                2026,
                9,
                8,
                10,
                0,
            ),
            scheduled_end=datetime(
                2026,
                9,
                8,
                10,
                30,
            ),
        )


def test_create_appointment_rejects_patient_schedule_conflict(
    app,
    monkeypatch,
):
    clinic = type(
        "Clinic",
        (),
        {"id": 10},
    )()

    patient = type(
        "Patient",
        (),
        {"clinic_id": 10, "is_active": True},
    )()

    staff = type(
        "Staff",
        (),
        {},
    )()

    monkeypatch.setattr(
        appointment_service,
        "_lock_clinic",
        lambda clinic_id: clinic,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "_validate_appointment_participants",
        lambda **kwargs: (
            clinic,
            patient,
            staff,
        ),
    )

    conflict = make_appointment(
        appointment_id=88,
    )

    monkeypatch.setattr(
        appointment_service,
        "_find_patient_overlap",
        lambda **kwargs: conflict,
    )

    with pytest.raises(
        ConflictError,
        match="Patient 20 already has an appointment",
    ):
        appointment_service.create_appointment(
            clinic_id=10,
            patient_id=20,
            staff_id=30,
            scheduled_start=datetime(
                2026,
                9,
                8,
                10,
                0,
            ),
            scheduled_end=datetime(
                2026,
                9,
                8,
                10,
                30,
            ),
        )


# ============================================================================
# Reschedule
# ============================================================================


def test_reschedule_appointment_success(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=5,
        status=AppointmentStatus.SCHEDULED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )
    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: type(
            "Patient",
            (),
            {"id": 20, "clinic_id": 10, "is_active": True},
        )(),
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "_ensure_no_schedule_conflict",
        lambda **kwargs: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    new_start = datetime(
        2026,
        9,
        9,
        11,
        0,
    )

    new_end = datetime(
        2026,
        9,
        9,
        11,
        30,
    )

    appointment.reminder_sent = True

    result = appointment_service.reschedule_appointment(
        appointment_id=5,
        new_start=new_start,
        new_end=new_end,
        clinic_id=10,
    )

    assert result is appointment
    assert result.scheduled_start == new_start
    assert result.scheduled_end == new_end
    assert result.reminder_sent is False


def test_reschedule_appointment_rejects_inactive_patient(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=5,
        status=AppointmentStatus.SCHEDULED,
    )
    patient = type(
        "Patient",
        (),
        {"id": 20, "clinic_id": 10, "is_active": False},
    )()

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )
    monkeypatch.setattr(
        appointment_service, "ensure_clinic_active", lambda clinic_id: None
    )
    monkeypatch.setattr(
        appointment_service, "get_patient", lambda patient_id: patient
    )

    with pytest.raises(ValidationError, match="inactive"):
        appointment_service.reschedule_appointment(
            appointment_id=5,
            new_start=datetime(2026, 9, 9, 11, 0),
            new_end=datetime(2026, 9, 9, 11, 30),
            clinic_id=10,
        )


def test_reschedule_appointment_rejects_invalid_status(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=5,
        status=AppointmentStatus.CANCELLED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    with pytest.raises(
        ConflictError,
        match="cannot perform this action",
    ):
        appointment_service.reschedule_appointment(
            appointment_id=5,
            new_start=datetime(
                2026,
                9,
                9,
                11,
                0,
            ),
            new_end=datetime(
                2026,
                9,
                9,
                11,
                30,
            ),
            clinic_id=10,
        )


def test_reschedule_appointment_rejects_invalid_times(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=5,
        status=AppointmentStatus.SCHEDULED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    with pytest.raises(
        ValidationError,
        match="new_end must be later than new_start",
    ):
        appointment_service.reschedule_appointment(
            appointment_id=5,
            new_start=datetime(
                2026,
                9,
                9,
                12,
                0,
            ),
            new_end=datetime(
                2026,
                9,
                9,
                11,
                0,
            ),
            clinic_id=10,
        )


# ============================================================================
# Confirm
# ============================================================================


def test_confirm_appointment_success(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=6,
        status=AppointmentStatus.SCHEDULED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )
    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: type(
            "Patient",
            (),
            {"id": 20, "clinic_id": 10, "is_active": True},
        )(),
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = appointment_service.confirm_appointment(
        appointment_id=6,
        clinic_id=10,
    )

    assert result is appointment
    assert result.status == AppointmentStatus.CONFIRMED


def test_confirm_appointment_rejects_inactive_patient(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=6,
        status=AppointmentStatus.SCHEDULED,
    )
    patient = type(
        "Patient",
        (),
        {"id": 20, "clinic_id": 10, "is_active": False},
    )()

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )
    monkeypatch.setattr(
        appointment_service, "ensure_clinic_active", lambda clinic_id: None
    )
    monkeypatch.setattr(
        appointment_service, "get_patient", lambda patient_id: patient
    )

    with pytest.raises(ValidationError, match="inactive"):
        appointment_service.confirm_appointment(
            appointment_id=6,
            clinic_id=10,
        )


def test_confirm_appointment_rejects_non_scheduled_status(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=6,
        status=AppointmentStatus.COMPLETED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    with pytest.raises(
        ConflictError,
        match="cannot perform this action",
    ):
        appointment_service.confirm_appointment(
            appointment_id=6,
            clinic_id=10,
        )


# ============================================================================
# Cancel
# ============================================================================


def test_cancel_appointment_success(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=7,
        status=AppointmentStatus.SCHEDULED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = appointment_service.cancel_appointment(
        appointment_id=7,
        reason="Patient unavailable",
        clinic_id=10,
    )

    assert result is appointment
    assert result.status == AppointmentStatus.CANCELLED
    assert result.cancellation_reason == (
        "Patient unavailable"
    )
    assert result.cancelled_at is not None
    assert result.cancelled_at.tzinfo is not None


def test_cancel_appointment_accepts_no_reason(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=8,
        status=AppointmentStatus.CONFIRMED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = appointment_service.cancel_appointment(
        appointment_id=8,
        clinic_id=10,
    )

    assert result.status == AppointmentStatus.CANCELLED
    assert result.cancellation_reason is None


def test_cancel_appointment_rejects_completed_status(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=8,
        status=AppointmentStatus.COMPLETED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    with pytest.raises(
        ConflictError,
        match="cannot perform this action",
    ):
        appointment_service.cancel_appointment(
            appointment_id=8,
            clinic_id=10,
        )


# ============================================================================
# Complete
# ============================================================================


def test_complete_appointment_success(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=9,
        status=AppointmentStatus.CONFIRMED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = appointment_service.complete_appointment(
        appointment_id=9,
        notes="Consultation completed",
        clinic_id=10,
    )

    assert result is appointment
    assert result.status == AppointmentStatus.COMPLETED
    assert result.notes == "Consultation completed"


def test_complete_appointment_without_notes_preserves_existing_notes(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=10,
        status=AppointmentStatus.CONFIRMED,
        notes="Existing notes",
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = appointment_service.complete_appointment(
        appointment_id=10,
        notes=None,
        clinic_id=10,
    )

    assert result.status == AppointmentStatus.COMPLETED
    assert result.notes == "Existing notes"


def test_complete_appointment_rejects_scheduled_status(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=10,
        status=AppointmentStatus.SCHEDULED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    with pytest.raises(
        ConflictError,
        match="cannot perform this action",
    ):
        appointment_service.complete_appointment(
            appointment_id=10,
            clinic_id=10,
        )


# ============================================================================
# No-show
# ============================================================================


def test_mark_no_show_success(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=11,
        status=AppointmentStatus.CONFIRMED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = appointment_service.mark_no_show(
        appointment_id=11,
        clinic_id=10,
    )

    assert result is appointment
    assert result.status == AppointmentStatus.NO_SHOW


def test_mark_no_show_rejects_scheduled_status(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=11,
        status=AppointmentStatus.SCHEDULED,
    )

    monkeypatch.setattr(
        appointment_service,
        "_get_appointment",
        lambda **kwargs: appointment,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    with pytest.raises(
        ConflictError,
        match="cannot perform this action",
    ):
        appointment_service.mark_no_show(
            appointment_id=11,
            clinic_id=10,
        )


# ============================================================================
# Patient appointments
# ============================================================================


def test_get_appointments_for_patient_success(
    app,
    monkeypatch,
):
    patient = type(
        "Patient",
        (),
        {"clinic_id": 10, "is_active": False},
    )()

    appointments = [
        make_appointment(
            appointment_id=1,
            patient_id=20,
        ),
        make_appointment(
            appointment_id=2,
            patient_id=20,
        ),
    ]

    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: patient,
    )

    pagination = make_pagination(
        appointments,
        page=1,
        per_page=50,
        total=2,
    )

    class FakeQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def paginate(
            self,
            page,
            per_page,
            error_out,
        ):
            assert page == 1
            assert per_page == 50
            assert error_out is False
            return pagination

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service.get_appointments_for_patient(
        patient_id=20,
        clinic_id=10,
    )

    assert result is pagination
    assert result.items == appointments
    assert result.total == 2


def test_get_appointments_for_patient_supports_pagination(
    app,
    monkeypatch,
):
    patient = type(
        "Patient",
        (),
        {"clinic_id": 10, "is_active": True},
    )()

    pagination = make_pagination(
        [
            make_appointment(
                appointment_id=51,
                patient_id=20,
            )
        ],
        page=2,
        per_page=25,
        total=51,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: patient,
    )

    class FakeQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def paginate(
            self,
            page,
            per_page,
            error_out,
        ):
            assert page == 2
            assert per_page == 25
            assert error_out is False
            return pagination

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service.get_appointments_for_patient(
        patient_id=20,
        clinic_id=10,
        page=2,
        per_page=25,
    )

    assert result.page == 2
    assert result.per_page == 25
    assert result.total == 51
    assert result.has_next is True
    assert result.has_prev is True


def test_get_appointments_for_patient_rejects_wrong_clinic(
    monkeypatch,
):
    patient = type(
        "Patient",
        (),
        {"clinic_id": 99},
    )()

    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: patient,
    )

    with pytest.raises(
        NotFoundError,
        match="Patient 20 not found",
    ):
        appointment_service.get_appointments_for_patient(
            patient_id=20,
            clinic_id=10,
        )


def test_get_appointments_for_patient_rejects_invalid_pagination(
    app,
    monkeypatch,
):
    patient = type(
        "Patient",
        (),
        {"clinic_id": 10, "is_active": True},
    )()

    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: patient,
    )

    with pytest.raises(
        ValidationError,
    ):
        appointment_service.get_appointments_for_patient(
            patient_id=20,
            clinic_id=10,
            page=0,
        )


# ============================================================================
# Staff appointments
# ============================================================================


def test_get_appointments_for_staff_success(
    app,
    monkeypatch,
):
    appointments = [
        make_appointment(
            appointment_id=1,
            clinic_id=10,
            staff_id=30,
        ),
        make_appointment(
            appointment_id=2,
            clinic_id=10,
            staff_id=30,
        ),
    ]

    monkeypatch.setattr(
        appointment_service,
        "get_staff",
        lambda staff_id, clinic_id: object(),
    )

    pagination = make_pagination(
        appointments,
        page=1,
        per_page=50,
        total=2,
    )

    class FakeQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def paginate(
            self,
            page,
            per_page,
            error_out,
        ):
            assert page == 1
            assert per_page == 50
            assert error_out is False
            return pagination

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service.get_appointments_for_staff(
        clinic_id=10,
        staff_id=30,
    )

    assert result is pagination
    assert result.items == appointments


def test_get_appointments_for_staff_supports_pagination(
    app,
    monkeypatch,
):
    monkeypatch.setattr(
        appointment_service,
        "get_staff",
        lambda staff_id, clinic_id: object(),
    )

    pagination = make_pagination(
        [
            make_appointment(
                appointment_id=51,
                clinic_id=10,
                staff_id=30,
            )
        ],
        page=2,
        per_page=25,
        total=51,
    )

    class FakeQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def paginate(
            self,
            page,
            per_page,
            error_out,
        ):
            assert page == 2
            assert per_page == 25
            assert error_out is False
            return pagination

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service.get_appointments_for_staff(
        clinic_id=10,
        staff_id=30,
        page=2,
        per_page=25,
    )

    assert result.page == 2
    assert result.per_page == 25
    assert result.total == 51
    assert result.pages == 3


def test_get_appointments_for_staff_with_date_filter(
    app,
    monkeypatch,
):
    target_date = date(
        2026,
        9,
        8,
    )

    appointments = [
        make_appointment(
            appointment_id=1,
            clinic_id=10,
            staff_id=30,
        ),
    ]

    monkeypatch.setattr(
        appointment_service,
        "get_staff",
        lambda staff_id, clinic_id: object(),
    )

    pagination = make_pagination(
        appointments,
    )

    class FakeQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def paginate(
            self,
            page,
            per_page,
            error_out,
        ):
            return pagination

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    result = appointment_service.get_appointments_for_staff(
        clinic_id=10,
        staff_id=30,
        date_=target_date,
    )

    assert result.items == appointments


@pytest.mark.parametrize(
    "clinic_id,staff_id",
    [
        (0, 30),
        (10, 0),
        (-1, 30),
        (10, -1),
    ],
)
def test_get_appointments_for_staff_rejects_invalid_ids(
    clinic_id,
    staff_id,
):
    with pytest.raises(
        ValidationError,
    ):
        appointment_service.get_appointments_for_staff(
            clinic_id=clinic_id,
            staff_id=staff_id,
        )


# ============================================================================
# Reminder task
# ============================================================================


def test_send_appointment_reminder_ignores_missing_appointment(
    app,
    monkeypatch,
):
    monkeypatch.setattr(
        appointment_service.db.session,
        "get",
        lambda model, appointment_id: None,
    )

    result = appointment_service.send_appointment_reminder(
        10,
        999,
    )

    assert result is None


def test_send_appointment_reminder_ignores_already_sent_reminder(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=12,
        reminder_sent=True,
    )

    monkeypatch.setattr(
        appointment_service.db.session,
        "get",
        lambda model, appointment_id: appointment,
    )

    result = appointment_service.send_appointment_reminder(
        10,
        12,
    )

    assert result is None
    assert appointment.reminder_sent is True


def test_send_appointment_reminder_marks_reminder_sent(
    app,
    monkeypatch,
):
    appointment = make_appointment(
        appointment_id=13,
        reminder_sent=False,
    )

    committed = False

    monkeypatch.setattr(
        appointment_service.db.session,
        "get",
        lambda model, appointment_id: appointment,
    )

    def fake_commit():
        nonlocal committed
        committed = True

    monkeypatch.setattr(
        appointment_service.db.session,
        "commit",
        fake_commit,
    )

    result = appointment_service.send_appointment_reminder(
        10,
        13,
    )

    assert result is None
    assert appointment.reminder_sent is True
    assert committed is True


def test_send_appointment_reminder_rejects_invalid_id(
    app,
):
    with pytest.raises(
        ValidationError,
        match="Appointment ID must be a positive integer",
    ):
        appointment_service.send_appointment_reminder(10, 0)


# ============================================================================
# Upcoming appointment reminder task
# ============================================================================


def test_check_upcoming_appointments_queues_reminders(
    app,
    monkeypatch,
):
    appointment_one = make_appointment(
        appointment_id=20,
        status=AppointmentStatus.SCHEDULED,
        reminder_sent=False,
    )

    appointment_two = make_appointment(
        appointment_id=21,
        status=AppointmentStatus.CONFIRMED,
        reminder_sent=False,
    )

    appointments = [
        appointment_one,
        appointment_two,
    ]

    class FakeQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def all(self):
            return appointments

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    queued = []

    class FakeReminderTask:
        def delay(self, clinic_id, appointment_id):
            queued.append((clinic_id, appointment_id))

    monkeypatch.setattr(
        appointment_service,
        "send_appointment_reminder",
        FakeReminderTask(),
    )

    appointment_service.check_upcoming_appointments()

    assert queued == [
        (10, 20),
        (10, 21),
    ]


def test_check_upcoming_appointments_handles_no_upcoming_appointments(
    app,
    monkeypatch,
):
    class FakeQuery:
        def filter(self, *args):
            return self

        def order_by(self, *args):
            return self

        def all(self):
            return []

    monkeypatch.setattr(
        appointment_service.Appointment,
        "query",
        FakeQuery(),
    )

    queued = []

    class FakeReminderTask:
        def delay(self, clinic_id, appointment_id):
            queued.append((clinic_id, appointment_id))

    monkeypatch.setattr(
        appointment_service,
        "send_appointment_reminder",
        FakeReminderTask(),
    )

    appointment_service.check_upcoming_appointments()

    assert queued == []
# ============================================================================
# Phase 9 Gate 3 — idempotency
# ============================================================================


def test_create_appointment_idempotency_replays_existing_appointment(
    app,
    monkeypatch,
):
    existing = type(
        "ExistingAppointment",
        (),
        {
            "id": 101,
            "clinic_id": 10,
        },
    )()

    record = type(
        "IdempotencyRecord",
        (),
        {
            "entity_type": "Appointment",
            "entity_id": 101,
        },
    )()

    captured = {}

    def fake_reserve(**kwargs):
        captured.update(kwargs)
        return record, False

    monkeypatch.setattr(
        appointment_service,
        "reserve_idempotency_operation",
        fake_reserve,
    )

    monkeypatch.setattr(
        appointment_service.db.session,
        "get",
        lambda model, entity_id: existing,
    )

    start = datetime(2026, 9, 8, 11, 0)
    end = datetime(2026, 9, 8, 11, 30)

    result = appointment_service.create_appointment(
        clinic_id=10,
        patient_id=20,
        staff_id=30,
        scheduled_start=start,
        scheduled_end=end,
        appointment_type=AppointmentType.EMERGENCY,
        reason="Priority appointment",
        notes="Replay test",
        idempotency_key="appointment-service-001",
        idempotency_user_id=50,
    )

    assert result is existing
    assert captured["operation"] == "appointment.create"
    assert captured["idempotency_key"] == (
        "appointment-service-001"
    )
    assert captured["user_id"] == 50


def test_create_appointment_idempotency_binds_created_appointment(
    app,
    monkeypatch,
):
    clinic = type(
        "Clinic",
        (),
        {"id": 10},
    )()

    patient = type(
        "Patient",
        (),
        {"clinic_id": 10, "is_active": True},
    )()

    staff = type(
        "Staff",
        (),
        {"id": 30},
    )()

    record = type(
        "IdempotencyRecord",
        (),
        {
            "entity_type": None,
            "entity_id": None,
        },
    )()

    monkeypatch.setattr(
        appointment_service,
        "reserve_idempotency_operation",
        lambda **kwargs: (record, True),
    )

    monkeypatch.setattr(
        appointment_service,
        "_lock_clinic",
        lambda clinic_id: clinic,
    )

    monkeypatch.setattr(
        appointment_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_clinic",
        lambda clinic_id: clinic,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_patient",
        lambda patient_id: patient,
    )

    monkeypatch.setattr(
        appointment_service,
        "get_staff",
        lambda staff_id, clinic_id: staff,
    )

    monkeypatch.setattr(
        appointment_service,
        "_ensure_no_schedule_conflict",
        lambda **kwargs: None,
    )

    monkeypatch.setattr(
        appointment_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    created = {}

    monkeypatch.setattr(
        appointment_service.db.session,
        "add",
        lambda appointment: created.update(
            appointment=appointment
        ),
    )

    def fake_flush():
        created["appointment"].id = 202

    monkeypatch.setattr(
        appointment_service.db.session,
        "flush",
        fake_flush,
    )

    result = appointment_service.create_appointment(
        clinic_id=10,
        patient_id=20,
        staff_id=30,
        scheduled_start=datetime(2026, 9, 8, 11, 0),
        scheduled_end=datetime(2026, 9, 8, 11, 30),
        appointment_type=AppointmentType.EMERGENCY,
        reason="Priority appointment",
        idempotency_key="appointment-service-002",
        idempotency_user_id=50,
    )

    assert result.id == 202
    assert record.entity_type == "Appointment"
    assert record.entity_id == 202
