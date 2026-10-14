from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.enums.consultation_enums import (
    ConsultationStatus,
    ConsultationType,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.modules.consultation.routes import consultation_route


def make_consultation(
    *,
    clinic_id=1,
    patient_id=1,
    staff_id=1,
    appointment_id=None,
    consultation_id=1,
    consultation_type=ConsultationType.GENERAL,
    status=ConsultationStatus.IN_PROGRESS,
    template_id=None,
    chief_complaint="Fever",
    symptoms="Fever and fatigue",
    diagnosis=None,
    treatment_plan=None,
    notes=None,
    voice_note_url=None,
    transcribed_text=None,
):
    now = datetime.now(timezone.utc)

    return SimpleNamespace(
        id=consultation_id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        staff_id=staff_id,
        appointment_id=appointment_id,
        icd10_code=None,
        consultation_type=consultation_type,
        status=status,
        chief_complaint=chief_complaint,
        symptoms=symptoms,
        diagnosis=diagnosis,
        treatment_plan=treatment_plan,
        notes=notes,
        voice_note_url=voice_note_url,
        transcribed_text=transcribed_text,
        template_id=template_id,
        started_at=now,
        ended_at=None,
        created_at=now,
        updated_at=now,
    )


def make_patient(
    *,
    patient_id=1,
    clinic_id=1,
    patient_number="PAT-001",
    first_name="Jane",
    last_name="Doe",
    gender=None,
    date_of_birth=None,
    phone="08000000000",
    email="jane@example.com",
    is_active=True,
):
    return SimpleNamespace(
        id=patient_id,
        clinic_id=clinic_id,
        patient_number=patient_number,
        first_name=first_name,
        last_name=last_name,
        gender=gender,
        date_of_birth=date_of_birth,
        phone=phone,
        email=email,
        is_active=is_active,
    )


def make_template(
    *,
    template_id=1,
    clinic_id=1,
    name="General Consultation",
    specialty="General Medicine",
    structure=None,
    is_active=True,
):
    now = datetime.now(timezone.utc)

    return SimpleNamespace(
        id=template_id,
        clinic_id=clinic_id,
        name=name,
        specialty=specialty,
        structure=structure
        or {
            "sections": [
                "history",
                "examination",
                "assessment",
                "plan",
            ]
        },
        is_active=is_active,
        created_at=now,
    )


def make_pagination(
    *,
    page=1,
    per_page=50,
    total=0,
    pages=0,
    has_next=False,
    has_prev=False,
    next_page=None,
    prev_page=None,
):
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_next": has_next,
        "has_prev": has_prev,
        "next_page": next_page,
        "prev_page": prev_page,
    }


def test_start_consultation_success(
    app,
    clinic,
    patient,
    staff,
    auth_headers_for,
    monkeypatch,
):
    headers = auth_headers_for(staff.user, role=Role.ADMIN)

    consultation = make_consultation(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
    )

    called = {}

    def fake_start_consultation(**kwargs):
        called.update(kwargs)
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "start_consultation",
        fake_start_consultation,
    )

    response = app.test_client().post(
        "/api/v1/consultations/",
        json={
            "patient_id": patient.id,
            "staff_id": staff.id,
            "consultation_type": ConsultationType.GENERAL.value,
            "chief_complaint": "Fever",
            "symptoms": "Fever and fatigue",
        },
        headers=headers,
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == consultation.id
    assert body["data"]["clinic_id"] == clinic.id
    assert body["data"]["patient_id"] == patient.id
    assert body["data"]["staff_id"] == staff.id
    assert body["data"]["consultation_type"] == (
        ConsultationType.GENERAL.value
    )
    assert body["data"]["status"] == (
        ConsultationStatus.IN_PROGRESS.value
    )

    assert called["clinic_id"] == clinic.id
    assert called["patient_id"] == patient.id
    assert called["staff_id"] == staff.id
    assert called["consultation_type"] == ConsultationType.GENERAL


def test_start_consultation_forwards_consultation_type(
    app,
    clinic,
    patient,
    staff,
    auth_headers_for,
    monkeypatch,
):
    headers = auth_headers_for(staff.user, role=Role.DOCTOR)

    consultation = make_consultation(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
        consultation_type=ConsultationType.EMERGENCY,
    )

    called = {}

    def fake_start_consultation(**kwargs):
        called.update(kwargs)
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "start_consultation",
        fake_start_consultation,
    )

    response = app.test_client().post(
        "/api/v1/consultations/",
        json={
            "patient_id": patient.id,
            "staff_id": staff.id,
            "consultation_type": ConsultationType.EMERGENCY.value,
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert called["consultation_type"] == ConsultationType.EMERGENCY
    assert response.get_json()["data"]["consultation_type"] == (
        ConsultationType.EMERGENCY.value
    )


def test_start_consultation_ignores_client_clinic_id(
    app,
    clinic,
    patient,
    staff,
    auth_headers_for,
    monkeypatch,
):
    headers = auth_headers_for(staff.user, role=Role.ADMIN)

    consultation = make_consultation(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
    )

    called = {}

    def fake_start_consultation(**kwargs):
        called.update(kwargs)
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "start_consultation",
        fake_start_consultation,
    )

    response = app.test_client().post(
        "/api/v1/consultations/",
        json={
            "clinic_id": 999999,
            "patient_id": patient.id,
            "staff_id": staff.id,
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert called["clinic_id"] == clinic.id
    assert called["clinic_id"] != 999999


def test_start_consultation_invalid_payload_returns_422(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    response = app.test_client().post(
        "/api/v1/consultations/",
        json={
            "patient_id": 0,
            "staff_id": 0,
        },
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_start_consultation_rejects_blank_chief_complaint(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    response = app.test_client().post(
        "/api/v1/consultations/",
        json={
            "patient_id": 1,
            "staff_id": 1,
            "chief_complaint": "   ",
        },
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_start_consultation_rejects_unknown_field(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    response = app.test_client().post(
        "/api/v1/consultations/",
        json={
            "patient_id": 1,
            "staff_id": 1,
            "unknown_field": "bad",
        },
        headers=headers,
    )

    assert response.status_code == 422


def test_get_consultation_success(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    consultation = make_consultation(
        clinic_id=clinic.id,
    )

    called = {}

    def fake_get_consultation(**kwargs):
        called.update(kwargs)
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "get_consultation",
        fake_get_consultation,
    )

    response = app.test_client().get(
        f"/api/v1/consultations/{consultation.id}",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == consultation.id
    assert body["data"]["clinic_id"] == clinic.id
    assert body["data"]["consultation_type"] == (
        ConsultationType.GENERAL.value
    )

    assert called["consultation_id"] == consultation.id
    assert called["clinic_id"] == clinic.id


def test_get_consultation_uses_authenticated_clinic(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    consultation = make_consultation(
        clinic_id=clinic.id,
        consultation_id=44,
    )

    called = {}

    def fake_get_consultation(**kwargs):
        called.update(kwargs)
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "get_consultation",
        fake_get_consultation,
    )

    response = app.test_client().get(
        "/api/v1/consultations/44",
        headers=headers,
    )

    assert response.status_code == 200
    assert called["consultation_id"] == 44
    assert called["clinic_id"] == clinic.id


def test_update_consultation_success(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    consultation = make_consultation(
        clinic_id=clinic.id,
        consultation_id=10,
        notes="Updated notes",
        diagnosis="Viral infection",
    )

    called = {}

    def fake_update_consultation_note(**kwargs):
        called.update(kwargs)
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "update_consultation_note",
        fake_update_consultation_note,
    )

    response = app.test_client().patch(
        "/api/v1/consultations/10",
        json={
            "diagnosis": "Viral infection",
            "notes": "Updated notes",
        },
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["diagnosis"] == "Viral infection"
    assert body["data"]["notes"] == "Updated notes"

    assert called["consultation_id"] == 10
    assert called["clinic_id"] == clinic.id
    assert called["diagnosis"] == "Viral infection"
    assert called["notes"] == "Updated notes"


def test_update_consultation_rejects_blank_notes(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    response = app.test_client().patch(
        "/api/v1/consultations/1",
        json={
            "notes": "   ",
        },
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_update_consultation_rejects_unknown_field(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    response = app.test_client().patch(
        "/api/v1/consultations/1",
        json={
            "clinic_id": 999,
        },
        headers=headers,
    )

    assert response.status_code == 422


def test_complete_consultation_success(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    consultation = make_consultation(
        clinic_id=clinic.id,
        consultation_id=20,
        status=ConsultationStatus.COMPLETED,
        diagnosis="Malaria",
        treatment_plan="Oral treatment",
        notes="Follow up in 3 days",
    )

    called = {}

    def fake_complete_consultation(**kwargs):
        called.update(kwargs)
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "complete_consultation",
        fake_complete_consultation,
    )

    response = app.test_client().post(
        "/api/v1/consultations/20/complete",
        json={
            "diagnosis": "Malaria",
            "treatment_plan": "Oral treatment",
            "notes": "Follow up in 3 days",
        },
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["status"] == (
        ConsultationStatus.COMPLETED.value
    )
    assert body["data"]["diagnosis"] == "Malaria"

    assert called["consultation_id"] == 20
    assert called["clinic_id"] == clinic.id
    assert called["diagnosis"] == "Malaria"


def test_complete_consultation_requires_diagnosis(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    response = app.test_client().post(
        "/api/v1/consultations/20/complete",
        json={},
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_complete_consultation_rejects_blank_diagnosis(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    response = app.test_client().post(
        "/api/v1/consultations/20/complete",
        json={
            "diagnosis": "   ",
        },
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_cancel_consultation_success(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.NURSE)

    consultation = make_consultation(
        clinic_id=clinic.id,
        consultation_id=30,
        status=ConsultationStatus.CANCELLED,
    )

    called = {}

    def fake_cancel_consultation(**kwargs):
        called.update(kwargs)
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "cancel_consultation",
        fake_cancel_consultation,
    )

    response = app.test_client().post(
        "/api/v1/consultations/30/cancel",
        json={
            "reason": "Patient unavailable",
        },
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["status"] == (
        ConsultationStatus.CANCELLED.value
    )

    assert called["consultation_id"] == 30
    assert called["clinic_id"] == clinic.id
    assert called["reason"] == "Patient unavailable"


def test_cancel_consultation_allows_missing_reason(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    consultation = make_consultation(
        clinic_id=clinic.id,
        consultation_id=31,
        status=ConsultationStatus.CANCELLED,
    )

    called = {}

    def fake_cancel_consultation(**kwargs):
        called.update(kwargs)
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "cancel_consultation",
        fake_cancel_consultation,
    )

    response = app.test_client().post(
        "/api/v1/consultations/31/cancel",
        json={},
        headers=headers,
    )

    assert response.status_code == 200
    assert called["consultation_id"] == 31
    assert called["clinic_id"] == clinic.id
    assert "reason" not in called


def test_cancel_consultation_rejects_blank_reason(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    response = app.test_client().post(
        "/api/v1/consultations/31/cancel",
        json={
            "reason": "   ",
        },
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_patient_consultations_success(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    consultations = [
        make_consultation(
            clinic_id=clinic.id,
            patient_id=55,
            consultation_id=1,
        ),
        make_consultation(
            clinic_id=clinic.id,
            patient_id=55,
            consultation_id=2,
        ),
    ]

    pagination = make_pagination(
        page=1,
        per_page=50,
        total=2,
        pages=1,
    )

    called = {}

    def fake_get_consultations_for_patient(**kwargs):
        called.update(kwargs)
        return consultations, pagination

    monkeypatch.setattr(
        consultation_route,
        "get_consultations_for_patient",
        fake_get_consultations_for_patient,
    )

    response = app.test_client().get(
        "/api/v1/consultations/patient/55",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert len(body["data"]) == 2
    assert body["data"][0]["id"] == 1
    assert body["data"][1]["id"] == 2
    assert body["pagination"] == pagination

    assert called["patient_id"] == 55
    assert called["clinic_id"] == clinic.id
    assert called["page"] == 1
    assert called["per_page"] == 50
    assert called["consultation_type"] is None


def test_patient_consultations_forwards_pagination(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    pagination = make_pagination(
        page=3,
        per_page=25,
        total=55,
        pages=3,
        has_next=False,
        has_prev=True,
        prev_page=2,
    )

    called = {}

    def fake_get_consultations_for_patient(**kwargs):
        called.update(kwargs)
        return [], pagination

    monkeypatch.setattr(
        consultation_route,
        "get_consultations_for_patient",
        fake_get_consultations_for_patient,
    )

    response = app.test_client().get(
        "/api/v1/consultations/patient/55?page=3&per_page=25",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["data"] == []
    assert body["pagination"] == pagination

    assert called["page"] == 3
    assert called["per_page"] == 25


def test_patient_consultations_filters_by_type(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    consultation = make_consultation(
        clinic_id=clinic.id,
        patient_id=55,
        consultation_id=3,
        consultation_type=ConsultationType.SPECIALIST,
    )

    pagination = make_pagination(
        total=1,
        pages=1,
    )

    called = {}

    def fake_get_consultations_for_patient(**kwargs):
        called.update(kwargs)
        return [consultation], pagination

    monkeypatch.setattr(
        consultation_route,
        "get_consultations_for_patient",
        fake_get_consultations_for_patient,
    )

    response = app.test_client().get(
        "/api/v1/consultations/patient/55"
        "?consultation_type=specialist",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"][0]["consultation_type"] == (
        ConsultationType.SPECIALIST.value
    )

    assert called["consultation_type"] == (
        ConsultationType.SPECIALIST
    )
    assert called["page"] == 1
    assert called["per_page"] == 50


def test_patient_consultations_rejects_invalid_type(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    response = app.test_client().get(
        "/api/v1/consultations/patient/55"
        "?consultation_type=invalid",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "Invalid consultation type" in body["error"]


def test_my_patients_success(
    app,
    clinic,
    auth_headers_for,
    staff,
    monkeypatch,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    patients = [
        make_patient(
            patient_id=10,
            clinic_id=clinic.id,
            patient_number="PAT-010",
            first_name="Alice",
            last_name="Adams",
        ),
        make_patient(
            patient_id=11,
            clinic_id=clinic.id,
            patient_number="PAT-011",
            first_name="Brian",
            last_name="Brown",
        ),
    ]

    pagination = make_pagination(
        page=1,
        per_page=50,
        total=2,
        pages=1,
    )

    called = {}

    def fake_get_patients_seen_by_staff(**kwargs):
        called.update(kwargs)
        return patients, pagination

    monkeypatch.setattr(
        consultation_route,
        "get_patients_seen_by_staff",
        fake_get_patients_seen_by_staff,
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert len(body["data"]) == 2

    assert body["data"][0]["id"] == 10
    assert body["data"][0]["patient_number"] == "PAT-010"
    assert body["data"][0]["first_name"] == "Alice"
    assert body["data"][0]["last_name"] == "Adams"

    assert body["data"][1]["id"] == 11
    assert body["data"][1]["patient_number"] == "PAT-011"

    assert body["pagination"] == pagination

    assert called["staff_id"] == staff.id
    assert called["clinic_id"] == clinic.id
    assert called["page"] == 1
    assert called["per_page"] == 50


def test_my_patients_forwards_pagination(
    app,
    clinic,
    auth_headers_for,
    staff,
    monkeypatch,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    pagination = make_pagination(
        page=2,
        per_page=10,
        total=15,
        pages=2,
        has_next=False,
        has_prev=True,
        prev_page=1,
    )

    called = {}

    def fake_get_patients_seen_by_staff(**kwargs):
        called.update(kwargs)
        return [], pagination

    monkeypatch.setattr(
        consultation_route,
        "get_patients_seen_by_staff",
        fake_get_patients_seen_by_staff,
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients"
        "?page=2&per_page=10",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"] == []
    assert body["pagination"] == pagination

    assert called["staff_id"] == staff.id
    assert called["clinic_id"] == clinic.id
    assert called["page"] == 2
    assert called["per_page"] == 10


def test_my_patients_returns_serialized_patient_fields(
    app,
    clinic,
    auth_headers_for,
    staff,
    monkeypatch,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        patient_id=55,
        clinic_id=clinic.id,
        patient_number="PAT-055",
        first_name="John",
        last_name="Doe",
        phone="08111111111",
        email="john@example.com",
        is_active=True,
    )

    monkeypatch.setattr(
        consultation_route,
        "get_patients_seen_by_staff",
        lambda **kwargs: (
            [patient],
            make_pagination(
                total=1,
                pages=1,
            ),
        ),
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients",
        headers=headers,
    )

    assert response.status_code == 200

    data = response.get_json()["data"][0]

    assert data["id"] == 55
    assert data["patient_number"] == "PAT-055"
    assert data["first_name"] == "John"
    assert data["last_name"] == "Doe"
    assert data["phone"] == "08111111111"
    assert data["email"] == "john@example.com"
    assert data["is_active"] is True


def test_my_patients_uses_authenticated_staff(
    app,
    clinic,
    auth_headers_for,
    staff,
    monkeypatch,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    called = {}

    def fake_get_patients_seen_by_staff(**kwargs):
        called.update(kwargs)
        return [], make_pagination()

    monkeypatch.setattr(
        consultation_route,
        "get_patients_seen_by_staff",
        fake_get_patients_seen_by_staff,
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients",
        headers=headers,
    )

    assert response.status_code == 200

    assert called["staff_id"] == staff.id
    assert called["staff_id"] != 999999
    assert called["clinic_id"] == clinic.id


def test_my_patients_does_not_accept_staff_id_from_query(
    app,
    clinic,
    auth_headers_for,
    staff,
    monkeypatch,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    called = {}

    def fake_get_patients_seen_by_staff(**kwargs):
        called.update(kwargs)
        return [], make_pagination()

    monkeypatch.setattr(
        consultation_route,
        "get_patients_seen_by_staff",
        fake_get_patients_seen_by_staff,
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients?staff_id=999999",
        headers=headers,
    )

    assert response.status_code == 200
    assert called["staff_id"] == staff.id
    assert called["staff_id"] != 999999


def test_my_patients_rejects_invalid_page(
    app,
    auth_headers_for,
    staff,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients?page=0",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "page must be greater than 0"


def test_my_patients_rejects_invalid_per_page(
    app,
    auth_headers_for,
    staff,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients?per_page=0",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "per_page must be greater than 0"


def test_my_patients_rejects_per_page_above_maximum(
    app,
    auth_headers_for,
    staff,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients?per_page=501",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "per_page must be less than or equal to 500"
    )


def test_my_patients_rejects_non_integer_page(
    app,
    auth_headers_for,
    staff,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients?page=abc",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "page must be an integer"


def test_my_patients_rejects_non_integer_per_page(
    app,
    auth_headers_for,
    staff,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients?per_page=abc",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "per_page must be an integer"


def test_my_patients_rejects_unlinked_user(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(
        user,
        role=Role.DOCTOR,
    )

    monkeypatch.setattr(
        consultation_route,
        "_get_current_user",
        lambda: SimpleNamespace(
            clinic_id=clinic.id,
            is_active=True,
            staff=None,
        ),
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients",
        headers=headers,
    )

    assert response.status_code == 403

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Authenticated user is not linked "
        "to a staff record"
    )


def test_my_patients_rejects_staff_from_other_clinic(
    app,
    clinic,
    auth_headers_for,
    staff,
    monkeypatch,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    monkeypatch.setattr(
        consultation_route,
        "_get_authenticated_clinic_id",
        lambda: (
            clinic.id + 999,
            None,
        ),
    )

    response = app.test_client().get(
        "/api/v1/consultations/my-patients",
        headers=headers,
    )

    assert response.status_code == 403

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Access denied"


def test_staff_consultations_success(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    consultations = [
        make_consultation(
            clinic_id=clinic.id,
            staff_id=77,
            consultation_id=1,
        )
    ]

    pagination = make_pagination(
        total=1,
        pages=1,
    )

    called = {}

    def fake_get_consultations_for_staff(**kwargs):
        called.update(kwargs)
        return consultations, pagination

    monkeypatch.setattr(
        consultation_route,
        "get_consultations_for_staff",
        fake_get_consultations_for_staff,
    )

    response = app.test_client().get(
        "/api/v1/consultations/staff/77?status=in_progress",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert len(body["data"]) == 1
    assert body["pagination"] == pagination

    assert called["staff_id"] == 77
    assert called["clinic_id"] == clinic.id
    assert called["status"] == ConsultationStatus.IN_PROGRESS
    assert called["consultation_type"] is None
    assert called["page"] == 1
    assert called["per_page"] == 50


def test_staff_consultations_without_status(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    called = {}

    def fake_get_consultations_for_staff(**kwargs):
        called.update(kwargs)
        return [], make_pagination()

    monkeypatch.setattr(
        consultation_route,
        "get_consultations_for_staff",
        fake_get_consultations_for_staff,
    )

    response = app.test_client().get(
        "/api/v1/consultations/staff/77",
        headers=headers,
    )

    assert response.status_code == 200
    assert called["status"] is None
    assert called["consultation_type"] is None
    assert called["page"] == 1
    assert called["per_page"] == 50


def test_staff_consultations_filters_by_type(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    consultation = make_consultation(
        clinic_id=clinic.id,
        staff_id=77,
        consultation_id=5,
        consultation_type=ConsultationType.EMERGENCY,
        status=ConsultationStatus.IN_PROGRESS,
    )

    pagination = make_pagination(
        total=1,
        pages=1,
    )

    called = {}

    def fake_get_consultations_for_staff(**kwargs):
        called.update(kwargs)
        return [consultation], pagination

    monkeypatch.setattr(
        consultation_route,
        "get_consultations_for_staff",
        fake_get_consultations_for_staff,
    )

    response = app.test_client().get(
        "/api/v1/consultations/staff/77"
        "?status=in_progress&consultation_type=emergency",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"][0]["consultation_type"] == (
        ConsultationType.EMERGENCY.value
    )

    assert called["staff_id"] == 77
    assert called["clinic_id"] == clinic.id
    assert called["status"] == ConsultationStatus.IN_PROGRESS
    assert called["consultation_type"] == ConsultationType.EMERGENCY


def test_staff_consultations_forwards_pagination(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    pagination = make_pagination(
        page=2,
        per_page=10,
        total=15,
        pages=2,
        has_next=False,
        has_prev=True,
        prev_page=1,
    )

    called = {}

    def fake_get_consultations_for_staff(**kwargs):
        called.update(kwargs)
        return [], pagination

    monkeypatch.setattr(
        consultation_route,
        "get_consultations_for_staff",
        fake_get_consultations_for_staff,
    )

    response = app.test_client().get(
        "/api/v1/consultations/staff/77?page=2&per_page=10",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["pagination"] == pagination
    assert called["page"] == 2
    assert called["per_page"] == 10


def test_staff_consultations_rejects_invalid_status(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    response = app.test_client().get(
        "/api/v1/consultations/staff/77?status=invalid",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "Invalid consultation status" in body["error"]


def test_staff_consultations_rejects_invalid_type(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    response = app.test_client().get(
        "/api/v1/consultations/staff/77"
        "?consultation_type=invalid",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "Invalid consultation type" in body["error"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/consultations/patient/55?page=0",
        "/api/v1/consultations/patient/55?page=-1",
        "/api/v1/consultations/staff/77?page=0",
        "/api/v1/consultations/staff/77?page=-1",
        "/api/v1/consultations/templates?page=0",
        "/api/v1/consultations/templates?page=-1",
    ],
)
def test_pagination_rejects_invalid_page(
    app,
    auth_headers_for,
    user,
    path,
):
    headers = auth_headers_for(
        user,
        role=Role.ADMIN,
    )

    response = app.test_client().get(
        path,
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "page must be greater than 0"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/consultations/patient/55?page=abc",
        "/api/v1/consultations/staff/77?page=abc",
        "/api/v1/consultations/templates?page=abc",
    ],
)
def test_pagination_rejects_non_integer_page(
    app,
    auth_headers_for,
    user,
    path,
):
    headers = auth_headers_for(
        user,
        role=Role.ADMIN,
    )

    response = app.test_client().get(
        path,
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "page must be an integer"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/consultations/patient/55?per_page=0",
        "/api/v1/consultations/patient/55?per_page=-1",
        "/api/v1/consultations/staff/77?per_page=0",
        "/api/v1/consultations/staff/77?per_page=-1",
        "/api/v1/consultations/templates?per_page=0",
        "/api/v1/consultations/templates?per_page=-1",
    ],
)
def test_pagination_rejects_invalid_per_page(
    app,
    auth_headers_for,
    user,
    path,
):
    headers = auth_headers_for(
        user,
        role=Role.ADMIN,
    )

    response = app.test_client().get(
        path,
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "per_page must be greater than 0"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/consultations/patient/55?per_page=501",
        "/api/v1/consultations/staff/77?per_page=501",
        "/api/v1/consultations/templates?per_page=501",
    ],
)
def test_pagination_rejects_per_page_above_maximum(
    app,
    auth_headers_for,
    user,
    path,
):
    headers = auth_headers_for(
        user,
        role=Role.ADMIN,
    )

    response = app.test_client().get(
        path,
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "per_page must be less than or equal to 500"
    )


def test_create_template_success(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    template = make_template(
        clinic_id=clinic.id,
    )

    called = {}

    def fake_create_consultation_template(**kwargs):
        called.update(kwargs)
        return template

    monkeypatch.setattr(
        consultation_route,
        "create_consultation_template",
        fake_create_consultation_template,
    )

    response = app.test_client().post(
        "/api/v1/consultations/templates",
        json={
            "name": "General Consultation",
            "specialty": "General Medicine",
            "structure": {
                "sections": [
                    "history",
                    "examination",
                    "assessment",
                    "plan",
                ]
            },
        },
        headers=headers,
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == template.id
    assert body["data"]["clinic_id"] == clinic.id
    assert body["data"]["name"] == "General Consultation"

    assert called["clinic_id"] == clinic.id
    assert called["name"] == "General Consultation"


def test_create_template_forbidden_for_nurse(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.NURSE)

    response = app.test_client().post(
        "/api/v1/consultations/templates",
        json={
            "name": "Nursing Template",
            "structure": {
                "sections": ["notes"]
            },
        },
        headers=headers,
    )

    assert response.status_code == 403


def test_create_template_rejects_blank_name(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    response = app.test_client().post(
        "/api/v1/consultations/templates",
        json={
            "name": "   ",
            "structure": {
                "sections": ["history"]
            },
        },
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_create_template_rejects_empty_structure(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    response = app.test_client().post(
        "/api/v1/consultations/templates",
        json={
            "name": "Empty Template",
            "structure": {},
        },
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_active_templates_admin_without_clinic_filter(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)
    called = {}

    def fake_get_active_templates(**kwargs):
        called.update(kwargs)
        return [
            make_template(clinic_id=None, name="Global"),
            make_template(clinic_id=clinic.id, name="Clinic"),
        ], make_pagination(total=2, pages=1)

    monkeypatch.setattr(
        consultation_route, "get_active_templates",
        fake_get_active_templates,
    )

    response = app.test_client().get(
        "/api/v1/consultations/templates", headers=headers,
    )

    assert response.status_code == 200
    assert len(response.get_json()["data"]) == 2
    assert called["clinic_id"] == clinic.id

def test_active_templates_admin_with_clinic_filter(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)
    called = {}

    def fake_get_active_templates(**kwargs):
        called.update(kwargs)
        return [make_template(clinic_id=clinic.id)], make_pagination(total=1)

    monkeypatch.setattr(
        consultation_route, "get_active_templates",
        fake_get_active_templates,
    )

    response = app.test_client().get(
        f"/api/v1/consultations/templates?clinic_id={clinic.id}",
        headers=headers,
    )

    assert response.status_code == 200
    assert called["clinic_id"] == clinic.id


def test_active_templates_admin_cannot_select_another_clinic(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)
    called = {"value": False}

    def fake_get_active_templates(**kwargs):
        called["value"] = True
        return [], make_pagination()

    monkeypatch.setattr(
        consultation_route, "get_active_templates",
        fake_get_active_templates,
    )

    response = app.test_client().get(
        f"/api/v1/consultations/templates?clinic_id={clinic.id + 1}",
        headers=headers,
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "Access denied"
    assert called["value"] is False

def test_active_templates_admin_without_clinic_is_denied(
    app,
    auth_headers_for,
    user,
):
    user.clinic_id = None
    headers = auth_headers_for(user, role=Role.ADMIN)

    response = app.test_client().get(
        "/api/v1/consultations/templates", headers=headers,
    )

    assert response.status_code == 403


def test_active_templates_non_admin_uses_authenticated_clinic(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    called = {}

    def fake_get_active_templates(**kwargs):
        called.update(kwargs)
        return [], make_pagination()

    monkeypatch.setattr(
        consultation_route,
        "get_active_templates",
        fake_get_active_templates,
    )

    response = app.test_client().get(
        "/api/v1/consultations/templates",
        headers=headers,
    )

    assert response.status_code == 200
    assert called["clinic_id"] == clinic.id
    assert called["page"] == 1
    assert called["per_page"] == 50


def test_active_templates_non_admin_rejects_other_clinic(
    app,
    clinic,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.DOCTOR)

    response = app.test_client().get(
        "/api/v1/consultations/templates?clinic_id=999",
        headers=headers,
    )

    assert response.status_code == 403

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Access denied"


def test_active_templates_rejects_non_integer_clinic_id(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    response = app.test_client().get(
        "/api/v1/consultations/templates?clinic_id=abc",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "clinic_id must be an integer"


def test_active_templates_rejects_non_positive_clinic_id(
    app,
    auth_headers_for,
    user,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    response = app.test_client().get(
        "/api/v1/consultations/templates?clinic_id=0",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "clinic_id must be greater than 0"


def test_active_templates_forwards_pagination(
    app,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    pagination = make_pagination(
        page=2,
        per_page=20,
        total=25,
        pages=2,
        has_next=False,
        has_prev=True,
        prev_page=1,
    )

    called = {}

    def fake_get_active_templates(**kwargs):
        called.update(kwargs)
        return [], pagination

    monkeypatch.setattr(
        consultation_route,
        "get_active_templates",
        fake_get_active_templates,
    )

    response = app.test_client().get(
        "/api/v1/consultations/templates?page=2&per_page=20",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["data"] == []
    assert body["pagination"] == pagination
    assert user.clinic_id is not None
    assert called["clinic_id"] == user.clinic_id
    assert called["page"] == 2
    assert called["per_page"] == 20


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/consultations/my-patients?page=0",
        "/api/v1/consultations/my-patients?page=-1",
    ],
)
def test_my_patients_pagination_rejects_invalid_page(
    app,
    auth_headers_for,
    staff,
    path,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().get(
        path,
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "page must be greater than 0"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/consultations/my-patients?page=abc",
    ],
)
def test_my_patients_pagination_rejects_non_integer_page(
    app,
    auth_headers_for,
    staff,
    path,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().get(
        path,
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "page must be an integer"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/consultations/my-patients?per_page=0",
        "/api/v1/consultations/my-patients?per_page=-1",
    ],
)
def test_my_patients_pagination_rejects_invalid_per_page(
    app,
    auth_headers_for,
    staff,
    path,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().get(
        path,
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "per_page must be greater than 0"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/consultations/my-patients?per_page=501",
    ],
)
def test_my_patients_pagination_rejects_per_page_above_maximum(
    app,
    auth_headers_for,
    staff,
    path,
):
    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().get(
        path,
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "per_page must be less than or equal to 500"
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/consultations/1"),
        ("patch", "/api/v1/consultations/1"),
        ("post", "/api/v1/consultations/1/complete"),
        ("post", "/api/v1/consultations/1/cancel"),
        ("get", "/api/v1/consultations/patient/1"),
        ("get", "/api/v1/consultations/my-patients"),
        ("get", "/api/v1/consultations/staff/1"),
        ("get", "/api/v1/consultations/templates"),
    ],
)
def test_consultation_routes_require_authentication(
    app,
    method,
    path,
):
    client = app.test_client()

    response = getattr(client, method)(path)

    assert response.status_code in (401, 422)


@pytest.mark.parametrize(
    "role",
    [
        Role.PATIENT,
        Role.PHARMACIST,
        Role.LAB_TECHNICIAN,
    ],
)
def test_read_routes_forbidden_for_unauthorized_roles(
    app,
    auth_headers_for,
    make_user,
    clinic,
    role,
):
    unauthorized_user = make_user(
        clinic,
        role=role,
    )

    headers = auth_headers_for(
        unauthorized_user,
        role=role,
    )

    response = app.test_client().get(
        "/api/v1/consultations/1",
        headers=headers,
    )

    assert response.status_code == 403


def test_create_template_admin_without_clinic_is_denied(
    app,
    auth_headers_for,
    user,
    monkeypatch,
):
    user.clinic_id = None
    headers = auth_headers_for(user, role=Role.ADMIN)
    called = {"value": False}

    def fake_create_consultation_template(**kwargs):
        called["value"] = True
        return make_template(clinic_id=None)

    monkeypatch.setattr(
        consultation_route, "create_consultation_template",
        fake_create_consultation_template,
    )

    response = app.test_client().post(
        "/api/v1/consultations/templates",
        json={"name": "Unscoped", "structure": {"sections": ["history"]}},
        headers=headers,
    )

    assert response.status_code == 403
    assert called["value"] is False


def test_template_create_allowed_for_admin(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    template = make_template(
        clinic_id=clinic.id,
    )

    monkeypatch.setattr(
        consultation_route,
        "create_consultation_template",
        lambda **kwargs: template,
    )

    response = app.test_client().post(
        "/api/v1/consultations/templates",
        json={
            "name": "Admin Template",
            "structure": {
                "sections": ["history"]
            },
        },
        headers=headers,
    )

    assert response.status_code == 201


def test_route_returns_domain_error_status(
    app,
    clinic,
    auth_headers_for,
    user,
    monkeypatch,
):
    headers = auth_headers_for(user, role=Role.ADMIN)

    def fake_get_consultation(**kwargs):
        raise ValidationError("Consultation not found")

    monkeypatch.setattr(
        consultation_route,
        "get_consultation",
        fake_get_consultation,
    )

    response = app.test_client().get(
        "/api/v1/consultations/999",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Consultation not found"
