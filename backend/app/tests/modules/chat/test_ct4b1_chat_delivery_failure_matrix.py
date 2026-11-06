"""CT4B1: simulated chat-transport failures with durable clinical safety.

Test-only fault injection. No production edits, DB migrations, or real
container/network interruption. Redis 8 is probed by the package runner.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from app.core.auth.user.models.user_model import User
from app.core.enums.chat_enums import ParticipantStatus
from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import NotFoundError
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.models.conversation_participant_model import (
    ConversationParticipant,
)
from app.modules.chat.services import case_chat_assignment_event_service as events
from app.modules.chat.services import case_conversation_creation_service as creation
from app.modules.chat.services import case_chat_membership_sync_service as sync
from app.modules.chat.services.chat_security_service import ChatSecurityService
from app.modules.chat.tasks import case_chat_sync_task as worker
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services import care_team_assignment_service as assignment
from app.modules.nursing.services import nursing_service
from app.modules.nursing.services import handoff_primary_transfer_service as transfer
from app.modules.staff.models.staff_model import Staff
from app.tests.modules.chat.test_case_chat_assignment_event_wiring import (
    _case as _admin_case,
    _assign as _assign_admin,
)
from app.tests.modules.nursing.test_handoff_primary_transfer_preconditions import (
    _case as _nurse_case,
)


@pytest.fixture(autouse=True)
def _quiet_audit_for_failure_matrix(monkeypatch):
    # Audit persistence has separate coverage; individual rollback cases below
    # explicitly restore the failure at the relevant clinical call boundary.
    for module in (assignment, creation, sync, nursing_service, transfer):
        monkeypatch.setattr(module, "create_audit_log", lambda **_kw: None)


def _queue(clinic_id: int, admission_id: int) -> AdmissionCaseChatSyncRequest:
    return db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == clinic_id,
            AdmissionCaseChatSyncRequest.admission_id == admission_id,
        )
    ).scalar_one()


def _admin_request(ids) -> AdmissionCaseChatSyncRequest:
    return _queue(ids["clinic"], ids["admission"])


def _queue_nurse(ids) -> AdmissionCaseChatSyncRequest:
    return _queue(ids["clinic_id"], ids["admission_id"])


def _active_assignment_count(ids):
    return db.session.scalar(
        db.select(db.func.count()).select_from(AdmissionCareAssignment).where(
            AdmissionCareAssignment.clinic_id == ids["clinic"],
            AdmissionCareAssignment.admission_id == ids["admission"],
            AdmissionCareAssignment.ended_at.is_(None),
        )
    )


def _fail(failure_type, marker="CT4B1 SIMULATED SERVICE UNAVAILABLE"):
    def unavailable(**_kwargs):
        raise failure_type(marker)
    return unavailable


def _defer_admin_creation(monkeypatch, ids):
    original = events.ensure_admission_case_conversation
    monkeypatch.setattr(events, "ensure_admission_case_conversation", _fail(ConnectionError))
    assigned = _assign_admin(ids)
    assert assigned.id > 0
    assert _admin_request(ids).request_sequence == 1
    assert _admin_request(ids).processed_sequence == 0
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    return assigned


def _deliver_admin(ids):
    return events.deliver_pending_case_chat_sync(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin"],
    )


def _member_statuses(conversation_id):
    return {
        row.user_id: row.status
        for row in db.session.execute(
            db.select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,
            )
        ).scalars().all()
    }


@pytest.mark.parametrize("fault", [
    TimeoutError, ConnectionError, OSError, RuntimeError, ValueError,
], ids=["timeout", "connection", "socket", "http_5xx", "payload_error"])
def test_after_commit_creation_outage_keeps_clinical_write_pending_and_recovers(
    clinic, make_patient, make_staff, monkeypatch, fault,
):
    ids = _admin_case(clinic, make_patient, make_staff)
    original = events.ensure_admission_case_conversation
    monkeypatch.setattr(events, "ensure_admission_case_conversation", _fail(fault))
    assigned = _assign_admin(ids)
    assert assigned.id > 0
    assert _active_assignment_count(ids) == 1
    assert _admin_request(ids).request_sequence == 1
    assert _admin_request(ids).processed_sequence == 0
    assert db.session.query(AdmissionCaseConversation).count() == 0
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    outcome = _deliver_admin(ids)
    assert outcome["pending"] is False
    assert outcome["acknowledged_sequence"] == 1
    assert _admin_request(ids).processed_sequence == 1
    assert db.session.query(AdmissionCaseConversation).count() == 1
    assert _active_assignment_count(ids) == 1
    assert _deliver_admin(ids) == {"pending": False, "changed": False}


@pytest.mark.parametrize("fault", [
    TimeoutError, ConnectionError, RuntimeError, ValueError,
], ids=["timeout", "connection", "http_5xx", "integrity_service"])
def test_reconciliation_outage_revokes_old_doctor_live_then_retries(
    clinic, make_patient, make_staff, monkeypatch, fault,
):
    ids = _admin_case(clinic, make_patient, make_staff)
    previous = _assign_admin(ids)
    original_link = db.session.query(AdmissionCaseConversation).one()
    old_id = ids["doctor_user"]
    new_doctor = make_staff(clinic, role=Role.DOCTOR)
    new_id = new_doctor.user_id
    original_sync = events.reconcile_admission_case_chat_membership
    monkeypatch.setattr(events, "reconcile_admission_case_chat_membership", _fail(fault))
    replacement = _assign_admin(
        ids, staff_id=new_doctor.id, expected_current_assignment_id=previous.id,
    )
    assert replacement.id != previous.id
    assert _admin_request(ids).request_sequence == 2
    assert _admin_request(ids).processed_sequence == 1
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(
            old_id, original_link.conversation_id,
        )
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(
            new_id, original_link.conversation_id,
        )
    monkeypatch.setattr(events, "reconcile_admission_case_chat_membership", original_sync)
    result = _deliver_admin(ids)
    assert result["pending"] is False
    assert _admin_request(ids).processed_sequence == 2
    statuses = _member_statuses(original_link.conversation_id)
    assert statuses[old_id] == ParticipantStatus.REMOVED
    assert statuses[new_id] == ParticipantStatus.ACCEPTED
    assert db.session.query(AdmissionCaseConversation).count() == 1


@pytest.mark.parametrize("fault", [TimeoutError, ConnectionError, RuntimeError],
                         ids=["timeout", "connection", "intermittent_5xx"])
def test_scheduled_worker_failure_keeps_generation_until_recovered(
    clinic, make_patient, make_staff, monkeypatch, fault,
):
    ids = _admin_case(clinic, make_patient, make_staff)
    _defer_admin_creation(monkeypatch, ids)
    original_delivery = worker.deliver_pending_case_chat_sync
    monkeypatch.setattr(worker, "deliver_pending_case_chat_sync", _fail(fault))
    for _ in range(2):
        outcome = worker.process_pending_case_chat_sync.run(limit=1)
        assert outcome == {"claimed": 1, "processed": 0, "failed": 1, "still_pending": 0}
        assert _admin_request(ids).processed_sequence == 0
    assert _active_assignment_count(ids) == 1
    monkeypatch.setattr(worker, "deliver_pending_case_chat_sync", original_delivery)
    outcome = worker.process_pending_case_chat_sync.run(limit=1)
    assert outcome == {"claimed": 1, "processed": 1, "failed": 0, "still_pending": 0}
    assert _admin_request(ids).processed_sequence == 1
    assert db.session.query(AdmissionCaseConversation).count() == 1
    assert worker.process_pending_case_chat_sync.run(limit=1)["claimed"] == 0


@pytest.mark.parametrize("fault_point", ["clinical_audit", "queue_write"])
def test_precommit_failure_rolls_back_both_clinical_and_queue(
    clinic, make_patient, make_staff, monkeypatch, fault_point,
):
    ids = _admin_case(clinic, make_patient, make_staff)
    if fault_point == "clinical_audit":
        monkeypatch.setattr(assignment, "create_audit_log", _fail(RuntimeError))
    else:
        monkeypatch.setattr(events, "queue_case_chat_sync_in_clinical_transaction", _fail(RuntimeError))
    with pytest.raises(RuntimeError, match="CT4B1 SIMULATED"):
        _assign_admin(ids)
    assert db.session.query(AdmissionCareAssignment).count() == 0
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 0
    assert db.session.query(AdmissionCaseConversation).count() == 0


@pytest.mark.parametrize("revocation", ["inactive_admin", "admin_role_revoked"])
def test_revoked_queued_actor_cannot_be_replaced_by_worker(
    clinic, make_patient, make_staff, monkeypatch, revocation,
):
    ids = _admin_case(clinic, make_patient, make_staff)
    _defer_admin_creation(monkeypatch, ids)
    actor = db.session.get(User, ids["admin"])
    if revocation == "inactive_admin":
        actor.is_active = False
    else:
        actor.role = Role.DOCTOR
    db.session.commit()
    outcome = worker.process_pending_case_chat_sync.run(limit=1)
    assert outcome["claimed"] == 1
    assert outcome["failed"] == 1
    assert outcome["processed"] == 0
    assert _admin_request(ids).processed_sequence == 0
    assert db.session.query(AdmissionCaseConversation).count() == 0
    assert _active_assignment_count(ids) == 1


def test_delayed_old_generation_cannot_acknowledge_a_newer_event(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _admin_case(clinic, make_patient, make_staff)
    _defer_admin_creation(monkeypatch, ids)
    original_ack = events._acknowledge_request_if_current
    observed = []

    def newer_request_during_ack(*, clinic_id, admission_id, expected_sequence):
        observed.append(expected_sequence)
        # A newer authorized assignment event arrives after the old chat
        # reconciliation but before its attempt to acknowledge the marker.
        events.queue_case_chat_sync_in_clinical_transaction(
            clinic_id=clinic_id, admission_id=admission_id,
            actor_user_id=ids["admin"],
        )
        db.session.commit()
        return original_ack(
            clinic_id=clinic_id, admission_id=admission_id,
            expected_sequence=expected_sequence,
        )

    monkeypatch.setattr(events, "_acknowledge_request_if_current", newer_request_during_ack)
    outcome = _deliver_admin(ids)
    assert observed == [1]
    assert outcome["pending"] is True
    assert outcome["acknowledged_sequence"] is None
    assert (_admin_request(ids).request_sequence, _admin_request(ids).processed_sequence) == (2, 0)
    monkeypatch.setattr(events, "_acknowledge_request_if_current", original_ack)
    assert _deliver_admin(ids)["pending"] is False
    assert _admin_request(ids).processed_sequence == 2
    assert db.session.query(AdmissionCaseConversation).count() == 1


@pytest.mark.parametrize("stage", ["post_commit", "scheduled_worker"])
def test_failure_logs_do_not_leak_exception_message_or_patient_identity(
    clinic, make_patient, make_staff, monkeypatch, caplog, stage,
):
    ids = _admin_case(clinic, make_patient, make_staff)
    marker = "CT4B1_UNIQUE_PRIVATE_PATIENT_NOTE_SENTINEL"
    if stage == "post_commit":
        monkeypatch.setattr(
            events, "ensure_admission_case_conversation", _fail(RuntimeError, marker),
        )
        with caplog.at_level(logging.WARNING):
            _assign_admin(ids)
        filtered = [r.getMessage() for r in caplog.records if "case_chat_sync." in r.getMessage()]
        assert any("deferred_after_clinical_commit" in value for value in filtered)
    else:
        _defer_admin_creation(monkeypatch, ids)
        monkeypatch.setattr(worker, "deliver_pending_case_chat_sync", _fail(RuntimeError, marker))
        with caplog.at_level(logging.WARNING):
            result = worker.process_pending_case_chat_sync.run(limit=1)
        assert result["failed"] == 1
        filtered = [r.getMessage() for r in caplog.records if "case_chat_sync." in r.getMessage()]
        assert any("scheduled_retry_deferred" in value for value in filtered)
    assert all(marker not in value for value in filtered)
    assert all(str(ids["admission"]) not in value.split("error_type=")[-1] for value in filtered)
    assert _admin_request(ids).processed_sequence == 0


@pytest.mark.parametrize("fault", [TimeoutError, ConnectionError, RuntimeError],
                         ids=["nurse_timeout", "nurse_connection", "nurse_5xx"])
def test_acknowledged_nurse_handoff_survives_chat_delivery_failure_and_recovers(
    clinic, make_patient, make_staff, monkeypatch, fault,
):
    ids = _nurse_case(clinic, make_patient, make_staff)
    original = events.ensure_admission_case_conversation
    monkeypatch.setattr(events, "ensure_admission_case_conversation", _fail(fault))
    result = nursing_service.acknowledge_handoff(
        ids["handoff_id"], clinic_id=ids["clinic_id"],
        actor_staff_id=ids["incoming_id"],
    )
    assert result.status == NursingHandoffStatus.ACKNOWLEDGED
    assert _queue_nurse(ids).authorization_handoff_id == ids["handoff_id"]
    assert _queue_nurse(ids).requested_by_user_id == ids["incoming_user_id"]
    assert (_queue_nurse(ids).request_sequence, _queue_nurse(ids).processed_sequence) == (1, 0)
    rows = db.session.execute(
        db.select(AdmissionCareAssignment).where(
            AdmissionCareAssignment.clinic_id == ids["clinic_id"],
            AdmissionCareAssignment.admission_id == ids["admission_id"],
            AdmissionCareAssignment.assignment_kind == "primary_nurse",
        ).order_by(AdmissionCareAssignment.id)
    ).scalars().all()
    assert len(rows) == 2 and rows[0].ended_at is not None
    assert rows[1].staff_id == ids["incoming_id"] and rows[1].ended_at is None
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    assert worker.process_pending_case_chat_sync.run(limit=1)["processed"] == 1
    assert _queue_nurse(ids).processed_sequence == 1
    link = db.session.query(AdmissionCaseConversation).one()
    statuses = _member_statuses(link.conversation_id)
    assert statuses == {ids["incoming_user_id"]: ParticipantStatus.ACCEPTED}
    assert len(rows) == 2


def test_worker_failure_of_one_clinic_does_not_block_another_clinic(
    clinic, make_clinic, make_patient, make_staff, monkeypatch,
):
    first = _admin_case(clinic, make_patient, make_staff)
    other = make_clinic(name="CT4B1 Tenant Next")
    second = _admin_case(other, make_patient, make_staff)
    _defer_admin_creation(monkeypatch, first)
    _defer_admin_creation(monkeypatch, second)
    original_delivery = worker.deliver_pending_case_chat_sync

    def one_tenant_down(**kwargs):
        if kwargs["clinic_id"] == first["clinic"]:
            raise ConnectionError("Simulated one-tenant chat provider outage")
        return original_delivery(**kwargs)

    monkeypatch.setattr(worker, "deliver_pending_case_chat_sync", one_tenant_down)
    outcome = worker.process_pending_case_chat_sync.run(limit=10)
    assert outcome == {"claimed": 2, "processed": 1, "failed": 1, "still_pending": 0}
    assert _admin_request(first).processed_sequence == 0
    assert _admin_request(second).processed_sequence == 1
    assert db.session.query(AdmissionCaseConversation).count() == 1


def test_repeated_failed_events_coalesce_and_recover_one_canonical_link(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _admin_case(clinic, make_patient, make_staff)
    original = events.ensure_admission_case_conversation
    monkeypatch.setattr(events, "ensure_admission_case_conversation", _fail(TimeoutError))
    _assign_admin(ids)
    _assign_admin(ids, kind="primary_nurse")
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 1
    assert _admin_request(ids).request_sequence == 2
    assert _admin_request(ids).processed_sequence == 0
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    assert _deliver_admin(ids)["pending"] is False
    assert _admin_request(ids).processed_sequence == 2
    assert db.session.query(AdmissionCaseConversation).count() == 1
    link = db.session.query(AdmissionCaseConversation).one()
    assert len(_member_statuses(link.conversation_id)) == 2
