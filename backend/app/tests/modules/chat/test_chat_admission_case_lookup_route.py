"""CHAT-E: admission-bound chat discovery is read-only and currently authorized.

Uses the existing Flask testing database and CT2 case-chat models/services.
No production database, queue, background worker, or new chat tables.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask_jwt_extended import create_access_token

from app.core.auth.user.models.user_model import User
from app.core.enums.chat_enums import ConversationType
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.extensions import db
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.services import case_conversation_creation_service as creation
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.ward.models.ward_model import Admission, Bed, Ward


def _headers(app, user_id):
    user = db.session.get(User, user_id)
    with app.app_context():
        token = create_access_token(
            identity=str(user.id),
            additional_claims={
                "role": user.role.value,
                "token_version": user.token_version,
            },
        )
    return {"Authorization": f"Bearer {token}"}


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    nurse = make_staff(clinic, role=Role.NURSE)
    ward = Ward(
        clinic_id=clinic.id, name=f"CHAT-E ward {patient.id}",
        ward_type=WardType.GENERAL, capacity=3,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(
        ward_id=ward.id, bed_number=f"CHAT-E-{patient.id}",
        status=BedStatus.OCCUPIED,
    )
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id, bed_id=bed.id,
        admitted_by_id=doctor.id, status=AdmissionStatus.ADMITTED,
        admitted_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.session.add(admission)
    db.session.flush()
    ids = {
        "clinic": clinic.id,
        "patient": patient.id,
        "admission": admission.id,
        "admin": admin.user_id,
        "doctor_staff": doctor.id,
        "doctor": doctor.user_id,
        "nurse_staff": nurse.id,
        "nurse": nurse.user_id,
    }
    db.session.commit()
    return ids


def _assign(ids, kind, staff_id):
    db.session.add(AdmissionCareAssignment(
        clinic_id=ids["clinic"], patient_id=ids["patient"],
        admission_id=ids["admission"], staff_id=staff_id,
        assignment_kind=kind, assigned_by_user_id=ids["admin"],
        assignment_reason="CHAT-E assigned clinical duty",
    ))
    db.session.commit()


def _setup(clinic, make_patient, make_staff, monkeypatch):
    monkeypatch.setattr(creation, "create_audit_log", lambda **_kw: None)
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor_staff"])
    _assign(ids, "primary_nurse", ids["nurse_staff"])
    conversation, link, created = creation.ensure_admission_case_conversation(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin"],
    )
    assert created is True
    assert link.conversation_id == conversation.id
    return ids, conversation.id


def _url(admission_id):
    return f"/api/v1/chat/admissions/{admission_id}/conversation"


def test_lookup_rejects_unauthenticated(client):
    assert client.get(_url(1)).status_code == 401


def test_lookup_without_link_is_not_found(
    client, app, clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    response = client.get(_url(ids["admission"]), headers=_headers(app, ids["nurse"]))
    assert response.status_code == 404


def test_lookup_returns_existing_link_only_for_assigned_nurse(
    client, app, clinic, make_patient, make_staff, monkeypatch,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff, monkeypatch)
    before = db.session.query(AdmissionCaseConversation).count()
    response = client.get(_url(ids["admission"]), headers=_headers(app, ids["nurse"]))
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    result = data["data"]
    assert result["clinic_id"] == ids["clinic"]
    assert result["patient_id"] == ids["patient"]
    assert result["admission_id"] == ids["admission"]
    assert result["conversation_id"] == conversation_id
    assert result["conversation"]["id"] == conversation_id
    assert result["conversation"]["patient_id"] == ids["patient"]
    assert result["conversation"]["conversation_type"] == ConversationType.PATIENT.value
    assert db.session.query(AdmissionCaseConversation).count() == before


def test_lookup_denies_admin_who_is_not_case_participant(
    client, app, clinic, make_patient, make_staff, monkeypatch,
):
    ids, _ = _setup(clinic, make_patient, make_staff, monkeypatch)
    response = client.get(_url(ids["admission"]), headers=_headers(app, ids["admin"]))
    assert response.status_code == 404


def test_lookup_denies_wrong_clinic(
    client, app, clinic, make_patient, make_staff, make_clinic, monkeypatch,
):
    ids, _ = _setup(clinic, make_patient, make_staff, monkeypatch)
    other_clinic = make_clinic()
    stranger = make_staff(other_clinic, role=Role.NURSE)
    response = client.get(_url(ids["admission"]), headers=_headers(app, stranger.user_id))
    assert response.status_code == 404


def test_lookup_denies_outgoing_nurse_before_membership_reconciliation(
    client, app, clinic, make_patient, make_staff, monkeypatch,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff, monkeypatch)
    assert client.get(_url(ids["admission"]), headers=_headers(app, ids["nurse"])).status_code == 200
    assignment = db.session.execute(db.select(AdmissionCareAssignment).where(
        AdmissionCareAssignment.clinic_id == ids["clinic"],
        AdmissionCareAssignment.admission_id == ids["admission"],
        AdmissionCareAssignment.assignment_kind == "primary_nurse",
        AdmissionCareAssignment.ended_at.is_(None),
    )).scalar_one()
    assignment.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
    assignment.ended_by_user_id = ids["admin"]
    assignment.end_reason = "Clinical assignment ended"
    db.session.commit()
    response = client.get(_url(ids["admission"]), headers=_headers(app, ids["nurse"]))
    assert response.status_code == 404
    assert db.session.query(AdmissionCaseConversation).filter_by(conversation_id=conversation_id).count() == 1


def test_lookup_denies_discharged_case(
    client, app, clinic, make_patient, make_staff, monkeypatch,
):
    ids, _ = _setup(clinic, make_patient, make_staff, monkeypatch)
    row = db.session.get(Admission, ids["admission"])
    row.status = AdmissionStatus.DISCHARGED
    db.session.commit()
    response = client.get(_url(ids["admission"]), headers=_headers(app, ids["doctor"]))
    assert response.status_code == 404
