from datetime import datetime, time, timedelta, timezone

import pytest

from app.core.enums.appointment_enums import AppointmentType
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.modules.appointment.services.appointment_service import (
    create_appointment, reschedule_appointment,
)
from app.modules.clinic.services.clinic_operating_modes import (
    appointment_within_hours, operating_status, normalize_explicit_operating_mode,
)
from app.modules.clinic.services.clinic_service import create_clinic, update_clinic


UTC = timezone.utc


def instant(hour, minute=0, day=24):
    return datetime(2030, 11, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize('opening,closing', [(time(0), time(0)), (time(20), time(6))])
def test_unrelated_updates_keep_extended_schedule(app, opening, closing, make_clinic):
    app.config['CLINIC_EXTENDED_OPERATING_HOURS_ENABLED'] = True
    mode = 'always_open' if opening == closing else 'scheduled'
    fields = {} if mode == 'always_open' else dict(opening_time=opening, closing_time=closing)
    owner = make_clinic().organization_id
    clinic = create_clinic(name='Compatibility clinic', organization_id=owner, operating_mode=mode, **fields)
    app.config['CLINIC_EXTENDED_OPERATING_HOURS_ENABLED'] = False
    updated = update_clinic(clinic.id, phone='08012345678', address='Updated address')
    assert updated.phone == '08012345678'
    assert updated.address == 'Updated address'
    assert (updated.opening_time, updated.closing_time) == (opening, closing)
    with pytest.raises(ValidationError):
        update_clinic(clinic.id, opening_time=closing)


@pytest.mark.parametrize('opening,closing,start,end,expected', [
    (time(8), time(17), instant(7), instant(16), True),
    (time(8), time(17), instant(6,59), instant(7,30), False),
    (time(8), time(17), instant(15,30), instant(16,1), False),
    (time(20), time(6), instant(21), instant(4,30,25), True),
    (time(20), time(6), instant(3), instant(5), True),
    (time(20), time(6), instant(4), instant(5,1), False),
    (time(0), time(0), instant(1), instant(1,0,25), True),
    (None, None, instant(1), instant(2), None),
    (time(8), None, instant(1), instant(2), None),
])
def test_clinic_local_window(opening, closing, start, end, expected):
    assert appointment_within_hours(opening, closing, 'Africa/Lagos', start, end) is expected


def test_naive_utc_and_explicit_offset_equivalence():
    assert appointment_within_hours(time(8), time(17), 'Africa/Lagos',
        instant(7).replace(tzinfo=None), instant(8).astimezone(timezone(timedelta(hours=2)))) is True


def test_ambiguous_dst_boundaries_use_conservative_interval():
    # New York fall-back: opening at repeated 01:30 -> later 06:30 UTC.
    day = datetime(2026, 11, 1, tzinfo=UTC)
    assert appointment_within_hours(time(1,30), time(3), 'America/New_York',
        day.replace(hour=5, minute=45), day.replace(hour=6, minute=15)) is False
    assert appointment_within_hours(time(1,30), time(3), 'America/New_York',
        day.replace(hour=6, minute=30), day.replace(hour=7)) is True


def test_nonexistent_dst_opening_does_not_invent_window():
    day = datetime(2026, 3, 8, tzinfo=UTC)
    assert appointment_within_hours(time(2,30), time(5), 'America/New_York',
        day.replace(hour=8), day.replace(hour=9)) is False


def test_status_is_half_open_and_unknown_not_24h():
    assert operating_status(time(8), time(17), 'Africa/Lagos', now=instant(7))['is_open_now'] is True
    closed = operating_status(time(8), time(17), 'Africa/Lagos', now=instant(16))
    assert closed['is_open_now'] is False
    assert closed['next_opening_at'] == instant(7, day=25).isoformat()
    assert operating_status(None, None, 'UTC', now=instant(7))['is_open_now'] is None
    assert operating_status(time(8), time(17), 'Invalid/Zone', now=instant(7))['is_open_now'] is None
    with pytest.raises(ValueError):
        normalize_explicit_operating_mode(time(8, tzinfo=UTC), time(17), 'scheduled')


def test_real_create_reschedule_and_emergency_paths(app, make_clinic, make_patient, make_staff):
    clinic = make_clinic(timezone='Africa/Lagos', opening_time=time(8), closing_time=time(17))
    patient = make_patient(clinic)
    staff = make_staff(clinic, role=Role.DOCTOR)
    app.config.update(CLINIC_EXTENDED_OPERATING_HOURS_ENABLED=True,
                      CLINIC_BOOKING_HOURS_ENFORCEMENT_ENABLED=True)
    def create(start, end, kind=AppointmentType.IN_PERSON):
        return create_appointment(clinic.id, patient.id, staff.id, start.replace(tzinfo=None),
                                  end.replace(tzinfo=None), appointment_type=kind)
    booked = create(instant(8), instant(9))
    assert booked.id is not None
    with pytest.raises(ValidationError, match='outside clinic operating hours'):
        create(instant(17), instant(18))
    with pytest.raises(ValidationError, match='outside clinic operating hours'):
        reschedule_appointment(booked.id, instant(17).replace(tzinfo=None),
                               instant(18).replace(tzinfo=None), clinic_id=clinic.id)
    assert reschedule_appointment(booked.id, instant(9).replace(tzinfo=None),
                                  instant(10).replace(tzinfo=None), clinic_id=clinic.id).id == booked.id
    emergency = create(instant(17), instant(18), AppointmentType.EMERGENCY)
    assert emergency.appointment_type == AppointmentType.EMERGENCY
    app.config['CLINIC_BOOKING_HOURS_ENFORCEMENT_ENABLED'] = False
    assert create(instant(19), instant(20)).id is not None


def test_private_profile_metadata_is_staged(app, client, make_clinic, make_user, auth_headers_for):
    clinic = make_clinic(timezone='UTC', opening_time=time(0), closing_time=time(0))
    user = make_user(clinic, role=Role.PATIENT)
    headers = auth_headers_for(user)
    app.config['CLINIC_EXTENDED_OPERATING_HOURS_ENABLED'] = False
    old = client.get('/api/v1/profile/me', headers=headers)
    assert old.status_code == 200, old.get_data(as_text=True)
    assert 'operating_mode' not in old.get_json()['data']['clinic']
    app.config['CLINIC_EXTENDED_OPERATING_HOURS_ENABLED'] = True
    new = client.get('/api/v1/profile/me', headers=headers)
    assert new.status_code == 200, new.get_data(as_text=True)
    assert new.get_json()['data']['clinic']['operating_mode'] == 'always_open'
    assert new.get_json()['data']['clinic']['is_open_now'] is True

def test_creation_audit_records_actual_hours_for_root_and_branch(app, make_clinic):
    from app.core.audit.models.audit_model import AuditLog
    from app.core.enums.audit_enums import AuditAction
    from app.modules.clinic.services.clinic_service import create_branch
    app.config['CLINIC_EXTENDED_OPERATING_HOURS_ENABLED'] = True
    root = create_clinic(name='Hours audit root', organization_id=make_clinic().organization_id, timezone='Africa/Lagos',
                         operating_mode='always_open')
    branch = create_branch(root.id, name='Hours audit branch', timezone='Africa/Lagos',
                           operating_mode='scheduled', opening_time=time(20), closing_time=time(6))
    for entity, opening, closing in [(root, '00:00:00', '00:00:00'),
                                      (branch, '20:00:00', '06:00:00')]:
        entry = AuditLog.query.filter_by(entity_type='Clinic', entity_id=entity.id,
                                         action=AuditAction.CREATE).one()
        assert entry.new_value['opening_time'] == opening
        assert entry.new_value['closing_time'] == closing
        assert entry.new_value['timezone'] == 'Africa/Lagos'


def test_staged_offset_storage_and_existing_conflict_authority(app, make_clinic, make_patient, make_staff, db):
    from app.core.exceptions import ConflictError
    clinic = make_clinic(timezone='Africa/Lagos', opening_time=time(8), closing_time=time(17))
    patient = make_patient(clinic)
    staff = make_staff(clinic, role=Role.DOCTOR)
    app.config.update(CLINIC_EXTENDED_OPERATING_HOURS_ENABLED=True,
                      CLINIC_BOOKING_HOURS_ENFORCEMENT_ENABLED=True)
    zone = timezone(timedelta(hours=2))
    booked = create_appointment(clinic.id, patient.id, staff.id,
                                instant(8).astimezone(zone), instant(9).astimezone(zone))
    db.session.refresh(booked)
    assert booked.scheduled_start == instant(8).replace(tzinfo=None)
    assert booked.scheduled_end == instant(9).replace(tzinfo=None)
    with pytest.raises(ConflictError):
        create_appointment(clinic.id, patient.id, staff.id, instant(8), instant(9))
    updated = reschedule_appointment(booked.id, instant(10).astimezone(zone),
                                     instant(11).astimezone(zone), clinic_id=clinic.id)
    db.session.refresh(updated)
    assert updated.scheduled_start == instant(10).replace(tzinfo=None)
    assert updated.scheduled_end == instant(11).replace(tzinfo=None)


def test_existing_bookings_report_hours_conflict_without_mutation(app, client, make_clinic,
        make_patient, make_authenticated_staff, make_appointment, db):
    from app.core.enums.appointment_enums import AppointmentStatus
    clinic = make_clinic(timezone='Africa/Lagos', opening_time=time(8), closing_time=time(17))
    patient = make_patient(clinic)
    staff, headers = make_authenticated_staff(clinic, Role.DOCTOR)
    def item(start, end, kind=AppointmentType.IN_PERSON, status=AppointmentStatus.SCHEDULED):
        return make_appointment(clinic, patient, staff, appointment_type=kind, status=status,
                                scheduled_start=instant(start).replace(tzinfo=None),
                                scheduled_end=instant(end).replace(tzinfo=None))
    inside = item(8, 9)
    outside = item(17, 18)
    emergency = item(18, 19, AppointmentType.EMERGENCY)
    completed = item(19, 20, status=AppointmentStatus.COMPLETED)
    ids = [x.id for x in (inside, outside, emergency, completed)]
    db.session.commit()
    url = f'/api/v1/appointments/patient/{patient.id}'
    app.config.update(CLINIC_EXTENDED_OPERATING_HOURS_ENABLED=False,
                      CLINIC_BOOKING_HOURS_ENFORCEMENT_ENABLED=False)
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert all('clinic_hours_conflict' not in row for row in response.get_json()['data']['items'])
    app.config['CLINIC_EXTENDED_OPERATING_HOURS_ENABLED'] = True
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    rows = {row['id']: row for row in response.get_json()['data']['items']}
    assert set(rows) == set(ids)
    assert [rows[i]['clinic_hours_conflict'] for i in ids] == [False, True, None, None]
    db.session.refresh(outside)
    assert outside.status == AppointmentStatus.SCHEDULED
    assert outside.scheduled_start == instant(17).replace(tzinfo=None)


def test_staged_idempotent_replay_does_not_rebook_after_hours_change(app, make_clinic,
        make_patient, make_authenticated_staff, db):
    clinic = make_clinic(timezone='UTC', opening_time=time(8), closing_time=time(17))
    patient = make_patient(clinic)
    staff, _ = make_authenticated_staff(clinic, Role.DOCTOR)
    app.config.update(CLINIC_EXTENDED_OPERATING_HOURS_ENABLED=True,
                      CLINIC_BOOKING_HOURS_ENFORCEMENT_ENABLED=True)
    offset = timezone(timedelta(hours=2))
    kwargs = dict(clinic_id=clinic.id, patient_id=patient.id, staff_id=staff.id,
                  scheduled_start=instant(8).astimezone(offset),
                  scheduled_end=instant(9).astimezone(offset),
                  idempotency_key='clinic-hours-02b-replay', idempotency_user_id=staff.user_id)
    first = create_appointment(**kwargs)
    first_id = first.id
    update_clinic(clinic.id, opening_time=time(10), closing_time=time(17))
    replay = create_appointment(**kwargs)
    assert replay.id == first_id
    from app.modules.appointment.models.appointment_model import Appointment
    assert Appointment.query.filter_by(clinic_id=clinic.id).count() == 1
