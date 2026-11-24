"""S13 synthetic, Patient-owned detail/payment/chat authorization tests."""
from decimal import Decimal
from types import SimpleNamespace
import pytest

from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.enums.billing_enums import InvoiceStatus
from app.modules.billing.models.billing_model import Invoice, InvoiceItem
from app.modules.prescription.models.prescription_model import Prescription, PrescriptionItem
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.patient.routes import patient_portal_services_route as api
from app.modules.billing.services import payment_orchestration_service as pay


@pytest.fixture
def actor(clinic, make_user, make_patient, auth_headers_for):
    user = make_user(clinic, role=Role.PATIENT)
    patient = make_patient(clinic, user_id=user.id)
    return user, patient, auth_headers_for(user)


def invoice(patient, suffix):
    row = Invoice(clinic_id=patient.clinic_id, patient_id=patient.id,
                  invoice_number=f'S13-TEST-{suffix}', currency='NGN',
                  total_amount=Decimal('300.00'), amount_paid=Decimal('50.00'),
                  status=InvoiceStatus.PARTIALLY_PAID)
    db.session.add(row)
    db.session.flush()
    return row


def test_detail_login_required(client):
    assert client.get('/api/v1/patient-portal/invoices/1').status_code == 401
    assert client.get('/api/v1/patient-portal/prescriptions/1').status_code == 401
    assert client.get('/api/v1/patient-portal/conversations').status_code == 401


def test_staff_denied(client, clinic, make_user, auth_headers_for):
    headers = auth_headers_for(make_user(clinic, role=Role.DOCTOR))
    assert client.get('/api/v1/patient-portal/invoices/1', headers=headers).status_code == 403
    assert client.get('/api/v1/patient-portal/conversations', headers=headers).status_code == 403


def test_unenrolled_patient_denied(client, clinic, make_user, auth_headers_for):
    headers = auth_headers_for(make_user(clinic, role=Role.PATIENT))
    r = client.get('/api/v1/patient-portal/invoices/1', headers=headers)
    assert r.status_code == 409
    assert r.json['code'] == 'PATIENT_ENROLLMENT_REQUIRED'


def test_invoice_own_details_and_items(client, actor):
    _, patient, headers = actor
    bill = invoice(patient, 'OWN-1')
    db.session.add(InvoiceItem(invoice_id=bill.id, description='Consultation',
                               quantity=1, unit_price=Decimal('300.00'),
                               subtotal=Decimal('300.00')))
    db.session.flush()
    r = client.get(f'/api/v1/patient-portal/invoices/{bill.id}', headers=headers)
    assert r.status_code == 200
    detail = r.json['data']
    assert detail['items'][0]['description'] == 'Consultation'
    assert detail['outstanding_balance'] == '250.00'
    assert detail['sandbox_checkout_available'] is False
    assert 'patient_id' not in detail
    assert 'clinic_id' not in detail


def test_foreign_invoice_never_disclosed(client, actor, make_patient):
    _, patient, headers = actor
    foreign = make_patient(patient.clinic)
    bill = invoice(foreign, 'FOREIGN-2')
    r = client.get(f'/api/v1/patient-portal/invoices/{bill.id}', headers=headers)
    assert r.status_code == 404
    assert 'Consultation' not in str(r.json)


def test_prescription_own_medicine_details(client, actor, make_staff):
    _, patient, headers = actor
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    drug = Drug(clinic_id=patient.clinic_id, name='Synthetic tablet')
    db.session.add(drug)
    db.session.flush()
    prescription = Prescription(clinic_id=patient.clinic_id,
        patient_id=patient.id, prescribed_by_id=doctor.id)
    db.session.add(prescription)
    db.session.flush()
    db.session.add(PrescriptionItem(prescription_id=prescription.id,
        drug_id=drug.id, dosage='1 tablet', frequency='once daily',
        instructions='After food'))
    db.session.flush()
    r = client.get(f'/api/v1/patient-portal/prescriptions/{prescription.id}', headers=headers)
    assert r.status_code == 200
    assert r.json['data']['items'][0]['medicine'] == 'Synthetic tablet'
    assert r.json['data']['items'][0]['instructions'] == 'After food'
    assert 'patient_id' not in r.json['data']


def test_foreign_prescription_blocked(client, actor, make_patient, make_staff):
    _, patient, headers = actor
    other = make_patient(patient.clinic)
    doctor = make_staff(patient.clinic, role=Role.DOCTOR)
    p = Prescription(clinic_id=patient.clinic_id, patient_id=other.id,
                     prescribed_by_id=doctor.id)
    db.session.add(p)
    db.session.flush()
    assert client.get(f'/api/v1/patient-portal/prescriptions/{p.id}',
                      headers=headers).status_code == 404


def test_sandbox_checkout_fails_closed(client, actor, monkeypatch):
    _, patient, headers = actor
    bill = invoice(patient, 'CHECKOUT-3')
    monkeypatch.delenv('S13_PATIENT_CHECKOUT_SANDBOX_ENABLED', raising=False)
    r = client.post(f'/api/v1/patient-portal/invoices/{bill.id}/checkout',
                    headers={**headers, 'Idempotency-Key': 'S13-checkout-unique-0001'},
                    json={'gateway': 'paystack'})
    assert r.status_code == 503


def test_sandbox_checkout_rejects_live_key(client, actor, monkeypatch):
    _, patient, headers = actor
    bill = invoice(patient, 'CHECKOUT-4')
    monkeypatch.setenv('S13_PATIENT_CHECKOUT_SANDBOX_ENABLED', '1')
    monkeypatch.setattr(api, 'get_payment_gateway',
                        lambda *a, **k: SimpleNamespace(secret_key='sk_live_fake'))
    r = client.post(f'/api/v1/patient-portal/invoices/{bill.id}/checkout',
                    headers={**headers, 'Idempotency-Key': 'S13-checkout-unique-0002'},
                    json={'gateway': 'paystack'})
    assert r.status_code == 503


def test_no_arbitrary_invoice_or_amount_in_checkout(client, actor, monkeypatch):
    _, patient, headers = actor
    bill = invoice(patient, 'CHECKOUT-5')
    monkeypatch.setenv('S13_PATIENT_CHECKOUT_SANDBOX_ENABLED', '1')
    monkeypatch.setattr(api, 'get_payment_gateway',
                        lambda *a, **k: SimpleNamespace(secret_key='sk_test_fake'))
    r = client.post(f'/api/v1/patient-portal/invoices/{bill.id}/checkout',
                    headers={**headers, 'Idempotency-Key': 'S13-checkout-unique-0003'},
                    json={'gateway': 'paystack', 'amount': '0.01', 'patient_id': 999})
    assert r.status_code == 422


def test_checkout_uses_server_balance_and_patient_idempotency(client, actor, monkeypatch):
    user, patient, headers = actor
    bill = invoice(patient, 'CHECKOUT-6')
    monkeypatch.setenv('S13_PATIENT_CHECKOUT_SANDBOX_ENABLED', '1')
    monkeypatch.setattr(api, 'get_payment_gateway',
                        lambda *a, **k: SimpleNamespace(secret_key='sk_test_fake'))
    recorded = {}
    def fake_payment(**kwargs):
        recorded.update(kwargs)
        return {'payment': SimpleNamespace(id=17), 'outcome': 'pending',
                'checkout_url': 'https://checkout.paystack.com/test'}
    monkeypatch.setattr(api, 'orchestrate_payment', fake_payment)
    r = client.post(f'/api/v1/patient-portal/invoices/{bill.id}/checkout',
                    headers={**headers, 'Idempotency-Key': 'S13-checkout-unique-0004'},
                    json={'gateway': 'paystack'})
    assert r.status_code == 202
    assert r.json['data']['outcome'] == 'pending'
    assert recorded['amount'] == Decimal('250.00')
    assert recorded['idempotency_user_id'] == user.id
    assert recorded['clinic_id'] == patient.clinic_id
    assert recorded['require_test_credentials'] is True
    assert r.json['data']['checkout_url'].startswith('https://checkout.paystack.com/')


def test_checkout_host_allowlist_rejects_evil_and_http():
    assert pay._hosted_checkout_url({'status': 'initialized', 'authorization_url': 'https://evil.invalid/checkout'}) is None
    assert pay._hosted_checkout_url({'status': 'initialized', 'authorization_url': 'http://checkout.paystack.com/pay'}) is None
    assert pay._hosted_checkout_url({'status': 'initialized', 'authorization_url': 'https://checkout.paystack.com/secure'}) == 'https://checkout.paystack.com/secure'


def test_chat_empty_when_enabled(client, actor, monkeypatch):
    _, _, headers = actor
    monkeypatch.setattr(api.ChatPolicyService, 'ensure_chat_enabled', lambda *_: None)
    r = client.get('/api/v1/patient-portal/conversations', headers=headers)
    assert r.status_code == 200
    assert r.json['data']['items'] == []


def test_chat_unknown_message_target_denied(client, actor):
    _, _, headers = actor
    r = client.post('/api/v1/patient-portal/conversations/9999/messages',
                    headers=headers, json={'content': 'Synthetic check'})
    assert r.status_code == 404


def test_chat_query_does_not_expose_internal_case(client, actor, monkeypatch):
    _, _, headers = actor
    monkeypatch.setattr(api.ChatPolicyService, 'ensure_chat_enabled', lambda *_: None)
    r = client.get('/api/v1/patient-portal/conversations?patient_id=999', headers=headers)
    assert r.status_code == 422

def test_b03_portal_stays_held_outside_testing(client, actor, app):
    _,_,headers=actor
    first=client.get('/api/v1/patient-portal/me',headers=headers)
    assert first.status_code==200 and 'no-store' in first.headers['Cache-Control']
    app.config['TESTING']=False
    try:
        assert client.get('/api/v1/patient-portal/me',headers=headers).status_code==503
        assert client.get('/api/v1/patient-portal/invoices/1',headers=headers).status_code==503
    finally: app.config['TESTING']=True


def test_b03_portal_clinic_hours_staged_timestamp_contract(app):
    from datetime import datetime,timedelta,timezone
    from app.modules.patient.routes.patient_portal_route import _booking_times
    start=datetime.now(timezone.utc)+timedelta(days=2)
    app.config['CLINIC_EXTENDED_OPERATING_HOURS_ENABLED']=True
    app.config['CLINIC_BOOKING_HOURS_ENFORCEMENT_ENABLED']=True
    a,b=_booking_times(start,start+timedelta(minutes=30))
    assert a.tzinfo is not None and b.tzinfo is not None and a==start


def test_b03_payment_service_checks_actual_provider_credentials(app,monkeypatch):
    from app.core.enums.billing_enums import PaymentGateway,PaymentMethod
    from app.core.exceptions import ServiceUnavailableError
    def fake_reservation(**kwargs):
        kwargs['before_provider_initialization'](gateway=PaymentGateway.PAYSTACK,invoice=None)
        pytest.fail('Live credential reached payment reservation')
    monkeypatch.setattr(pay,'_get_or_create_pending_payment',fake_reservation)
    monkeypatch.setattr(pay,'get_payment_gateway',lambda *a,**k: SimpleNamespace(secret_key='sk_live_fake'))
    with pytest.raises(ServiceUnavailableError):
        pay.orchestrate_payment(clinic_id=1,invoice_id=1,amount=Decimal('1.00'),method=PaymentMethod.CARD,gateway=PaymentGateway.PAYSTACK,currency='NGN',idempotency_key='synthetic-checkout-0001',require_test_credentials=True)


def test_b03_sandbox_pending_replay_does_not_call_provider(app,monkeypatch):
    from app.core.enums.billing_enums import PaymentGateway,PaymentMethod,PaymentStatus
    payment=SimpleNamespace(status=PaymentStatus.PENDING)
    monkeypatch.setattr(pay,'_get_or_create_pending_payment',lambda **kwargs:(payment,False))
    monkeypatch.setattr(pay,'_reconcile_pending_payment',lambda **kwargs:pytest.fail('Unsafe sandbox provider replay'))
    r=pay.orchestrate_payment(clinic_id=1,invoice_id=1,amount=Decimal('1.00'),method=PaymentMethod.CARD,gateway=PaymentGateway.PAYSTACK,currency='NGN',idempotency_key='synthetic-checkout-0002',require_test_credentials=True)
    assert r['outcome']=='pending' and 'checkout_url' not in r


def test_b03_patient_booking_respects_existing_clinic_hours_gate(client,actor,app,make_staff):
    from datetime import datetime,timedelta,timezone,time
    from app.modules.appointment.models.appointment_model import Appointment
    _,patient,headers=actor
    clinic=patient.clinic
    doctor=make_staff(clinic,role=Role.DOCTOR)
    clinic.opening_time=time(8,0);clinic.closing_time=time(18,0);clinic.timezone='UTC'
    db.session.commit()
    app.config['CLINIC_EXTENDED_OPERATING_HOURS_ENABLED']=True
    app.config['CLINIC_BOOKING_HOURS_ENFORCEMENT_ENABLED']=True
    start=(datetime.now(timezone.utc)+timedelta(days=2)).replace(hour=2,minute=0,second=0,microsecond=0)
    response=client.post('/api/v1/patient-portal/appointments',headers={**headers,'Idempotency-Key':'b03-hours-outside-0001'},json={'staff_id':doctor.id,'scheduled_start':start.isoformat(),'scheduled_end':(start+timedelta(minutes=30)).isoformat(),'appointment_type':'in_person'})
    assert response.status_code==422,response.get_json()
    assert 'outside clinic operating hours' in str(response.get_json())
    assert Appointment.query.filter_by(patient_id=patient.id,clinic_id=clinic.id).count()==0
