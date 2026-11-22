"""S12 patient self-service: authenticated ownership, deny-first, bounded booking."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.enums.role_enums import Role
from app.core.enums.appointment_enums import AppointmentStatus, AppointmentType
from app.modules.appointment.models.appointment_model import Appointment
from app.modules.patient.routes import patient_portal_route as portal


@pytest.fixture
def enrolled(clinic, make_user, make_patient, auth_headers_for):
    user = make_user(clinic, role=Role.PATIENT)
    patient = make_patient(clinic, user_id=user.id)
    return user, patient, auth_headers_for(user)


def _booking(staff_id, *, start=None):
    start = start or datetime.now(timezone.utc) + timedelta(days=4)
    return {
        'staff_id': staff_id,
        'scheduled_start': start.isoformat(),
        'scheduled_end': (start + timedelta(minutes=30)).isoformat(),
        'appointment_type': 'in_person',
    }


def test_patient_profile_self_only(client, enrolled):
    _, patient, headers = enrolled
    r = client.get('/api/v1/patient-portal/me', headers=headers)
    assert r.status_code == 200
    assert r.json['data']['first_name'] == patient.first_name
    assert 'patient_id' not in r.json['data']
    assert 'clinic_id' not in r.json['data']


def test_staff_cannot_use_patient_portal(client, clinic, make_user, auth_headers_for):
    user = make_user(clinic, role=Role.DOCTOR)
    r = client.get('/api/v1/patient-portal/me', headers=auth_headers_for(user))
    assert r.status_code == 403


def test_unlinked_patient_denied(client, clinic, make_user, auth_headers_for):
    user = make_user(clinic, role=Role.PATIENT)
    r = client.get('/api/v1/patient-portal/me', headers=auth_headers_for(user))
    assert r.status_code == 409
    assert r.json['code'] == 'PATIENT_ENROLLMENT_REQUIRED'


def test_missing_auth_denied(client):
    assert client.get('/api/v1/patient-portal/appointments').status_code == 401


def test_booking_cannot_select_other_patient(client, enrolled, make_staff):
    user, patient, headers = enrolled
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    body = _booking(doctor.id)
    body['patient_id'] = patient.id + 1
    r = client.post('/api/v1/patient-portal/appointments', headers={**headers, 'Idempotency-Key': 's12-unique-booking-key-01'}, json=body)
    assert r.status_code == 422


def test_booking_requires_utc_offset_and_key(client, enrolled, make_staff):
    _, patient, headers = enrolled
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    body = _booking(doctor.id)
    body['scheduled_start'] = '2027-01-01T10:00:00'
    r = client.post('/api/v1/patient-portal/appointments', headers={**headers, 'Idempotency-Key': 's12-unique-booking-key-02'}, json=body)
    assert r.status_code == 422
    r = client.post('/api/v1/patient-portal/appointments', headers=headers, json=_booking(doctor.id))
    assert r.status_code == 422


def test_booking_staff_must_be_active_doctor(client, enrolled, make_staff):
    _, patient, headers = enrolled
    nurse = make_staff(patient.clinic, role=Role.NURSE)
    r = client.post('/api/v1/patient-portal/appointments', headers={**headers, 'Idempotency-Key': 's12-unique-booking-key-03'}, json=_booking(nurse.id))
    assert r.status_code == 404


def test_booking_source_ids_and_idempotency_are_server_owned(client, enrolled, make_staff, monkeypatch):
    user, patient, headers = enrolled
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    seen = {}

    def fake_create_appointment(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            id=700, status=AppointmentStatus.SCHEDULED, appointment_type=AppointmentType.IN_PERSON,
            staff_id=doctor.id, scheduled_start=kwargs['scheduled_start'],
            scheduled_end=kwargs['scheduled_end'], reason=None,
        )

    monkeypatch.setattr(portal, 'create_appointment', fake_create_appointment)
    r = client.post('/api/v1/patient-portal/appointments', headers={**headers, 'Idempotency-Key': 's12-unique-booking-key-04'}, json=_booking(doctor.id))
    assert r.status_code == 201
    assert seen['patient_id'] == patient.id
    assert seen['clinic_id'] == patient.clinic_id
    assert seen['idempotency_user_id'] == user.id
    assert seen['idempotency_key'] == 's12-unique-booking-key-04'
    assert seen['scheduled_start'].tzinfo is None


def test_patient_appointment_read_does_not_return_staff_notes(client, enrolled, make_staff, db_session):
    _, patient, headers = enrolled
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    start = datetime.utcnow() + timedelta(days=3)
    a = Appointment(clinic_id=patient.clinic_id, patient_id=patient.id, staff_id=doctor.id,
                    scheduled_start=start, scheduled_end=start + timedelta(minutes=30),
                    appointment_type=AppointmentType.IN_PERSON, status=AppointmentStatus.SCHEDULED,
                    notes='Private staff clinical note', reason='Review')
    db_session.add(a)
    db_session.flush()
    r = client.get('/api/v1/patient-portal/appointments', headers=headers)
    assert r.status_code == 200
    assert len(r.json['data']['items']) == 1
    assert 'notes' not in r.json['data']['items'][0]
    assert 'Private staff clinical note' not in r.get_data(as_text=True)


def test_foreign_patient_appointment_cannot_be_cancelled(client, enrolled, make_user, make_patient, make_staff, db_session):
    _, patient, headers = enrolled
    other = make_patient(patient.clinic)
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    start = datetime.utcnow() + timedelta(days=3)
    a = Appointment(clinic_id=patient.clinic_id, patient_id=other.id, staff_id=doctor.id,
                    scheduled_start=start, scheduled_end=start + timedelta(minutes=30),
                    appointment_type=AppointmentType.IN_PERSON, status=AppointmentStatus.SCHEDULED)
    db_session.add(a)
    db_session.flush()
    r = client.post(f'/api/v1/patient-portal/appointments/{a.id}/cancel', headers=headers,
                    json={'cancellation_reason': 'Unable to attend'})
    assert r.status_code == 404


def test_read_my_invoices_prescriptions_only(client, enrolled):
    _, patient, headers = enrolled
    assert client.get('/api/v1/patient-portal/invoices', headers=headers).status_code == 200
    assert client.get('/api/v1/patient-portal/prescriptions', headers=headers).status_code == 200
    assert client.get('/api/v1/patient-portal/clinicians', headers=headers).status_code == 200


def test_unknown_query_rejected(client, enrolled):
    _, _, headers = enrolled
    r = client.get('/api/v1/patient-portal/appointments?patient_id=5', headers=headers)
    assert r.status_code == 422


def test_invoices_never_include_other_patient(client, enrolled, make_patient, db_session):
    from decimal import Decimal
    from app.modules.billing.models.billing_model import Invoice
    from app.core.enums.billing_enums import InvoiceStatus
    _, patient, headers = enrolled
    other = make_patient(patient.clinic)
    invoice = Invoice(clinic_id=patient.clinic_id, patient_id=other.id,
                      invoice_number='S12-FOREIGN-ONLY', currency='NGN',
                      total_amount=Decimal('50.00'), amount_paid=Decimal('0.00'),
                      status=InvoiceStatus.ISSUED)
    db_session.add(invoice)
    db_session.flush()
    r = client.get('/api/v1/patient-portal/invoices', headers=headers)
    assert r.status_code == 200
    assert r.json['data']['items'] == []
    assert 'S12-FOREIGN-ONLY' not in r.get_data(as_text=True)


def test_prescriptions_never_include_other_patient(client, enrolled, make_patient, make_staff, db_session):
    from app.modules.prescription.models.prescription_model import Prescription
    _, patient, headers = enrolled
    other = make_patient(patient.clinic)
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    row = Prescription(clinic_id=patient.clinic_id, patient_id=other.id,
                       prescribed_by_id=doctor.id)
    db_session.add(row)
    db_session.flush()
    r = client.get('/api/v1/patient-portal/prescriptions', headers=headers)
    assert r.status_code == 200
    assert r.json['data']['items'] == []


def test_confirmed_appointment_not_cancellable_by_patient(client, enrolled, make_staff, db_session):
    _, patient, headers = enrolled
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    start = datetime.utcnow() + timedelta(days=4)
    row = Appointment(clinic_id=patient.clinic_id, patient_id=patient.id,
        staff_id=doctor.id, scheduled_start=start,
        scheduled_end=start + timedelta(minutes=30),
        status=AppointmentStatus.CONFIRMED,
        appointment_type=AppointmentType.IN_PERSON)
    db_session.add(row)
    db_session.flush()
    r = client.post(f'/api/v1/patient-portal/appointments/{row.id}/cancel',
                    headers=headers, json={'cancellation_reason': 'Change of plans'})
    assert r.status_code == 409
    assert row.status is AppointmentStatus.CONFIRMED


def test_booking_cannot_request_emergency_type(client, enrolled, make_staff):
    _, patient, headers = enrolled
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    body = _booking(doctor.id)
    body['appointment_type'] = 'emergency'
    r = client.post('/api/v1/patient-portal/appointments',
                    headers={**headers, 'Idempotency-Key': 's12-unique-booking-key-05'},
                    json=body)
    assert r.status_code == 422
