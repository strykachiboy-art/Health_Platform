"""CT4A2: true Flask route/tenant denials over committed CT1–CT3.

These tests use the canonical nursing and chat blueprints, real JWTs, actual
SQLite test transactions and the existing case-chat live access guard.
No production monkeypatches except audit isolation and explicit delivery outage
in the tests that must prove revocation *before* deferred synchronization.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.enums.chat_enums import (
    ConversationStatus, ConversationType, ParticipantRole, ParticipantStatus,
)
from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.models.conversation_participant_model import (
    ConversationParticipant,
)
from app.modules.chat.services import case_chat_assignment_event_service as events
from app.modules.chat.services import case_conversation_creation_service as creation
from app.modules.chat.services import case_chat_membership_sync_service as membership
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services import (
    care_team_assignment_service as assignment,
    handoff_primary_transfer_service as transfer,
    nursing_service,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission
from app.tests.modules.nursing.test_handoff_primary_transfer_preconditions import (
    _case,
)


@pytest.fixture(autouse=True)
def _audit_isolation(monkeypatch):
    # Match CT3's focused tests: audit correctness is independently covered;
    # this suite's target is real route denial and persistent ownership state.
    for module in (nursing_service, transfer, assignment, creation, membership):
        monkeypatch.setattr(module, "create_audit_log", lambda **_kw: None)


def _headers(auth_headers_for, staff, *, key=None):
    result = auth_headers_for(staff.user, role=staff.user.role)
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def _handoff_url(ids):
    return f"/api/v1/nursing/handoffs/{ids['handoff_id']}"


def _team_url(ids):
    return f"/api/v1/nursing/admissions/{ids['admission_id']}/care-team"


def _ack(client, ids, headers):
    return client.post(_handoff_url(ids) + "/acknowledge", headers=headers)


def _assignments(ids):
    return db.session.execute(
        db.select(AdmissionCareAssignment).where(
            AdmissionCareAssignment.clinic_id == ids["clinic_id"],
            AdmissionCareAssignment.admission_id == ids["admission_id"],
            AdmissionCareAssignment.assignment_kind == "primary_nurse",
        ).order_by(AdmissionCareAssignment.id)
    ).scalars().all()


def _state(ids):
    handoff = db.session.get(NursingHandoff, ids["handoff_id"])
    rows = _assignments(ids)
    requests = db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == ids["clinic_id"],
            AdmissionCaseChatSyncRequest.admission_id == ids["admission_id"],
        )
    ).scalars().all()
    return (
        handoff.status, handoff.version,
        tuple((r.id, r.staff_id, r.ended_at) for r in rows),
        tuple((r.id, r.request_sequence, r.processed_sequence) for r in requests),
    )


def _admin(clinic, make_staff):
    return make_staff(clinic, role=Role.ADMIN)


def _conversation(ids, admin):
    conv, link, _created = creation.ensure_admission_case_conversation(
        clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
        actor_user_id=admin.user_id,
    )
    assert link.conversation_id == conv.id
    return conv


def _defer_chat(monkeypatch):
    def unavailable(**_kw):
        raise RuntimeError("CT4A2 simulated post-commit chat outage")
    monkeypatch.setattr(events, "ensure_admission_case_conversation", unavailable)


def _unchanged(ids, before):
    assert _state(ids) == before


@pytest.mark.parametrize("endpoint,method", [
    ("/admissions/{admission_id}/care-team", "GET"),
    ("/admissions/{admission_id}/care-team/assignment-history", "GET"),
    ("/handoffs/{handoff_id}", "GET"),
    ("/handoffs/{handoff_id}/summary", "GET"),
    ("/handoffs/{handoff_id}/acknowledge", "POST"),
    ("/admissions/{admission_id}/care-team/assignments", "POST"),
])
def test_unauthed_care_team_and_handoff_routes_denied(
    client, clinic, make_patient, make_staff, endpoint, method,
):
    ids = _case(clinic, make_patient, make_staff)
    before = _state(ids)
    url = "/api/v1/nursing" + endpoint.format(**ids)
    response = client.open(url, method=method)
    assert response.status_code == 401
    _unchanged(ids, before)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.DOCTOR])
def test_non_nurse_cannot_acknowledge_ready_handoff(
    client, clinic, make_patient, make_staff, auth_headers_for, role,
):
    ids = _case(clinic, make_patient, make_staff)
    actor = make_staff(clinic, role=role)
    before = _state(ids)
    response = _ack(client, ids, _headers(auth_headers_for, actor, key="ct4a2-role"))
    assert response.status_code == 403
    _unchanged(ids, before)


def test_acknowledgement_requires_idempotency_header(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    incoming = db.session.get(Staff, ids["incoming_id"])
    before = _state(ids)
    assert _ack(client, ids, _headers(auth_headers_for, incoming)).status_code == 422
    _unchanged(ids, before)


def test_wrong_nurse_jwt_cannot_acknowledge_other_nurses_handoff(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    intruder = make_staff(clinic, role=Role.NURSE)
    before = _state(ids)
    response = _ack(client, ids, _headers(auth_headers_for, intruder, key="ct4a2-wrong-nurse"))
    assert response.status_code == 409
    _unchanged(ids, before)


@pytest.mark.parametrize("path", [
    "care-team", "care-team/assignment-history",
])
def test_foreign_tenant_cannot_read_admission_responsibility(
    client, clinic, make_clinic, make_patient, make_staff, auth_headers_for, path,
):
    ids = _case(clinic, make_patient, make_staff)
    foreign = make_clinic(name="CT4A2 foreign care-team tenant")
    actor = make_staff(foreign, role=Role.ADMIN)
    before = _state(ids)
    url = f"/api/v1/nursing/admissions/{ids['admission_id']}/{path}"
    response = client.get(url, headers=_headers(auth_headers_for, actor))
    assert response.status_code == 404
    assert "responsibility" not in (response.get_json() or {})
    _unchanged(ids, before)


@pytest.mark.parametrize("path", ["", "/summary"])
def test_foreign_tenant_cannot_read_nursing_handoff(
    client, clinic, make_clinic, make_patient, make_staff, auth_headers_for, path,
):
    ids = _case(clinic, make_patient, make_staff)
    foreign = make_clinic(name="CT4A2 foreign handoff tenant")
    actor = make_staff(foreign, role=Role.NURSE)
    before = _state(ids)
    response = client.get(
        _handoff_url(ids) + path, headers=_headers(auth_headers_for, actor),
    )
    assert response.status_code == 404
    _unchanged(ids, before)


def test_foreign_nurse_cannot_acknowledge_local_ready_handoff(
    client, clinic, make_clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    foreign = make_clinic(name="CT4A2 foreign ack tenant")
    actor = make_staff(foreign, role=Role.NURSE)
    before = _state(ids)
    response = _ack(client, ids, _headers(auth_headers_for, actor, key="ct4a2-foreign"))
    assert response.status_code == 404
    _unchanged(ids, before)


def test_foreign_admin_cannot_write_local_primary_assignment(
    client, clinic, make_clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    foreign = make_clinic(name="CT4A2 foreign assign tenant")
    actor = make_staff(foreign, role=Role.ADMIN)
    foreign_nurse = make_staff(foreign, role=Role.NURSE)
    before = _state(ids)
    response = client.post(
        _team_url(ids) + "/assignments",
        headers=_headers(auth_headers_for, actor),
        json={"assignment_kind": "primary_nurse", "staff_id": foreign_nurse.id,
              "assignment_reason": "Cross-tenant attempt"},
    )
    assert response.status_code == 404
    _unchanged(ids, before)


@pytest.mark.parametrize("role", [Role.DOCTOR, Role.NURSE])
def test_non_admin_cannot_end_current_primary_nurse_by_route(
    client, clinic, make_patient, make_staff, auth_headers_for, role,
):
    ids = _case(clinic, make_patient, make_staff)
    actor = make_staff(clinic, role=role)
    before = _state(ids)
    response = client.post(
        _team_url(ids) + "/end-assignment",
        headers=_headers(auth_headers_for, actor),
        json={"assignment_kind": "primary_nurse",
              "expected_current_assignment_id": ids["assignment_id"],
              "end_reason": "Unauthorized"},
    )
    assert response.status_code == 403
    _unchanged(ids, before)


def test_missing_active_primary_nurse_blocks_handoff_at_http_boundary(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    incoming = db.session.get(Staff, ids["incoming_id"])
    db.session.delete(db.session.get(AdmissionCareAssignment, ids["assignment_id"]))
    db.session.commit()
    before = _state(ids)
    result = _ack(client, ids, _headers(auth_headers_for, incoming, key="ct4a2-no-primary"))
    assert result.status_code == 409
    _unchanged(ids, before)


def test_discharged_admission_blocks_handoff_at_http_boundary(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    incoming = db.session.get(Staff, ids["incoming_id"])
    admission = db.session.get(Admission, ids["admission_id"])
    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    before = _state(ids)
    result = _ack(client, ids, _headers(auth_headers_for, incoming, key="ct4a2-discharge"))
    assert result.status_code == 409
    _unchanged(ids, before)


def test_suspended_incoming_staff_cannot_acknowledge_at_route(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    incoming = db.session.get(Staff, ids["incoming_id"])
    headers = _headers(auth_headers_for, incoming, key="ct4a2-suspended")
    incoming.status = StaffStatus.SUSPENDED
    db.session.commit()
    before = _state(ids)
    response = _ack(client, ids, headers)
    assert response.status_code in (403, 422)
    _unchanged(ids, before)


def test_ready_but_not_yet_acknowledged_nurse_cannot_read_case_chat(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    admin = _admin(clinic, make_staff)
    conv = _conversation(ids, admin)
    incoming = db.session.get(Staff, ids["incoming_id"])
    response = client.get(
        f"/api/v1/chat/conversations/{conv.id}",
        headers=_headers(auth_headers_for, incoming),
    )
    assert response.status_code == 404
    assert db.session.get(NursingHandoff, ids["handoff_id"]).status == NursingHandoffStatus.READY


def test_clinic_admin_is_not_implicitly_a_case_chat_participant(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    admin = _admin(clinic, make_staff)
    conv = _conversation(ids, admin)
    response = client.get(
        f"/api/v1/chat/conversations/{conv.id}",
        headers=_headers(auth_headers_for, admin),
    )
    assert response.status_code == 404


def test_foreign_user_cannot_read_local_case_conversation(
    client, clinic, make_clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    admin = _admin(clinic, make_staff)
    conv = _conversation(ids, admin)
    foreign = make_clinic(name="CT4A2 foreign case chat")
    actor = make_staff(foreign, role=Role.NURSE)
    response = client.get(
        f"/api/v1/chat/conversations/{conv.id}",
        headers=_headers(auth_headers_for, actor),
    )
    assert response.status_code == 404


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_removed_primary_nurse_cannot_access_chat_while_sync_is_pending(
    client, clinic, make_patient, make_staff, auth_headers_for,
    monkeypatch, method,
):
    ids = _case(clinic, make_patient, make_staff)
    admin = _admin(clinic, make_staff)
    conv = _conversation(ids, admin)
    outgoing = db.session.get(Staff, ids["outgoing_id"])
    incoming = db.session.get(Staff, ids["incoming_id"])
    headers_old = _headers(auth_headers_for, outgoing)
    headers_new = _headers(auth_headers_for, incoming, key="ct4a2-ack-sync-outage")
    assert client.get(
        f"/api/v1/chat/conversations/{conv.id}", headers=headers_old,
    ).status_code == 200
    _defer_chat(monkeypatch)
    assert _ack(client, ids, headers_new).status_code == 200
    queue = db.session.query(AdmissionCaseChatSyncRequest).one()
    assert queue.request_sequence > queue.processed_sequence
    if method == "GET":
        denied = client.get(
            f"/api/v1/chat/conversations/{conv.id}/messages", headers=headers_old,
        )
    else:
        denied = client.post(
            f"/api/v1/chat/conversations/{conv.id}/messages",
            headers=headers_old, json={"content": "Unauthorized after handoff"},
        )
    assert denied.status_code == 404
    assert len(_assignments(ids)) == 2
    assert _assignments(ids)[1].staff_id == ids["incoming_id"]


def test_stale_manually_accepted_staff_still_denied_at_chat_http_guard(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    admin = _admin(clinic, make_staff)
    conv = _conversation(ids, admin)
    outsider = make_staff(clinic, role=Role.NURSE)
    db.session.add(ConversationParticipant(
        clinic_id=ids["clinic_id"], conversation_id=conv.id,
        user_id=outsider.user_id, role=ParticipantRole.MEMBER,
        status=ParticipantStatus.ACCEPTED, joined_at=datetime.now(timezone.utc),
    ))
    db.session.commit()
    response = client.get(
        f"/api/v1/chat/conversations/{conv.id}",
        headers=_headers(auth_headers_for, outsider),
    )
    assert response.status_code == 404


def test_stale_clinician_chat_denied_on_discharge_without_sync(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    admin = _admin(clinic, make_staff)
    conv = _conversation(ids, admin)
    outgoing = db.session.get(Staff, ids["outgoing_id"])
    headers = _headers(auth_headers_for, outgoing)
    assert client.get(f"/api/v1/chat/conversations/{conv.id}", headers=headers).status_code == 200
    admission = db.session.get(Admission, ids["admission_id"])
    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    assert client.get(f"/api/v1/chat/conversations/{conv.id}", headers=headers).status_code == 404


def test_acknowledgement_route_replay_preserves_one_assignment_transfer(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    incoming = db.session.get(Staff, ids["incoming_id"])
    headers = _headers(auth_headers_for, incoming, key="ct4a2-ack-replay")
    first = _ack(client, ids, headers)
    assert first.status_code == 200
    second = _ack(client, ids, headers)
    assert second.status_code == 200
    assert len(_assignments(ids)) == 2
    assert _assignments(ids)[0].ended_at is not None
    assert _assignments(ids)[1].staff_id == ids["incoming_id"]
    request = db.session.query(AdmissionCaseChatSyncRequest).one()
    assert request.request_sequence == 1


def test_route_rejects_unexpected_handoff_id_without_write(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    ids = _case(clinic, make_patient, make_staff)
    incoming = db.session.get(Staff, ids["incoming_id"])
    before = _state(ids)
    response = client.post(
        f"/api/v1/nursing/handoffs/{ids['handoff_id'] + 100000}/acknowledge",
        headers=_headers(auth_headers_for, incoming, key="ct4a2-missing"),
    )
    assert response.status_code == 404
    _unchanged(ids, before)
