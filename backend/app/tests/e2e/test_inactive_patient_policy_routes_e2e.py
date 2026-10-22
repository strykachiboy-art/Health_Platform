from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.enums.lab_enums import SampleType
from app.core.enums.role_enums import Role


def _assert_inactive_patient_rejected(response):
    body = response.get_json() or {}
    assert response.status_code == 422, {
        "status_code": response.status_code,
        "body": body,
    }

    message = body.get("error") or body.get("message") or str(body)
    assert "inactive" in str(message).lower(), body


def test_inactive_patient_cannot_create_appointment_via_api(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
):
    staff, headers = make_authenticated_staff(clinic, Role.ADMIN)
    patient = make_patient(clinic, is_active=False)

    start = datetime.now(timezone.utc) + timedelta(days=3)
    end = start + timedelta(minutes=30)

    response = client.post(
        "/api/v1/appointments/",
        headers=headers,
        json={
            "patient_id": patient.id,
            "staff_id": staff.id,
            "scheduled_start": start.isoformat(),
            "scheduled_end": end.isoformat(),
            "appointment_type": "in_person",
            "reason": "Finding 10 inactive-patient regression",
        },
    )

    _assert_inactive_patient_rejected(response)


def test_inactive_patient_cannot_start_consultation_via_api(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
):
    staff, headers = make_authenticated_staff(clinic, Role.ADMIN)
    patient = make_patient(clinic, is_active=False)

    response = client.post(
        "/api/v1/consultations/",
        headers=headers,
        json={
            "patient_id": patient.id,
            "staff_id": staff.id,
            "consultation_type": "general",
            "chief_complaint": "Finding 10 inactive-patient regression",
            "symptoms": "Test-only symptom",
        },
    )

    _assert_inactive_patient_rejected(response)


def test_inactive_patient_cannot_create_lab_order_via_api(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    make_lab_test,
):
    staff, headers = make_authenticated_staff(clinic, Role.ADMIN)
    patient = make_patient(clinic, is_active=False)
    lab_test = make_lab_test(
        clinic,
        code=f"F10-LAB-{clinic.id}-{patient.id}",
        sample_type=SampleType.BLOOD,
        price=Decimal("25.00"),
        is_active=True,
    )

    response = client.post(
        "/api/v1/lab/orders",
        headers=headers,
        json={
            "patient_id": patient.id,
            "test_ids": [lab_test.id],
        },
    )

    _assert_inactive_patient_rejected(response)


def test_inactive_patient_cannot_create_prescription_via_api(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    make_drug,
):
    _staff, headers = make_authenticated_staff(clinic, Role.DOCTOR)
    patient = make_patient(clinic, is_active=False)
    drug = make_drug(clinic)

    response = client.post(
        "/api/v1/prescriptions",
        headers=headers,
        json={
            "patient_id": patient.id,
            "items": [
                {
                    "drug_id": drug.id,
                    "dosage": "500 mg",
                    "frequency": "twice daily",
                    "duration": "7 days",
                    "quantity": 14,
                    "instructions": "Take after meals",
                }
            ],
            "notes": "Finding 10 inactive-patient regression",
        },
    )

    _assert_inactive_patient_rejected(response)


def test_inactive_patient_profile_remains_readable_via_api(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
):
    _staff, headers = make_authenticated_staff(clinic, Role.ADMIN)
    patient = make_patient(clinic, is_active=False)

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers=headers,
    )

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["data"]["id"] == patient.id
    assert body["data"]["is_active"] is False


def test_inactive_patient_appointment_history_remains_readable_via_api(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    make_appointment,
):
    staff, headers = make_authenticated_staff(clinic, Role.ADMIN)
    patient = make_patient(clinic)
    appointment = make_appointment(clinic, patient, staff)

    patient.is_active = False

    response = client.get(
        f"/api/v1/appointments/patient/{patient.id}",
        headers=headers,
    )

    assert response.status_code == 200, response.get_json()
    items = response.get_json()["data"]["items"]
    assert appointment.id in {item["id"] for item in items}


def test_inactive_patient_consultation_history_remains_readable_via_api(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    make_consultation,
):
    staff, headers = make_authenticated_staff(clinic, Role.ADMIN)
    patient = make_patient(clinic)
    consultation = make_consultation(clinic, patient, staff)

    patient.is_active = False

    response = client.get(
        f"/api/v1/consultations/patient/{patient.id}",
        headers=headers,
    )

    assert response.status_code == 200, response.get_json()
    items = response.get_json()["data"]
    assert consultation.id in {item["id"] for item in items}


def test_inactive_patient_lab_history_remains_readable_via_api(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    make_lab_test,
    make_lab_order,
):
    staff, headers = make_authenticated_staff(clinic, Role.ADMIN)
    patient = make_patient(clinic)
    lab_test = make_lab_test(
        clinic,
        code=f"F10-HIST-{clinic.id}-{patient.id}",
        sample_type=SampleType.BLOOD,
        price=Decimal("25.00"),
        is_active=True,
    )
    order = make_lab_order(
        clinic,
        patient,
        staff,
        tests=[lab_test],
    )

    patient.is_active = False

    response = client.get(
        f"/api/v1/lab/orders?patient_id={patient.id}",
        headers=headers,
    )

    assert response.status_code == 200, response.get_json()
    items = response.get_json()["data"]["items"]
    assert order.id in {item["id"] for item in items}


def test_inactive_patient_prescription_history_remains_readable_via_api(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    make_prescription,
):
    staff, headers = make_authenticated_staff(clinic, Role.DOCTOR)
    patient = make_patient(clinic)
    prescription = make_prescription(clinic, patient, staff)

    patient.is_active = False

    response = client.get(
        f"/api/v1/prescriptions/patients/{patient.id}",
        headers=headers,
    )

    assert response.status_code == 200, response.get_json()
    items = response.get_json()["data"]["items"]
    assert prescription.id in {item["id"] for item in items}
