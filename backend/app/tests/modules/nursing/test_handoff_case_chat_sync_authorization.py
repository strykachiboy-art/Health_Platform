"""CT3C focused: acknowledged-handoff provenance, durable sync and revocation.

CT1/CT2/CT3A/CT3B regressions intentionally not rerun in this slice.
"""
from datetime import datetime, timezone

import pytest

from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.chat_enums import ParticipantStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError
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
from app.modules.chat.services.case_chat_handoff_authorization import (
    require_acknowledged_handoff_sync_authority as authorize,
)
from app.modules.chat.services.chat_security_service import ChatSecurityService
from app.modules.chat.tasks import case_chat_sync_task as worker
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services import nursing_service
from app.modules.nursing.services import handoff_primary_transfer_service as transfer
from app.modules.nursing.services import care_team_assignment_service as assignment
from app.modules.staff.models.staff_model import Staff
from app.core.auth.user.models.user_model import User
from app.tests.modules.nursing.test_handoff_primary_transfer_preconditions import _case


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch):
    for mod in (nursing_service, transfer, creation, sync, assignment):
        monkeypatch.setattr(mod, "create_audit_log", lambda **_kw: None)


def _ack(ids, **kw):
    args = {
        "clinic_id": ids["clinic_id"],
        "actor_staff_id": ids["incoming_id"],
    }
    args.update(kw)
    return nursing_service.acknowledge_handoff(ids["handoff_id"], **args)


def _queue(ids):
    return db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == ids["clinic_id"],
            AdmissionCaseChatSyncRequest.admission_id == ids["admission_id"],
        )
    ).scalar_one()


def _assignments(ids):
    return db.session.execute(
        db.select(AdmissionCareAssignment).where(
            AdmissionCareAssignment.clinic_id == ids["clinic_id"],
            AdmissionCareAssignment.admission_id == ids["admission_id"],
            AdmissionCareAssignment.assignment_kind == "primary_nurse",
        ).order_by(AdmissionCareAssignment.id)
    ).scalars().all()


def _outgoing_user_id(ids):
    return db.session.get(Staff, ids["outgoing_id"]).user_id


def _defer(monkeypatch):
    original = events.ensure_admission_case_conversation
    def unavailable(**_kw):
        raise RuntimeError("Simulated chat unavailable")
    monkeypatch.setattr(events, "ensure_admission_case_conversation", unavailable)
    return original


def _admin(clinic, make_staff):
    return make_staff(clinic, role=Role.ADMIN)


def _link(ids):
    return db.session.execute(
        db.select(AdmissionCaseConversation).where(
            AdmissionCaseConversation.clinic_id == ids["clinic_id"],
            AdmissionCaseConversation.admission_id == ids["admission_id"],
        )
    ).scalar_one()


def test_ready_handoff_has_no_chat_sync_authority(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 0
    with pytest.raises(NotFoundError):
        authorize(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"], handoff_id=ids["handoff_id"],
        )


def test_acknowledgement_records_nurse_provenance_and_one_transfer(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    assert _ack(ids).status == NursingHandoffStatus.ACKNOWLEDGED
    queue = _queue(ids)
    assert queue.requested_by_user_id == ids["incoming_user_id"]
    assert queue.authorization_handoff_id == ids["handoff_id"]
    assert (queue.request_sequence, queue.processed_sequence) == (1, 0)
    rows = _assignments(ids)
    assert len(rows) == 2
    assert rows[0].ended_at is not None and rows[1].staff_id == ids["incoming_id"]
    assert rows[1].ended_at is None


def test_successful_auto_delivery_creates_one_canonical_channel(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _ack(ids)
    link = _link(ids)
    queue = _queue(ids)
    assert link.patient_id == ids["patient_id"]
    assert queue.request_sequence == queue.processed_sequence == 1
    members = db.session.execute(
        db.select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == link.conversation_id,
            ConversationParticipant.status == ParticipantStatus.ACCEPTED,
        )
    ).scalars().all()
    assert {m.user_id for m in members} == {ids["incoming_user_id"]}


def test_old_primary_loses_chat_access_even_before_delivery(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    admin = _admin(clinic, make_staff)
    creation.ensure_admission_case_conversation(
        clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
        actor_user_id=admin.user_id,
    )
    old_user = _outgoing_user_id(ids)
    link = _link(ids)
    assert ChatSecurityService.ensure_user_can_access_conversation(
        old_user, link.conversation_id,
    ).id == link.conversation_id
    _defer(monkeypatch)
    _ack(ids)
    assert _queue(ids).request_sequence > _queue(ids).processed_sequence
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(
            old_user, link.conversation_id,
        )


def test_background_retry_reconciles_removed_old_and_accepted_new(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    admin = _admin(clinic, make_staff)
    creation.ensure_admission_case_conversation(
        clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
        actor_user_id=admin.user_id,
    )
    _original = _defer(monkeypatch)
    _ack(ids)
    monkeypatch.setattr(events, "ensure_admission_case_conversation", _original)
    result = worker.process_pending_case_chat_sync.run(limit=10)
    assert result["processed"] == 1 and result["failed"] == 0
    link = _link(ids)
    members = {
        row.user_id: row.status
        for row in db.session.execute(
            db.select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == link.conversation_id,
            )
        ).scalars().all()
    }
    assert members[_outgoing_user_id(ids)] == ParticipantStatus.REMOVED
    assert members[ids["incoming_user_id"]] == ParticipantStatus.ACCEPTED
    assert _queue(ids).request_sequence == _queue(ids).processed_sequence
    assert len(_assignments(ids)) == 2


def test_chat_delivery_failure_never_undoes_clinical_ack(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    result = worker.process_pending_case_chat_sync.run(limit=10)
    assert result["failed"] == 1
    assert _queue(ids).processed_sequence == 0
    assert db.session.get(NursingHandoff, ids["handoff_id"]).status == NursingHandoffStatus.ACKNOWLEDGED
    assert len(_assignments(ids)) == 2


def test_queue_write_failure_rolls_back_handoff_and_assignment(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    def reject_queue(**_kw):
        raise RuntimeError("Simulated queue database write failure")
    monkeypatch.setattr(events, "queue_handoff_case_chat_sync_in_clinical_transaction", reject_queue)
    with pytest.raises(RuntimeError):
        _ack(ids)
    assert db.session.get(NursingHandoff, ids["handoff_id"]).status == NursingHandoffStatus.READY
    assert len(_assignments(ids)) == 1 and _assignments(ids)[0].ended_at is None


def test_identical_idempotency_replay_does_not_requeue(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    key = {"idempotency_key": "ct3c-ack-idem", "idempotency_user_id": ids["incoming_user_id"]}
    _ack(ids, **key)
    _ack(ids, **key)
    assert len(_assignments(ids)) == 2
    assert _queue(ids).request_sequence == 1


def test_duplicate_acknowledgement_does_not_requeue(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    with pytest.raises(ConflictError):
        _ack(ids)
    assert _queue(ids).request_sequence == 1


def test_inactive_incoming_nurse_cannot_retry(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    user = db.session.get(User, ids["incoming_user_id"])
    user.is_active = False
    db.session.commit()
    result = worker.process_pending_case_chat_sync.run(limit=1)
    assert result["failed"] == 1 and _queue(ids).processed_sequence == 0


def test_suspended_incoming_staff_cannot_retry(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    staff = db.session.get(Staff, ids["incoming_id"])
    staff.status = StaffStatus.SUSPENDED
    db.session.commit()
    with pytest.raises(NotFoundError):
        authorize(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"], handoff_id=ids["handoff_id"],
        )


def test_wrong_actor_cannot_consume_nurse_provenance(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    admin = _admin(clinic, make_staff)
    with pytest.raises(NotFoundError):
        events.deliver_pending_case_chat_sync(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=admin.user_id,
        )
    assert _queue(ids).processed_sequence == 0


def test_foreign_clinic_cannot_use_acknowledged_handoff(
    clinic, make_clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    other = make_clinic(name="CT3C foreign")
    with pytest.raises(NotFoundError):
        authorize(
            clinic_id=other.id, admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"], handoff_id=ids["handoff_id"],
        )


def test_ready_or_tampered_handoff_cannot_authorize_pending_delivery(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    handoff = db.session.get(NursingHandoff, ids["handoff_id"])
    handoff.status = NursingHandoffStatus.READY
    db.session.commit()
    with pytest.raises(NotFoundError):
        events.deliver_pending_case_chat_sync(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"],
        )
    assert _queue(ids).processed_sequence == 0


def test_wrong_handoff_provenance_rejected(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    # Do not mutate a database FK just to simulate forged provenance; the
    # authorization boundary must reject a wrong handoff ID on its own.
    with pytest.raises(NotFoundError):
        authorize(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"],
            handoff_id=ids["handoff_id"] + 77777,
        )
    assert _queue(ids).authorization_handoff_id == ids["handoff_id"]


def test_stale_handoff_does_not_authorize_after_new_primary_assigned(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    next_nurse = make_staff(clinic, role=Role.NURSE)
    active = _assignments(ids)[-1]
    active.staff_id = next_nurse.id
    db.session.commit()
    with pytest.raises(NotFoundError):
        authorize(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"], handoff_id=ids["handoff_id"],
        )


def test_admin_reassignment_overwrites_nurse_delivery_provenance(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    admin = _admin(clinic, make_staff)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    assignment.assign_primary_clinician(
        clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
        actor_user_id=admin.user_id, staff_id=doctor.id,
        assignment_kind="primary_doctor", assignment_reason="Additional clinical cover",
    )
    row = _queue(ids)
    assert row.authorization_handoff_id is None
    assert row.requested_by_user_id == admin.user_id
    assert row.request_sequence == 2 and row.processed_sequence == 0
    with pytest.raises(NotFoundError):
        events.deliver_pending_case_chat_sync(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"],
        )


def test_authorizer_rejects_booleans_and_invalid_ids(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    for bad in (True, 0, -3, "1"):
        with pytest.raises(NotFoundError):
            authorize(
                clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
                actor_user_id=ids["incoming_user_id"], handoff_id=bad,
            )


def test_wrong_user_role_cannot_retry(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer(monkeypatch)
    _ack(ids)
    db.session.get(User, ids["incoming_user_id"]).role = Role.DOCTOR
    db.session.commit()
    with pytest.raises(NotFoundError):
        authorize(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"], handoff_id=ids["handoff_id"],
        )
