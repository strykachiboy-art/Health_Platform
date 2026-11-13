"""CE1-S02 SQLite-focused scheduling safety tests (not a PG concurrency substitute)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.surgery.models.theatre_model import OperatingTheatre, TheatreBooking
from app.modules.surgery.services import theatre_booking_service as booking_service
from app.tests.modules.surgery.test_surgical_case_foundation import _context, _order, _plan


def _times(hours=2, duration=2):
    start = datetime(2030, 1, 10, 8, 0, tzinfo=timezone.utc) + timedelta(hours=hours)
    start = start.replace(microsecond=0)
    return start, start + timedelta(hours=duration)


@pytest.fixture()
def context(monkeypatch, clinic, make_patient, make_staff):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    monkeypatch.setattr(booking_service, "create_audit_log", lambda **kw: ctx["events"].append(kw))
    ctx["admin"] = make_staff(clinic, role=Role.ADMIN)
    ctx["clinic"] = clinic
    return ctx


def _room(ctx, code="OR-1", **kwargs):
    return booking_service.create_operating_theatre(
        clinic_id=ctx["clinic"].id, actor_staff_id=ctx["admin"].id,
        code=code, display_name="Operating Room " + code, **kwargs
    )


def _case(ctx):
    order = _order(ctx["clinic"], ctx)
    case = _plan(ctx["clinic"], ctx, order)
    return order, case


def _schedule(ctx, case, theatre, *, hours=2, duration=2, **kwargs):
    start, end = _times(hours=hours, duration=duration)
    values = dict(clinic_id=ctx["clinic"].id,
                  actor_staff_id=ctx["doctor"].id,
                  surgical_case_id=case.id, theatre_id=theatre.id,
                  starts_at=start, ends_at=end)
    values.update(kwargs)
    return booking_service.schedule_theatre(**values)


def test_room_registration_requires_admin_and_is_clinic_scoped(context):
    theatre = _room(context, "or-a")
    assert theatre.code == "OR-A"
    assert theatre.clinic_id == context["clinic"].id
    assert theatre.is_active is True
    assert any(e["entity_type"] == "OperatingTheatre" for e in context["events"])
    with pytest.raises(ValidationError, match="administrator"):
        booking_service.create_operating_theatre(
            clinic_id=context["clinic"].id, actor_staff_id=context["doctor"].id,
            code="OR-B", display_name="Other"
        )


def test_duplicate_room_code_is_blocked(context):
    _room(context)
    with pytest.raises(ConflictError, match="code already"):
        _room(context, "or-1")


def test_booking_reserves_room_without_changing_case_execution_status(context):
    theatre = _room(context)
    order, case = _case(context)
    booking = _schedule(context, case, theatre)
    assert booking.id is not None
    assert booking.status == "scheduled"
    assert booking.version == 1
    assert booking.surgical_case_id == case.id
    assert case.status == "planned"
    assert order.status == "active"
    assert booking_service.get_theatre_booking(
        booking_id=booking.id, clinic_id=context["clinic"].id
    ).id == booking.id
    assert any(e["entity_type"] == "TheatreBooking" for e in context["events"])


def test_overlapping_time_same_room_blocked(context):
    theatre = _room(context)
    _, first = _case(context)
    _, second = _case(context)
    _schedule(context, first, theatre)
    with pytest.raises(ConflictError, match="Overlapping"):
        _schedule(context, second, theatre, hours=3)
    assert db.session.query(TheatreBooking).count() == 1


def test_exact_time_duplicate_same_room_blocked(context):
    theatre = _room(context)
    _, first = _case(context)
    _, second = _case(context)
    _schedule(context, first, theatre)
    with pytest.raises(ConflictError, match="Overlapping"):
        _schedule(context, second, theatre)


def test_adjacent_time_same_room_allowed(context):
    theatre = _room(context)
    _, first = _case(context)
    _, second = _case(context)
    a = _schedule(context, first, theatre)
    b = _schedule(context, second, theatre, hours=4)
    assert a.ends_at == b.starts_at
    assert db.session.query(TheatreBooking).count() == 2


def test_same_time_in_different_theatres_allowed(context):
    a = _room(context, "OR-1")
    b = _room(context, "OR-2")
    _, first = _case(context)
    _, second = _case(context)
    _schedule(context, first, a)
    _schedule(context, second, b)
    assert db.session.query(TheatreBooking).count() == 2


def test_duplicate_active_case_cannot_move_to_second_room(context):
    a = _room(context, "OR-1")
    b = _room(context, "OR-2")
    _, case = _case(context)
    _schedule(context, case, a)
    with pytest.raises(ConflictError, match="already has"):
        _schedule(context, case, b)


def test_cancel_frees_room_but_keeps_historical_row(context):
    theatre = _room(context)
    _, case = _case(context)
    _, other = _case(context)
    booking = _schedule(context, case, theatre)
    booking_service.cancel_theatre_booking(
        booking_id=booking.id, clinic_id=context["clinic"].id,
        actor_staff_id=context["doctor"].id, expected_version=1,
        reason="Patient requested postponement",
    )
    assert booking.status == "cancelled"
    assert booking.version == 2
    assert booking.cancelled_at is not None
    replacement = _schedule(context, other, theatre)
    assert replacement.status == "scheduled"
    assert db.session.query(TheatreBooking).count() == 2


def test_cancelled_case_can_be_rebooked_as_new_historical_row(context):
    theatre = _room(context)
    _, case = _case(context)
    original = _schedule(context, case, theatre)
    booking_service.cancel_theatre_booking(
        booking_id=original.id, clinic_id=context["clinic"].id,
        actor_staff_id=context["doctor"].id, expected_version=1,
        reason="Reschedule after postponement",
    )
    renewed = _schedule(context, case, theatre, hours=5)
    assert renewed.id != original.id
    assert db.session.query(TheatreBooking).count() == 2


def test_same_room_reschedule_keeps_version_and_checks_overlaps(context):
    theatre = _room(context)
    _, case = _case(context)
    booking = _schedule(context, case, theatre)
    start, end = _times(hours=7)
    booking_service.reschedule_theatre(
        booking_id=booking.id, clinic_id=context["clinic"].id,
        actor_staff_id=context["doctor"].id,
        starts_at=start, ends_at=end, expected_version=1,
    )
    assert booking.version == 2
    assert booking.starts_at == start.replace(tzinfo=None)
    with pytest.raises(ConflictError, match="version changed"):
        booking_service.reschedule_theatre(
            booking_id=booking.id, clinic_id=context["clinic"].id,
            actor_staff_id=context["doctor"].id,
            starts_at=start, ends_at=end, expected_version=1,
        )


def test_conflicting_reschedule_preserves_previous_interval(context):
    theatre = _room(context)
    _, case1 = _case(context)
    _, case2 = _case(context)
    _schedule(context, case1, theatre, hours=3)
    second = _schedule(context, case2, theatre, hours=8)
    before = (second.starts_at, second.ends_at, second.version)
    start, end = _times(hours=4)
    with pytest.raises(ConflictError, match="Overlapping"):
        booking_service.reschedule_theatre(
            booking_id=second.id, clinic_id=context["clinic"].id,
            actor_staff_id=context["doctor"].id,
            starts_at=start, ends_at=end, expected_version=1,
        )
    db.session.refresh(second)
    assert (second.starts_at, second.ends_at, second.version) == before


def test_inactive_theatre_rejected(context):
    theatre = _room(context)
    _, case = _case(context)
    theatre.is_active = False
    db.session.commit()
    with pytest.raises(ConflictError, match="Inactive theatre"):
        _schedule(context, case, theatre)


def test_nurse_cannot_schedule_or_cancel(context):
    theatre = _room(context)
    _, case = _case(context)
    with pytest.raises(ValidationError, match="Active doctor"):
        _schedule(context, case, theatre, actor_staff_id=context["nurse"].id)
    booking = _schedule(context, case, theatre)
    with pytest.raises(ValidationError, match="Active doctor"):
        booking_service.cancel_theatre_booking(
            booking_id=booking.id, clinic_id=context["clinic"].id,
            actor_staff_id=context["nurse"].id, expected_version=1,
            reason="Not allowed",
        )


def test_other_clinic_cannot_view_theatre_booking(context, make_clinic):
    theatre = _room(context)
    _, case = _case(context)
    booking = _schedule(context, case, theatre)
    other = make_clinic(name="Other Surgery Hospital")
    with pytest.raises(NotFoundError):
        booking_service.get_theatre_booking(booking_id=booking.id, clinic_id=other.id)


def test_booking_does_not_accept_foreign_theatre(context, make_clinic, make_staff, monkeypatch):
    theatre = _room(context)
    _, case = _case(context)
    other = make_clinic(name="Other Theatre Clinic")
    admin = make_staff(other, role=Role.ADMIN)
    foreign = booking_service.create_operating_theatre(
        clinic_id=other.id, actor_staff_id=admin.id,
        code="OTHER", display_name="Foreign theatre",
    )
    with pytest.raises(NotFoundError):
        _schedule(context, case, foreign)
    assert theatre.clinic_id == context["clinic"].id


def test_non_timezone_and_invalid_interval_rejected(context):
    theatre = _room(context)
    _, case = _case(context)
    start, end = _times()
    with pytest.raises(ValidationError, match="timezone"):
        _schedule(context, case, theatre,
                  starts_at=start.replace(tzinfo=None), ends_at=end)
    with pytest.raises(ValidationError, match="before ends_at"):
        _schedule(context, case, theatre, starts_at=end, ends_at=start)
    assert db.session.query(TheatreBooking).count() == 0


def test_terminal_order_blocks_booking(context):
    theatre = _room(context)
    order, case = _case(context)
    from app.modules.clinical_orders.services import procedure_order_service
    # The normal CPOE cancellation API now protects linked surgical cases.
    with pytest.raises(ConflictError, match="dedicated Surgery workflow"):
        procedure_order_service.cancel_procedure_order(
            order.id, clinic_id=context["clinic"].id,
            actor_staff_id=context["doctor"].id, reason="New plan",
        )
    assert order.status == "active"
    # Test-only legacy terminal state with valid DB cancellation evidence:
    # theatre scheduling must independently reject the terminal source.
    # Resolve the staff identifier before any incomplete terminal-state write.
    cancelling_staff_id = context["doctor"].id
    order.status = "cancelled"
    order.cancelled_at = datetime.now(timezone.utc)
    order.cancelled_by_staff_id = cancelling_staff_id
    order.cancellation_reason = "Synthetic legacy state in isolated test DB"
    db.session.flush()
    with pytest.raises(ConflictError, match="Active consent-required"):
        _schedule(context, case, theatre)


def test_discharged_admission_blocks_booking(context):
    theatre = _room(context)
    _, case = _case(context)
    context["admission"].status = AdmissionStatus.DISCHARGED
    db.session.commit()
    with pytest.raises(ConflictError, match="Active admission"):
        _schedule(context, case, theatre)


def test_cancelled_case_blocks_booking(context):
    theatre = _room(context)
    _, case = _case(context)
    case.status = "cancelled"
    db.session.commit()
    with pytest.raises(ConflictError, match="Only planned"):
        _schedule(context, case, theatre)


def test_cancel_requires_reason_and_fresh_version(context):
    theatre = _room(context)
    _, case = _case(context)
    booking = _schedule(context, case, theatre)
    with pytest.raises(ValidationError, match="not be blank"):
        booking_service.cancel_theatre_booking(
            booking_id=booking.id, clinic_id=context["clinic"].id,
            actor_staff_id=context["doctor"].id, expected_version=1, reason="   ",
        )
    with pytest.raises(ConflictError, match="version changed"):
        booking_service.cancel_theatre_booking(
            booking_id=booking.id, clinic_id=context["clinic"].id,
            actor_staff_id=context["doctor"].id, expected_version=2, reason="changed",
        )
    assert booking.status == "scheduled"


def test_database_interval_constraint_is_not_only_service_policy(context):
    theatre = _room(context)
    _, case = _case(context)
    start, end = _times()
    booking = TheatreBooking(
        clinic_id=context["clinic"].id, surgical_case_id=case.id,
        theatre_id=theatre.id, starts_at=end.replace(tzinfo=None),
        ends_at=start.replace(tzinfo=None), status="scheduled",
        scheduled_by_staff_id=context["doctor"].id, version=1,
    )
    db.session.add(booking)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_database_active_case_unique_index(context):
    theatre = _room(context)
    _, case = _case(context)
    _schedule(context, case, theatre)
    start, end = _times(hours=9)
    duplicate = TheatreBooking(
        clinic_id=context["clinic"].id, surgical_case_id=case.id,
        theatre_id=theatre.id, starts_at=start.replace(tzinfo=None),
        ends_at=end.replace(tzinfo=None), status="scheduled",
        scheduled_by_staff_id=context["doctor"].id, version=1,
    )
    db.session.add(duplicate)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_no_public_surgery_routes_in_s02(app):
    # S11 permits exactly two authorized GET-only documentation endpoints.
    # No Surgery mutation or operative-execution routes may be registered.
    surgery_routes = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if "/surgery" in rule.rule
    }
    assert surgery_routes == {
        ("/api/v1/dashboard/surgery", frozenset({"GET"})),
        ("/api/v1/dashboard/surgery/cases/<int:surgical_case_id>", frozenset({"GET"})),
    }
