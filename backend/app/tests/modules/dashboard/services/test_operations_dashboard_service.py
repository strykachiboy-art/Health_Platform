from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.ambulance_enums import (
    EquipmentLevel,
    TripStatus,
    TripType,
    VehicleStatus,
)
from app.core.enums.appointment_enums import (
    AppointmentStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import (
    StaffStatus,
)
from app.core.exceptions import ValidationError

from app.modules.ambulance.models.ambulance_model import (
    AmbulanceTrip,
    AmbulanceVehicle,
)
from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardActivitySchema,
    DashboardChatSummarySchema,
    DashboardQuerySchema,
)
from app.modules.dashboard.services import (
    operations_dashboard_service,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_appointment(
    db_session,
    clinic,
    patient,
    staff,
    scheduled_start: datetime,
    status: AppointmentStatus,
):
    appointment = Appointment(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_start + timedelta(hours=1),
        status=status,
    )

    db_session.add(appointment)
    db_session.flush()

    return appointment


def _make_vehicle(
    db_session,
    clinic,
    *,
    plate_number: str,
    status: VehicleStatus,
):
    vehicle = AmbulanceVehicle(
        clinic_id=clinic.id,
        plate_number=plate_number,
        equipment_level=EquipmentLevel.BLS,
        capacity=4,
        status=status,
    )

    db_session.add(vehicle)
    db_session.flush()

    return vehicle


def _make_trip(
    db_session,
    clinic,
    *,
    status: TripStatus,
):
    trip = AmbulanceTrip(
        clinic_id=clinic.id,
        trip_type=TripType.EMERGENCY_PICKUP,
        status=status,
    )

    db_session.add(trip)
    db_session.flush()

    return trip


@pytest.fixture
def dashboard_helpers(monkeypatch):
    chat = DashboardChatSummarySchema(
        unread_messages=0,
        unread_conversations=0,
        mentions=0,
        priority_messages=0,
        recent_messages=0,
    )

    activity = []

    chat_mock = Mock(return_value=chat)
    activity_mock = Mock(return_value=activity)

    monkeypatch.setattr(
        operations_dashboard_service,
        "build_chat_summary",
        chat_mock,
    )

    monkeypatch.setattr(
        operations_dashboard_service,
        "build_recent_activity",
        activity_mock,
    )

    return SimpleNamespace(
        chat=chat,
        activity=activity,
        chat_mock=chat_mock,
        activity_mock=activity_mock,
    )


@pytest.fixture
def operations_actor(
    make_user,
    clinic,
):
    actor = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
    )

    actor.clinic = clinic

    return actor


def test_rejects_non_operations_role(
    operations_actor,
    dashboard_helpers,
):
    operations_actor.role = Role.DOCTOR

    with pytest.raises(
        ValidationError,
        match="Operations dashboard is not available",
    ):
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )


@pytest.mark.parametrize(
    "role",
    [
        Role.ADMIN,
        Role.SUPER_ADMIN,
        Role.DOCTOR,
        Role.NURSE,
        Role.PHARMACIST,
        Role.LAB_TECHNICIAN,
        Role.ACCOUNTANT,
        Role.PATIENT,
    ],
)
def test_rejects_all_non_operations_roles(
    make_user,
    clinic,
    dashboard_helpers,
    role,
):
    actor = make_user(
        clinic=clinic,
        role=role,
    )

    with pytest.raises(
        ValidationError,
        match="Operations dashboard is not available",
    ):
        operations_dashboard_service.get_operations_dashboard(
            actor=actor,
        )


@pytest.mark.parametrize(
    "role",
    [
        Role.RECEPTIONIST,
        Role.DRIVER,
        Role.AMBULANCE_DISPATCHER,
        Role.AMBULANCE_COORDINATOR,
        Role.OTHER,
    ],
)
def test_all_operations_roles_are_allowed(
    make_user,
    clinic,
    dashboard_helpers,
    role,
):
    actor = make_user(
        clinic=clinic,
        role=role,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=actor,
        )
    )

    assert result.context.role is role
    assert result.context.scope == "clinic"
    assert result.context.clinic_id == clinic.id


def test_rejects_inactive_user(
    make_user,
    clinic,
    dashboard_helpers,
):
    actor = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        is_active=False,
    )

    with pytest.raises(
        ValidationError,
        match="User account is inactive",
    ):
        operations_dashboard_service.get_operations_dashboard(
            actor=actor,
        )


def test_rejects_missing_clinic_id(
    dashboard_helpers,
):
    actor = SimpleNamespace(
        id=1,
        clinic_id=None,
        role=Role.RECEPTIONIST,
        is_active=True,
    )

    with pytest.raises(
        ValidationError,
        match="not associated with a valid clinic",
    ):
        operations_dashboard_service.get_operations_dashboard(
            actor=actor,
        )


@pytest.mark.parametrize(
    "clinic_id",
    [
        0,
        -1,
    ],
)
def test_rejects_invalid_clinic_id(
    dashboard_helpers,
    clinic_id,
):
    actor = SimpleNamespace(
        id=1,
        clinic_id=clinic_id,
        role=Role.RECEPTIONIST,
        is_active=True,
    )

    with pytest.raises(
        ValidationError,
        match="not associated with a valid clinic",
    ):
        operations_dashboard_service.get_operations_dashboard(
            actor=actor,
        )


def test_role_membership_does_not_require_staff_profile(
    make_user,
    clinic,
    dashboard_helpers,
):
    actor = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=actor,
        )
    )

    assert result.context.clinic_id == clinic.id


def test_empty_dashboard_returns_zero_counts(
    operations_actor,
    dashboard_helpers,
):
    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    overview = result.overview

    assert overview.appointments_today == 0
    assert overview.missed_appointments_today == 0
    assert overview.scheduled_appointments_today == 0
    assert overview.confirmed_appointments_today == 0
    assert overview.active_ambulance_trips == 0
    assert overview.pending_ambulance_requests == 0
    assert overview.active_staff == 0

    assert result.chat is dashboard_helpers.chat
    assert result.alerts == []
    assert result.recent_activity == []


def test_default_period_uses_current_day(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
        status=StaffStatus.ACTIVE,
    )

    today = _utcnow().date()

    today_start = datetime.combine(
        today,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=10)

    yesterday_start = today_start - timedelta(days=1)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        today_start,
        AppointmentStatus.SCHEDULED,
    )

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        yesterday_start,
        AppointmentStatus.CONFIRMED,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.appointments_today == 1
    assert result.overview.missed_appointments_today == 0
    assert result.overview.scheduled_appointments_today == 1
    assert result.overview.confirmed_appointments_today == 0


def test_explicit_period_filters_appointments(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
        status=StaffStatus.ACTIVE,
    )

    target_date = _utcnow().date() - timedelta(days=2)

    inside_start = datetime.combine(
        target_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=9)

    outside_start = inside_start + timedelta(days=3)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        inside_start,
        AppointmentStatus.SCHEDULED,
    )

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        outside_start,
        AppointmentStatus.CONFIRMED,
    )

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        inside_start + timedelta(hours=2),
        AppointmentStatus.NO_SHOW,
    )

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        outside_start + timedelta(hours=1),
        AppointmentStatus.NO_SHOW,
    )

    query = DashboardQuerySchema(
        date_from=target_date,
        date_to=target_date,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
            query=query,
        )
    )

    assert result.overview.appointments_today == 1
    assert result.overview.missed_appointments_today == 1
    assert result.overview.scheduled_appointments_today == 1
    assert result.overview.confirmed_appointments_today == 0

    metrics = {
        item.key: item
        for item in result.metrics
    }

    assert metrics["missed_appointments"].value == 1
    assert metrics["missed_appointments"].unit == "appointments"

def test_single_sided_date_from_resolves_to_same_day(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
    )

    target_date = _utcnow().date()

    start = datetime.combine(
        target_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=8)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        start,
        AppointmentStatus.SCHEDULED,
    )

    query = DashboardQuerySchema(
        date_from=target_date,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
            query=query,
        )
    )

    assert result.overview.appointments_today == 1
    assert result.overview.missed_appointments_today == 0


def test_single_sided_date_to_resolves_to_same_day(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
    )

    target_date = _utcnow().date()

    start = datetime.combine(
        target_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=11)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        start,
        AppointmentStatus.CONFIRMED,
    )

    query = DashboardQuerySchema(
        date_to=target_date,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
            query=query,
        )
    )

    assert result.overview.appointments_today == 1
    assert result.overview.missed_appointments_today == 0
    assert result.overview.confirmed_appointments_today == 1


@pytest.mark.parametrize(
    "status",
    [
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CONFIRMED,
    ],
)
def test_counts_supported_appointment_statuses(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
    status,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
    )

    today = _utcnow().date()

    start = datetime.combine(
        today,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=9)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        start,
        status,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.appointments_today == 1

    if status is AppointmentStatus.SCHEDULED:
        assert result.overview.scheduled_appointments_today == 1
        assert result.overview.confirmed_appointments_today == 0
    else:
        assert result.overview.scheduled_appointments_today == 0
        assert result.overview.confirmed_appointments_today == 1


def test_counts_no_show_appointments_as_missed(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
    )

    today = _utcnow().date()

    start = datetime.combine(
        today,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=9)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        start,
        AppointmentStatus.NO_SHOW,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.appointments_today == 0
    assert result.overview.missed_appointments_today == 1
    assert (
        next(
            metric
            for metric in result.metrics
            if metric.key == "missed_appointments"
        ).value
        == 1
    )

    metrics = {
        item.key: item
        for item in result.metrics
    }

    assert metrics["missed_appointments"].value == 1
    assert metrics["missed_appointments"].unit == "appointments"


def test_excludes_cancelled_appointments(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
    )

    today = _utcnow().date()

    start = datetime.combine(
        today,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=9)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        start,
        AppointmentStatus.CANCELLED,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.appointments_today == 0
    assert result.overview.missed_appointments_today == 0
    assert result.overview.scheduled_appointments_today == 0
    assert result.overview.confirmed_appointments_today == 0


@pytest.mark.parametrize(
    "status",
    [
        TripStatus.REQUESTED,
        TripStatus.DISPATCHED,
        TripStatus.EN_ROUTE_TO_PICKUP,
        TripStatus.AT_PICKUP,
        TripStatus.PATIENT_ON_BOARD,
        TripStatus.EN_ROUTE_TO_DESTINATION,
    ],
)
def test_counts_all_active_ambulance_trip_statuses(
    db_session,
    operations_actor,
    dashboard_helpers,
    status,
):
    _make_trip(
        db_session,
        operations_actor.clinic,
        status=status,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.active_ambulance_trips == 1

    if status is TripStatus.REQUESTED:
        assert result.overview.pending_ambulance_requests == 1
    else:
        assert result.overview.pending_ambulance_requests == 0


@pytest.mark.parametrize(
    "status",
    [
        TripStatus.COMPLETED,
        TripStatus.CANCELLED,
    ],
)
def test_excludes_terminal_ambulance_trip_statuses(
    db_session,
    operations_actor,
    dashboard_helpers,
    status,
):
    _make_trip(
        db_session,
        operations_actor.clinic,
        status=status,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.active_ambulance_trips == 0
    assert result.overview.pending_ambulance_requests == 0


def test_pending_requests_are_subset_of_active_trips(
    db_session,
    operations_actor,
    dashboard_helpers,
):
    _make_trip(
        db_session,
        operations_actor.clinic,
        status=TripStatus.REQUESTED,
    )

    _make_trip(
        db_session,
        operations_actor.clinic,
        status=TripStatus.DISPATCHED,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.active_ambulance_trips == 2
    assert result.overview.pending_ambulance_requests == 1


def test_counts_active_staff_only(
    db_session,
    operations_actor,
    make_staff,
    dashboard_helpers,
):
    make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
        status=StaffStatus.ACTIVE,
    )

    make_staff(
        clinic=operations_actor.clinic,
        role=Role.DRIVER,
        status=StaffStatus.ON_LEAVE,
    )

    make_staff(
        clinic=operations_actor.clinic,
        role=Role.AMBULANCE_DISPATCHER,
        status=StaffStatus.SUSPENDED,
    )

    make_staff(
        clinic=operations_actor.clinic,
        role=Role.AMBULANCE_COORDINATOR,
        status=StaffStatus.TERMINATED,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.active_staff == 1


@pytest.mark.parametrize(
    "status",
    [
        StaffStatus.ON_LEAVE,
        StaffStatus.SUSPENDED,
        StaffStatus.TERMINATED,
    ],
)
def test_non_active_staff_statuses_are_excluded(
    db_session,
    operations_actor,
    make_staff,
    dashboard_helpers,
    status,
):
    make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
        status=status,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.active_staff == 0


def test_vehicle_status_counts_feed_metrics(
    db_session,
    operations_actor,
    dashboard_helpers,
):
    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-001",
        status=VehicleStatus.AVAILABLE,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-002",
        status=VehicleStatus.AVAILABLE,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-003",
        status=VehicleStatus.MAINTENANCE,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-004",
        status=VehicleStatus.ON_TRIP,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-005",
        status=VehicleStatus.OUT_OF_SERVICE,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    metrics = {
        item.key: item
        for item in result.metrics
    }

    assert metrics["available_ambulances"].value == 2
    assert metrics["maintenance_ambulances"].value == 1


def test_vehicle_counts_are_clinic_scoped(
    db_session,
    operations_actor,
    make_clinic,
    dashboard_helpers,
):
    other_clinic = make_clinic(
        name="Other Operations Clinic",
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-LOCAL",
        status=VehicleStatus.AVAILABLE,
    )

    _make_vehicle(
        db_session,
        other_clinic,
        plate_number="OPS-OTHER",
        status=VehicleStatus.AVAILABLE,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-MAINT-LOCAL",
        status=VehicleStatus.MAINTENANCE,
    )

    _make_vehicle(
        db_session,
        other_clinic,
        plate_number="OPS-MAINT-OTHER",
        status=VehicleStatus.MAINTENANCE,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    metrics = {
        item.key: item
        for item in result.metrics
    }

    assert metrics["available_ambulances"].value == 1
    assert metrics["maintenance_ambulances"].value == 1


def test_appointment_counts_are_clinic_scoped(
    db_session,
    operations_actor,
    make_clinic,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    other_clinic = make_clinic(
        name="Other Appointment Clinic",
    )

    local_patient = make_patient(
        clinic=operations_actor.clinic,
    )

    local_staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
    )

    other_patient = make_patient(
        clinic=other_clinic,
    )

    other_staff = make_staff(
        clinic=other_clinic,
        role=Role.RECEPTIONIST,
    )

    today = _utcnow().date()

    local_start = datetime.combine(
        today,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=10)

    other_start = local_start + timedelta(hours=1)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        local_patient,
        local_staff,
        local_start,
        AppointmentStatus.CONFIRMED,
    )

    _make_appointment(
        db_session,
        other_clinic,
        other_patient,
        other_staff,
        other_start,
        AppointmentStatus.CONFIRMED,
    )

    _make_appointment(
        db_session,
        other_clinic,
        other_patient,
        other_staff,
        other_start + timedelta(hours=1),
        AppointmentStatus.NO_SHOW,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.appointments_today == 1
    assert result.overview.missed_appointments_today == 0
    assert result.overview.missed_appointments_today == 0
    assert result.overview.confirmed_appointments_today == 1


def test_staff_counts_are_clinic_scoped(
    operations_actor,
    make_clinic,
    make_staff,
    dashboard_helpers,
):
    other_clinic = make_clinic(
        name="Other Staff Clinic",
    )

    make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
        status=StaffStatus.ACTIVE,
    )

    make_staff(
        clinic=other_clinic,
        role=Role.RECEPTIONIST,
        status=StaffStatus.ACTIVE,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.active_staff == 1


def test_ambulance_trip_counts_are_clinic_scoped(
    db_session,
    operations_actor,
    make_clinic,
    dashboard_helpers,
):
    other_clinic = make_clinic(
        name="Other Ambulance Clinic",
    )

    _make_trip(
        db_session,
        operations_actor.clinic,
        status=TripStatus.REQUESTED,
    )

    _make_trip(
        db_session,
        other_clinic,
        status=TripStatus.REQUESTED,
    )

    _make_trip(
        db_session,
        other_clinic,
        status=TripStatus.DISPATCHED,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.active_ambulance_trips == 1
    assert result.overview.pending_ambulance_requests == 1


def test_chat_summary_is_requested_with_actor_and_clinic_scope(
    operations_actor,
    dashboard_helpers,
):
    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    dashboard_helpers.chat_mock.assert_called_once()

    kwargs = (
        dashboard_helpers.chat_mock.call_args.kwargs
    )

    assert kwargs["user_id"] == operations_actor.id
    assert kwargs["clinic_id"] == operations_actor.clinic_id

    assert kwargs["period"].date_from == _utcnow().date()
    assert kwargs["period"].date_to == _utcnow().date()

    assert result.chat is dashboard_helpers.chat


def test_chat_summary_uses_explicit_period(
    operations_actor,
    dashboard_helpers,
):
    target_date = _utcnow().date() - timedelta(days=3)

    query = DashboardQuerySchema(
        date_from=target_date,
        date_to=target_date,
    )

    operations_dashboard_service.get_operations_dashboard(
        actor=operations_actor,
        query=query,
    )

    dashboard_helpers.chat_mock.assert_called_once()

    period = (
        dashboard_helpers.chat_mock.call_args.kwargs[
            "period"
        ]
    )

    assert period.date_from == target_date
    assert period.date_to == target_date


def test_recent_activity_is_requested_for_clinic(
    operations_actor,
    dashboard_helpers,
):
    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    dashboard_helpers.activity_mock.assert_called_once_with(
        clinic_id=operations_actor.clinic_id,
    )

    assert result.recent_activity == (
        dashboard_helpers.activity_mock.return_value
    )


def test_metrics_have_expected_keys(
    operations_actor,
    dashboard_helpers,
):
    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert [metric.key for metric in result.metrics] == [
        "appointments",
        "missed_appointments",
        "active_ambulance_trips",
        "available_ambulances",
        "maintenance_ambulances",
        "unread_chat_messages",
        "hospital_active_admissions",
        "hospital_available_beds",
    ]


def test_metrics_have_expected_units(
    operations_actor,
    dashboard_helpers,
):
    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    metrics = {
        item.key: item
        for item in result.metrics
    }

    assert metrics["appointments"].unit == "appointments"
    assert metrics["missed_appointments"].unit == "appointments"
    assert metrics["active_ambulance_trips"].unit == "trips"
    assert metrics["available_ambulances"].unit == "vehicles"
    assert metrics["maintenance_ambulances"].unit == "vehicles"
    assert metrics["unread_chat_messages"].unit == "messages"


def test_metrics_reflect_dashboard_counts(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
        status=StaffStatus.ACTIVE,
    )

    today = _utcnow().date()

    start = datetime.combine(
        today,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=9)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        start,
        AppointmentStatus.CONFIRMED,
    )

    _make_trip(
        db_session,
        operations_actor.clinic,
        status=TripStatus.DISPATCHED,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-METRIC-001",
        status=VehicleStatus.AVAILABLE,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-METRIC-002",
        status=VehicleStatus.MAINTENANCE,
    )

    dashboard_helpers.chat = DashboardChatSummarySchema(
        unread_messages=7,
        unread_conversations=3,
        mentions=2,
        priority_messages=0,
        recent_messages=0,
    )

    dashboard_helpers.chat_mock.return_value = (
        dashboard_helpers.chat
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    metrics = {
        item.key: item
        for item in result.metrics
    }

    assert metrics["appointments"].value == 1
    assert metrics["missed_appointments"].value == 0
    assert metrics["active_ambulance_trips"].value == 1
    assert metrics["available_ambulances"].value == 1
    assert metrics["maintenance_ambulances"].value == 1
    assert metrics["unread_chat_messages"].value == 7


def test_pending_ambulance_requests_create_critical_alert(
    db_session,
    operations_actor,
    dashboard_helpers,
):
    _make_trip(
        db_session,
        operations_actor.clinic,
        status=TripStatus.REQUESTED,
    )

    _make_trip(
        db_session,
        operations_actor.clinic,
        status=TripStatus.REQUESTED,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert len(result.alerts) == 1

    alert = result.alerts[0]

    assert alert.key == "pending_ambulance_requests"
    assert alert.severity == "critical"
    assert alert.title == "Pending ambulance requests"
    assert alert.count == 2

    assert (
        alert.description
        == "Ambulance requests waiting for dispatch."
    )


def test_maintenance_vehicles_create_warning_alert(
    db_session,
    operations_actor,
    dashboard_helpers,
):
    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-ALERT-001",
        status=VehicleStatus.MAINTENANCE,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-ALERT-002",
        status=VehicleStatus.MAINTENANCE,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-ALERT-003",
        status=VehicleStatus.AVAILABLE,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert len(result.alerts) == 1

    alert = result.alerts[0]

    assert alert.key == "ambulances_maintenance"
    assert alert.severity == "warning"
    assert alert.title == "Ambulances in maintenance"
    assert alert.count == 2

    assert (
        alert.description
        == (
            "Ambulances currently unavailable "
            "because of maintenance."
        )
    )


def test_priority_chat_messages_create_critical_alert(
    operations_actor,
    dashboard_helpers,
):
    dashboard_helpers.chat = DashboardChatSummarySchema(
        unread_messages=4,
        unread_conversations=2,
        mentions=1,
        priority_messages=3,
        recent_messages=0,
    )

    dashboard_helpers.chat_mock.return_value = (
        dashboard_helpers.chat
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert len(result.alerts) == 1

    alert = result.alerts[0]

    assert alert.key == "priority_chat_messages"
    assert alert.severity == "critical"
    assert alert.title == "Priority chat messages"
    assert alert.count == 3

    assert (
        alert.description
        == (
            "Urgent or STAT messages "
            "in active conversations."
        )
    )


def test_no_alerts_when_dashboard_conditions_are_clear(
    operations_actor,
    dashboard_helpers,
):
    dashboard_helpers.chat = DashboardChatSummarySchema(
        unread_messages=2,
        unread_conversations=1,
        mentions=0,
        priority_messages=0,
        recent_messages=0,
    )

    dashboard_helpers.chat_mock.return_value = (
        dashboard_helpers.chat
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.alerts == []


def test_multiple_alerts_are_returned_in_expected_order(
    db_session,
    operations_actor,
    dashboard_helpers,
):
    _make_trip(
        db_session,
        operations_actor.clinic,
        status=TripStatus.REQUESTED,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-MULTI-001",
        status=VehicleStatus.MAINTENANCE,
    )

    dashboard_helpers.chat = DashboardChatSummarySchema(
        unread_messages=5,
        unread_conversations=2,
        mentions=1,
        priority_messages=4,
        recent_messages=0,
    )

    dashboard_helpers.chat_mock.return_value = (
        dashboard_helpers.chat
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert [
        alert.key
        for alert in result.alerts
    ] == [
        "pending_ambulance_requests",
        "ambulances_maintenance",
        "priority_chat_messages",
    ]


def test_context_contains_operations_role_and_clinic_scope(
    operations_actor,
    dashboard_helpers,
):
    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.context.role is operations_actor.role
    assert result.context.scope == "clinic"
    assert result.context.clinic_id == operations_actor.clinic_id
    assert result.context.generated_at is not None


def test_custom_recent_activity_is_returned_unchanged(
    operations_actor,
    monkeypatch,
):
    activity = [
        DashboardActivitySchema(
            entity_type="Appointment",
            entity_id=10,
            action="created",
            occurred_at=_utcnow(),
        ),
        DashboardActivitySchema(
            entity_type="AmbulanceTrip",
            entity_id=20,
            action="updated",
            occurred_at=_utcnow(),
        ),
    ]

    activity_mock = Mock(
        return_value=activity,
    )

    monkeypatch.setattr(
        operations_dashboard_service,
        "build_chat_summary",
        Mock(
            return_value=DashboardChatSummarySchema(
                unread_messages=0,
                unread_conversations=0,
                mentions=0,
                priority_messages=0,
                recent_messages=0,
            ),
        ),
    )

    monkeypatch.setattr(
        operations_dashboard_service,
        "build_recent_activity",
        activity_mock,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.recent_activity == activity

    activity_mock.assert_called_once_with(
        clinic_id=operations_actor.clinic_id,
    )


def test_combined_dashboard_summary(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
        status=StaffStatus.ACTIVE,
    )

    today = _utcnow().date()

    appointment_start = datetime.combine(
        today,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=10)

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        appointment_start,
        AppointmentStatus.SCHEDULED,
    )

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        appointment_start + timedelta(hours=2),
        AppointmentStatus.CONFIRMED,
    )

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        appointment_start + timedelta(hours=4),
        AppointmentStatus.NO_SHOW,
    )

    _make_trip(
        db_session,
        operations_actor.clinic,
        status=TripStatus.REQUESTED,
    )

    _make_trip(
        db_session,
        operations_actor.clinic,
        status=TripStatus.DISPATCHED,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-COMBINED-001",
        status=VehicleStatus.AVAILABLE,
    )

    _make_vehicle(
        db_session,
        operations_actor.clinic,
        plate_number="OPS-COMBINED-002",
        status=VehicleStatus.MAINTENANCE,
    )

    dashboard_helpers.chat = DashboardChatSummarySchema(
        unread_messages=6,
        unread_conversations=3,
        mentions=2,
        priority_messages=1,
        recent_messages=0,
    )

    dashboard_helpers.chat_mock.return_value = (
        dashboard_helpers.chat
    )

    dashboard_helpers.activity = [
        DashboardActivitySchema(
            entity_type="Appointment",
            entity_id=1,
            action="created",
            occurred_at=_utcnow(),
        ),
    ]

    dashboard_helpers.activity_mock.return_value = (
        dashboard_helpers.activity
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert result.overview.appointments_today == 2
    assert result.overview.missed_appointments_today == 1
    assert result.overview.scheduled_appointments_today == 1
    assert result.overview.confirmed_appointments_today == 1
    assert result.overview.active_ambulance_trips == 2
    assert result.overview.pending_ambulance_requests == 1
    assert result.overview.active_staff == 1

    metrics = {
        item.key: item
        for item in result.metrics
    }

    assert metrics["appointments"].value == 2
    assert metrics["missed_appointments"].value == 1
    assert metrics["active_ambulance_trips"].value == 2
    assert metrics["available_ambulances"].value == 1
    assert metrics["maintenance_ambulances"].value == 1
    assert metrics["unread_chat_messages"].value == 6

    assert [
        alert.key
        for alert in result.alerts
    ] == [
        "pending_ambulance_requests",
        "ambulances_maintenance",
        "priority_chat_messages",
    ]

    assert result.chat.priority_messages == 1

    assert result.recent_activity == (
        dashboard_helpers.activity
    )


def test_service_preserves_zero_values_as_integers(
    operations_actor,
    dashboard_helpers,
):
    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
        )
    )

    assert isinstance(
        result.overview.appointments_today,
        int,
    )

    assert isinstance(
        result.overview.missed_appointments_today,
        int,
    )

    assert isinstance(
        result.overview.scheduled_appointments_today,
        int,
    )

    assert isinstance(
        result.overview.confirmed_appointments_today,
        int,
    )

    assert isinstance(
        result.overview.active_ambulance_trips,
        int,
    )

    assert isinstance(
        result.overview.pending_ambulance_requests,
        int,
    )

    assert isinstance(
        result.overview.active_staff,
        int,
    )


def test_appointments_outside_end_boundary_are_excluded(
    db_session,
    operations_actor,
    make_patient,
    make_staff,
    dashboard_helpers,
):
    patient = make_patient(
        clinic=operations_actor.clinic,
    )

    staff = make_staff(
        clinic=operations_actor.clinic,
        role=Role.RECEPTIONIST,
    )

    target_date = _utcnow().date()

    start_of_next_day = datetime.combine(
        target_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )

    _make_appointment(
        db_session,
        operations_actor.clinic,
        patient,
        staff,
        start_of_next_day,
        AppointmentStatus.CONFIRMED,
    )

    query = DashboardQuerySchema(
        date_from=target_date,
        date_to=target_date,
    )

    result = (
        operations_dashboard_service.get_operations_dashboard(
            actor=operations_actor,
            query=query,
        )
    )

    assert result.overview.appointments_today == 0
    assert result.overview.missed_appointments_today == 0


def test_operations_role_fallback_can_be_disabled(
    make_user,
    clinic,
    monkeypatch,
    dashboard_helpers,
):
    actor = make_user(
        clinic=clinic,
        role=Role.OTHER,
    )

    monkeypatch.setattr(
        operations_dashboard_service,
        "OPERATIONS_ROLES",
        {
            Role.RECEPTIONIST,
            Role.DRIVER,
            Role.AMBULANCE_DISPATCHER,
            Role.AMBULANCE_COORDINATOR,
        },
    )

    with pytest.raises(
        ValidationError,
        match="Operations dashboard is not available",
    ):
        operations_dashboard_service.get_operations_dashboard(
            actor=actor,
        )