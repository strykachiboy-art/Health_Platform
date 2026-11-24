"""CT4D1: HTTP clinical care-team -> handoff -> durable chat E2E.

Real canonical Flask routes + PostgreSQL 18 and Redis 8; no mock audit.
Only after-commit chat delivery is failed intentionally, proving durable retry.
No main repo or dev database or production service modifications.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app import create_app
from app.config import config_by_name
from app.core.audit.models.audit_model import AuditLog
from app.core.auth.user.models.user_model import User
from app.core.enums.chat_enums import ParticipantStatus
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import ConversationParticipant
from app.modules.chat.services import case_chat_assignment_event_service as events
from app.modules.chat.services import case_conversation_creation_service as creation
from app.modules.chat.tasks.case_chat_sync_task import process_pending_case_chat_sync
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward

NURSING = "/api/v1/nursing"
CHAT = "/api/v1/chat"


@pytest.fixture()
def ct4d1_app():
    url = os.environ.get("CT4D1_TEST_DATABASE_URL", "")
    parsed = make_url(url)
    assert parsed.drivername == "postgresql+psycopg2", "CT4D1_POSTGRES_DRIVER_REQUIRED"
    assert parsed.database == "ct4d1_disposable", "CT4D1_DISPOSABLE_DB_REQUIRED"
    assert parsed.username == "ct4d1" and parsed.host == "127.0.0.1"
    assert str(parsed.port) == os.environ.get("CT4D1_PG_PORT")
    redis_url = os.environ.get("TEST_REDIS_URL", "")
    assert redis_url.startswith("redis://127.0.0.1:") and redis_url.endswith("/15")
    old = config_by_name["testing"].SQLALCHEMY_DATABASE_URI
    config_by_name["testing"].SQLALCHEMY_DATABASE_URI = url
    app = None
    try:
        app = create_app("testing")
        with app.app_context():
            from app import models_registry  # noqa: F401
            assert db.engine.dialect.name == "postgresql"
            assert db.engine.url.database == "ct4d1_disposable"
            db.create_all()  # Disposable PG only; schema construction is NOT a migration
            try:
                yield app
            finally:
                db.session.rollback()
                db.session.remove()
                db.drop_all()
                db.engine.dispose()
    finally:
        config_by_name["testing"].SQLALCHEMY_DATABASE_URI = old


def _now():
    return datetime.now(timezone.utc)


def _seed():
    clinic = Clinic(name="CT4D1 Admission Clinic", status=ClinicStatus.ACTIVE, ai_credits=5)
    db.session.add(clinic)
    db.session.flush()
    people = {}
    for label, role in (
        ("admin", Role.ADMIN),
        ("doctor", Role.DOCTOR),
        ("outgoing", Role.NURSE),
        ("incoming", Role.NURSE),
    ):
        user = User(clinic_id=clinic.id, role=role, is_active=True,
                    email=f"ct4d1-{label}@test.invalid")
        db.session.add(user)
        db.session.flush()
        staff = Staff(clinic_id=clinic.id, user_id=user.id, first_name="CT4D1",
                      last_name=label, status=StaffStatus.ACTIVE)
        db.session.add(staff)
        db.session.flush()
        people[label] = {"user_id": user.id, "staff_id": staff.id}
    patient = Patient(clinic_id=clinic.id, first_name="Test", last_name="Patient",
                      patient_number="CT4D1-PATIENT", is_active=True)
    db.session.add(patient)
    db.session.flush()
    ward = Ward(clinic_id=clinic.id, name="CT4D1 Ward", ward_type=WardType.GENERAL, capacity=2)
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number="CT4D1-01", status=BedStatus.OCCUPIED)
    db.session.add(bed)
    db.session.flush()
    admission = Admission(patient_id=patient.id, bed_id=bed.id,
                          admitted_by_id=people["doctor"]["staff_id"],
                          status=AdmissionStatus.ADMITTED,
                          admitted_at=_now().replace(tzinfo=None))
    db.session.add(admission)
    db.session.commit()
    return {"clinic": clinic.id, "admission": admission.id,
            "patient": patient.id, **people}


def _headers(ids, role, *, key=None):
    user_id = ids[role]["user_id"]
    user = db.session.get(User, user_id)
    claims = {"role": user.role.value, "token_version": user.token_version}
    token = create_access_token(identity=str(user_id), additional_claims=claims)
    result = {"Authorization": "Bearer " + token}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def _get_json(response, expected, label):
    assert response.status_code == expected, (label, response.status_code,
                                               (response.get_json() or {}).get("error"))
    return response.get_json()


def _assign(client, ids, role, kind):
    result = _get_json(client.post(
        f"{NURSING}/admissions/{ids['admission']}/care-team/assignments",
        headers=_headers(ids, "admin"),
        json={"staff_id": ids[role]["staff_id"], "assignment_kind": kind,
              "assignment_reason": "CT4D1 full route workflow"},
    ), 201, "assign_" + role)
    return result["assignment"]["assignment_id"]


def _prepare_ready(client, ids):
    # All four transitions here use the real HTTP route and real audit writes.
    doctor_id = _assign(client, ids, "doctor", "primary_doctor")
    outgoing_id = _assign(client, ids, "outgoing", "primary_nurse")
    team = _get_json(client.get(
        f"{NURSING}/admissions/{ids['admission']}/care-team",
        headers=_headers(ids, "outgoing"),
    ), 200, "care_team_before")
    assert team["responsibility"]["confirmed"] is True
    assert team["responsibility"]["primary_doctor"]["assignment_id"] == doctor_id
    assert team["responsibility"]["primary_nurse"]["assignment_id"] == outgoing_id
    link = db.session.execute(select(AdmissionCaseConversation).where(
        AdmissionCaseConversation.admission_id == ids["admission"],
    )).scalar_one()
    chat_id = link.conversation_id
    assert client.get(f"{CHAT}/conversations/{chat_id}",
                      headers=_headers(ids, "outgoing")).status_code == 200
    assert client.get(f"{CHAT}/conversations/{chat_id}",
                      headers=_headers(ids, "incoming")).status_code == 404
    now = _now()
    created = _get_json(client.post(
        f"{NURSING}/admissions/{ids['admission']}/handoffs",
        headers=_headers(ids, "outgoing"),
        json={"incoming_staff_id": ids["incoming"]["staff_id"],
              "shift_start_at": now.isoformat(),
              "shift_end_at": (now + timedelta(hours=8)).isoformat(),
              "situation": "Stable", "assessment": "Handoff reviewed",
              "recommendation": "Continue nurse monitoring"},
    ), 201, "draft_handoff")
    handoff_id = created["handoff"]["id"]
    assert created["handoff"]["status"] == NursingHandoffStatus.DRAFT.value
    ready = _get_json(client.post(
        f"{NURSING}/handoffs/{handoff_id}/ready",
        headers=_headers(ids, "outgoing"),
    ), 200, "ready_handoff")
    assert ready["handoff"]["status"] == NursingHandoffStatus.READY.value
    assert client.get(f"{CHAT}/conversations/{chat_id}",
                      headers=_headers(ids, "incoming")).status_code == 404
    print("CT4D1_HTTP_ASSIGN_READ_DRAFT_READY=PASS", flush=True)
    return {"handoff": handoff_id, "chat": chat_id, "outgoing_assignment": outgoing_id,
            "doctor_assignment": doctor_id}


def _queue(ids):
    return db.session.execute(select(AdmissionCaseChatSyncRequest).where(
        AdmissionCaseChatSyncRequest.clinic_id == ids["clinic"],
        AdmissionCaseChatSyncRequest.admission_id == ids["admission"],
    )).scalar_one()


def _ack_deferred(client, ids, details, monkeypatch, *, key):
    original = events.ensure_admission_case_conversation
    def unavailable(**_kw):
        raise RuntimeError("CT4D1 simulated after-commit chat transport failure")
    monkeypatch.setattr(events, "ensure_admission_case_conversation", unavailable)
    before = _queue(ids)
    previous_sequence, previous_processed = before.request_sequence, before.processed_sequence
    reply = _get_json(client.post(
        f"{NURSING}/handoffs/{details['handoff']}/acknowledge",
        headers=_headers(ids, "incoming", key=key),
    ), 200, "ack_handoff")
    assert reply["handoff"]["status"] == NursingHandoffStatus.ACKNOWLEDGED.value
    db.session.rollback()
    updated = _queue(ids)
    assert updated.request_sequence == previous_sequence + 1
    assert updated.processed_sequence == previous_processed
    assert updated.authorization_handoff_id == details["handoff"]
    assert updated.requested_by_user_id == ids["incoming"]["user_id"]
    rows = db.session.execute(select(AdmissionCareAssignment).where(
        AdmissionCareAssignment.clinic_id == ids["clinic"],
        AdmissionCareAssignment.admission_id == ids["admission"],
        AdmissionCareAssignment.assignment_kind == "primary_nurse",
    ).order_by(AdmissionCareAssignment.id)).scalars().all()
    assert len(rows) == 2
    assert rows[0].staff_id == ids["outgoing"]["staff_id"]
    assert rows[0].ended_at is not None and rows[1].ended_at is None
    assert rows[1].staff_id == ids["incoming"]["staff_id"]
    assert rows[0].ended_at == rows[1].assigned_at
    handoff = db.session.get(NursingHandoff, details["handoff"])
    assert handoff.status == NursingHandoffStatus.ACKNOWLEDGED
    assert handoff.acknowledged_by_staff_id == ids["incoming"]["staff_id"]
    assert client.get(f"{CHAT}/conversations/{details['chat']}",
                      headers=_headers(ids, "outgoing")).status_code == 404
    assert client.get(f"{CHAT}/conversations/{details['chat']}",
                      headers=_headers(ids, "incoming")).status_code == 404
    assert client.get(f"{CHAT}/conversations/{details['chat']}/messages",
                      headers=_headers(ids, "outgoing")).status_code == 404
    print("CT4D1_ACK_ATOMIC_PRIMARY_TRANSFER_AND_PENDING_QUEUE=PASS", flush=True)
    print("CT4D1_STALE_CHAT_HTTP_ACCESS_DENIED_BEFORE_RETRY=PASS", flush=True)
    return original


def _assert_synced(client, ids, details):
    db.session.rollback()
    row = _queue(ids)
    assert row.request_sequence == row.processed_sequence
    links = db.session.execute(select(AdmissionCaseConversation).where(
        AdmissionCaseConversation.clinic_id == ids["clinic"],
        AdmissionCaseConversation.admission_id == ids["admission"],
    )).scalars().all()
    assert len(links) == 1 and links[0].conversation_id == details["chat"]
    assert db.session.scalar(select(func.count()).select_from(Conversation)) == 1
    members = db.session.execute(select(ConversationParticipant).where(
        ConversationParticipant.conversation_id == details["chat"],
    )).scalars().all()
    assert len(members) == len({m.user_id for m in members})
    active = {m.user_id for m in members if m.status == ParticipantStatus.ACCEPTED}
    assert active == {ids["doctor"]["user_id"], ids["incoming"]["user_id"]}
    assert client.get(f"{CHAT}/conversations/{details['chat']}",
                      headers=_headers(ids, "incoming")).status_code == 200
    assert client.get(f"{CHAT}/conversations/{details['chat']}",
                      headers=_headers(ids, "doctor")).status_code == 200
    assert client.get(f"{CHAT}/conversations/{details['chat']}",
                      headers=_headers(ids, "outgoing")).status_code == 404
    team = _get_json(client.get(
        f"{NURSING}/admissions/{ids['admission']}/care-team",
        headers=_headers(ids, "incoming"),
    ), 200, "care_team_after")
    assert team["responsibility"]["primary_nurse_staff_id"] == ids["incoming"]["staff_id"]
    assert team["responsibility"]["primary_doctor_staff_id"] == ids["doctor"]["staff_id"]
    audits = db.session.execute(select(AuditLog).where(
        AuditLog.clinic_id == ids["clinic"],
    )).scalars().all()
    assert len(audits) >= 10
    assert all(len(a.evidence_sha256) == 64 for a in audits)
    print("CT4D1_HTTP_TEAM_CHAT_ACL_CONVERGED_AND_AUDITED=PASS", flush=True)


def _setup(client, monkeypatch):
    ids = _seed()
    details = _prepare_ready(client, ids)
    original = _ack_deferred(client, ids, details, monkeypatch, key="ct4d1-handoff-once")
    return ids, details, original


def test_full_http_handoff_delivery_recovery_and_clinical_chat_acl(ct4d1_app, monkeypatch):
    client = ct4d1_app.test_client()
    ids, details, original = _setup(client, monkeypatch)
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    result = process_pending_case_chat_sync.run(limit=10)
    assert result == {"claimed": 1, "processed": 1, "failed": 0, "still_pending": 0}
    _assert_synced(client, ids, details)
    assert process_pending_case_chat_sync.run(limit=10) == {
        "claimed": 0, "processed": 0, "failed": 0, "still_pending": 0,
    }
    print("CT4D1_FULL_HTTP_HANDOFF_TO_CHAT_RECOVERY=PASS", flush=True)


def test_same_key_http_ack_replay_does_not_duplicate_transfer_or_audit(ct4d1_app, monkeypatch):
    client = ct4d1_app.test_client()
    ids, details, original = _setup(client, monkeypatch)
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    assert process_pending_case_chat_sync.run(limit=10)["processed"] == 1
    db.session.rollback()
    before = (_queue(ids).request_sequence, _queue(ids).processed_sequence)
    before_audits = db.session.scalar(select(func.count()).select_from(AuditLog))
    replay = _get_json(client.post(
        f"{NURSING}/handoffs/{details['handoff']}/acknowledge",
        headers=_headers(ids, "incoming", key="ct4d1-handoff-once"),
    ), 200, "ack_replay")
    assert replay["handoff"]["id"] == details["handoff"]
    db.session.rollback()
    assert (_queue(ids).request_sequence, _queue(ids).processed_sequence) == before
    assert db.session.scalar(select(func.count()).select_from(AuditLog)) == before_audits
    _assert_synced(client, ids, details)
    print("CT4D1_IDEMPOTENT_HTTP_REPLAY_NO_DUPLICATE=PASS", flush=True)


def test_cross_tenant_http_care_team_handoff_and_chat_isolation(ct4d1_app, monkeypatch):
    client = ct4d1_app.test_client()
    ids, details, original = _setup(client, monkeypatch)
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    assert process_pending_case_chat_sync.run(limit=10)["processed"] == 1
    foreign = Clinic(name="CT4D1 Foreign Clinic", status=ClinicStatus.ACTIVE, ai_credits=5)
    db.session.add(foreign)
    db.session.flush()
    foreign_user = User(clinic_id=foreign.id, role=Role.NURSE, is_active=True,
                        email="ct4d1-foreign@test.invalid")
    db.session.add(foreign_user)
    db.session.flush()
    db.session.add(Staff(clinic_id=foreign.id, user_id=foreign_user.id,
                         first_name="Foreign", last_name="Nurse", status=StaffStatus.ACTIVE))
    db.session.commit()
    ids["foreign"] = {"user_id": foreign_user.id}
    headers = _headers(ids, "foreign")
    assert client.get(f"{NURSING}/admissions/{ids['admission']}/care-team",
                      headers=headers).status_code == 404
    assert client.get(f"{NURSING}/handoffs/{details['handoff']}",
                      headers=headers).status_code == 404
    assert client.get(f"{CHAT}/conversations/{details['chat']}",
                      headers=headers).status_code == 404
    assert client.post(f"{NURSING}/handoffs/{details['handoff']}/acknowledge",
                       headers={**headers, "Idempotency-Key": "foreign-ack"}).status_code == 404
    _assert_synced(client, ids, details)
    print("CT4D1_CROSS_TENANT_HTTP_RESOURCES_DENIED=PASS", flush=True)


def test_scheduled_retry_failure_then_recovery_preserves_one_case(ct4d1_app, monkeypatch):
    client = ct4d1_app.test_client()
    ids, details, original = _setup(client, monkeypatch)
    # The transport remains failed for the first scheduled retry as well.
    fail = process_pending_case_chat_sync.run(limit=10)
    assert fail == {"claimed": 1, "processed": 0, "failed": 1, "still_pending": 0}
    db.session.rollback()
    assert _queue(ids).request_sequence > _queue(ids).processed_sequence
    assert db.session.scalar(select(func.count()).select_from(AdmissionCaseConversation)) == 1
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    success = process_pending_case_chat_sync.run(limit=10)
    assert success == {"claimed": 1, "processed": 1, "failed": 0, "still_pending": 0}
    _assert_synced(client, ids, details)
    print("CT4D1_FAILED_RETRY_THEN_RECOVERY_ONE_CASE=PASS", flush=True)
