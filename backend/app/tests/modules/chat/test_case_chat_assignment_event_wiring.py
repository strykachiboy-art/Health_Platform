"""CT2C3 focused only: durable nonblocking assignment -> case chat delivery.

Prior CT2A/B/C1/C2 tests deliberately NOT rerun in this slice.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.enums.chat_enums import ParticipantStatus, ConversationType
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
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
from app.modules.chat.services import case_chat_membership_sync_service as sync
from app.modules.chat.services.chat_security_service import ChatSecurityService
from app.modules.nursing.services import care_team_assignment_service as assignment
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.ward.models.ward_model import Admission, Bed, Ward


@pytest.fixture(autouse=True)
def quiet_audits(monkeypatch):
    monkeypatch.setattr(assignment, "create_audit_log", lambda **_kw: None)
    monkeypatch.setattr(creation, "create_audit_log", lambda **_kw: None)
    monkeypatch.setattr(sync, "create_audit_log", lambda **_kw: None)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    nurse = make_staff(clinic, role=Role.NURSE)
    ward = Ward(
        clinic_id=clinic.id, name="CT2C3 Ward", ward_type=WardType.GENERAL,
        capacity=6,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number="CT2C3-1", status=BedStatus.OCCUPIED)
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id, bed_id=bed.id, admitted_by_id=doctor.id,
        status=AdmissionStatus.ADMITTED, admitted_at=_now(),
    )
    db.session.add(admission)
    db.session.flush()
    values = {
        "clinic": clinic.id,
        "patient": patient.id,
        "admin": admin.user_id,
        "doctor": doctor.id,
        "doctor_user": doctor.user_id,
        "nurse": nurse.id,
        "nurse_user": nurse.user_id,
        "admission": admission.id,
    }
    db.session.commit()
    return values


def _assign(ids, kind="primary_doctor", staff_id=None, **overrides):
    target = ids["doctor"] if kind == "primary_doctor" else ids["nurse"]
    args = {
        "clinic_id": ids["clinic"],
        "admission_id": ids["admission"],
        "actor_user_id": ids["admin"],
        "staff_id": target if staff_id is None else staff_id,
        "assignment_kind": kind,
        "assignment_reason": "CT2C3 clinical coverage",
    }
    args.update(overrides)
    return assignment.assign_primary_clinician(**args)


def _end(ids, row):
    return assignment.end_primary_clinician_assignment(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin"], assignment_kind=row.assignment_kind,
        expected_current_assignment_id=row.id, end_reason="Shift ended",
    )


def _link(ids):
    return db.session.query(AdmissionCaseConversation).filter_by(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
    ).one()


def _queue(ids):
    return db.session.query(AdmissionCaseChatSyncRequest).filter_by(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
    ).one()


def _members(ids):
    link = _link(ids)
    return {
        p.user_id: p
        for p in db.session.query(ConversationParticipant).filter_by(
            conversation_id=link.conversation_id,
        ).all()
    }


def _retry(ids, **overrides):
    kw = {
        "clinic_id": ids["clinic"], "admission_id": ids["admission"],
        "actor_user_id": ids["admin"],
    }
    kw.update(overrides)
    return events.deliver_pending_case_chat_sync(**kw)


def test_first_authorized_assignment_creates_patient_case_automatically(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    result = _assign(ids)
    link = _link(ids)
    conversation = db.session.get(Conversation, link.conversation_id)
    assert result.staff_id == ids["doctor"]
    assert conversation.patient_id == ids["patient"]
    assert conversation.conversation_type == ConversationType.PATIENT
    assert set(_members(ids)) == {ids["doctor_user"]}
    assert _queue(ids).request_sequence == _queue(ids).processed_sequence == 1
    assert ids["admin"] not in _members(ids)


def test_second_clinical_assignment_reuses_case_and_adds_nurse(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    link_id = _link(ids).id
    _assign(ids, "primary_nurse")
    assert _link(ids).id == link_id
    assert db.session.query(Conversation).count() == 1
    assert set(_members(ids)) == {ids["doctor_user"], ids["nurse_user"]}
    assert all(x.status == ParticipantStatus.ACCEPTED for x in _members(ids).values())
    assert _queue(ids).request_sequence == _queue(ids).processed_sequence == 2


def test_reassignment_immediately_revokes_old_doctor_and_adds_new_doctor(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    first = _assign(ids)
    another = make_staff(clinic, role=Role.DOCTOR)
    second = _assign(
        ids, "primary_doctor", another.id,
        expected_current_assignment_id=first.id,
    )
    members = _members(ids)
    assert second.staff_id == another.id
    assert members[ids["doctor_user"]].status == ParticipantStatus.REMOVED
    assert members[another.user_id].status == ParticipantStatus.ACCEPTED
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(
            ids["doctor_user"], _link(ids).conversation_id,
        )
    assert ChatSecurityService.ensure_user_can_access_conversation(
        another.user_id, _link(ids).conversation_id,
    )
    assert db.session.query(Conversation).count() == 1


def test_ending_assignment_automatically_revokes_outgoing_nurse(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    nurse_row = _assign(ids, "primary_nurse")
    _end(ids, nurse_row)
    assert _members(ids)[ids["nurse_user"]].status == ParticipantStatus.REMOVED
    assert _members(ids)[ids["doctor_user"]].status == ParticipantStatus.ACCEPTED
    assert _queue(ids).processed_sequence == 3


def test_chat_create_failure_does_not_block_clinical_assignment_and_is_retryable(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    original = events.ensure_admission_case_conversation
    def fail(**_kw):
        raise ValidationError("Chat feature disabled")
    monkeypatch.setattr(events, "ensure_admission_case_conversation", fail)
    assigned = _assign(ids)
    assert assigned.id > 0
    assert db.session.query(AdmissionCareAssignment).count() == 1
    assert db.session.query(Conversation).count() == 0
    assert _queue(ids).request_sequence == 1
    assert _queue(ids).processed_sequence == 0
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    result = _retry(ids)
    assert result["pending"] is False
    assert result["created"] is True
    assert _queue(ids).processed_sequence == 1
    assert set(_members(ids)) == {ids["doctor_user"]}


def test_sync_failure_does_not_block_clinical_reassignment(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    original_row = _assign(ids)
    replacement = make_staff(clinic, role=Role.DOCTOR)
    original_sync = events.reconcile_admission_case_chat_membership
    def fail(**_kw):
        raise RuntimeError("Chat audit unavailable")
    monkeypatch.setattr(events, "reconcile_admission_case_chat_membership", fail)
    next_row = _assign(
        ids, "primary_doctor", replacement.id,
        expected_current_assignment_id=original_row.id,
    )
    assert next_row.id > original_row.id
    assert _queue(ids).request_sequence == 2
    assert _queue(ids).processed_sequence == 1
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(
            ids["doctor_user"], _link(ids).conversation_id,
        )
    monkeypatch.setattr(events, "reconcile_admission_case_chat_membership", original_sync)
    result = _retry(ids)
    assert result["pending"] is False
    assert _members(ids)[replacement.user_id].status == ParticipantStatus.ACCEPTED
    assert _members(ids)[ids["doctor_user"]].status == ParticipantStatus.REMOVED


def test_successful_delivery_is_idempotent_and_does_not_duplicate_link(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    assert _retry(ids) == {"pending": False, "changed": False}
    assert _retry(ids) == {"pending": False, "changed": False}
    assert db.session.query(AdmissionCaseConversation).count() == 1
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 1
    assert db.session.query(ConversationParticipant).count() == 1


def test_stale_compare_and_swap_failure_adds_no_request(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    first = _assign(ids)
    sequence = _queue(ids).request_sequence
    second = make_staff(clinic, role=Role.DOCTOR)
    with pytest.raises(ConflictError):
        _assign(ids, "primary_doctor", second.id, expected_current_assignment_id=999999)
    assert _queue(ids).request_sequence == sequence
    assert db.session.query(AdmissionCareAssignment).count() == 1
    assert db.session.query(Conversation).count() == 1


def test_role_validation_failure_does_not_create_sync_request(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    with pytest.raises(ValidationError):
        _assign(ids, "primary_doctor", ids["nurse"])
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 0
    assert db.session.query(Conversation).count() == 0


def test_clinical_audit_failure_rolls_back_queue_and_prevents_chat_creation(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    def fail(**_kw):
        raise RuntimeError("Clinical audit unavailable")
    monkeypatch.setattr(assignment, "create_audit_log", fail)
    with pytest.raises(RuntimeError, match="Clinical audit unavailable"):
        _assign(ids)
    assert db.session.query(AdmissionCareAssignment).count() == 0
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 0
    assert db.session.query(Conversation).count() == 0


def test_foreign_clinic_cannot_retry_case_chat_request(
    clinic, make_clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    other = make_clinic(name="CT2C3 Foreign Clinic")
    foreign_admin = make_staff(other, role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        _retry(ids, actor_user_id=foreign_admin.user_id)
    with pytest.raises(NotFoundError):
        _retry(ids, clinic_id=other.id, actor_user_id=foreign_admin.user_id)
    assert _queue(ids).processed_sequence == 1


def test_stale_acknowledgement_does_not_erase_later_request(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    events.queue_case_chat_sync_in_clinical_transaction(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin"],
    )
    db.session.commit()
    assert _queue(ids).request_sequence == 2
    assert events._acknowledge_request_if_current(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        expected_sequence=1,
    ) is False
    assert _queue(ids).processed_sequence == 1
    assert _retry(ids)["pending"] is False
    assert _queue(ids).processed_sequence == 2


def test_two_failed_deliveries_coalesce_into_one_request_row(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    def fail(**_kw):
        raise ValidationError("Chat transport off")
    monkeypatch.setattr(events, "ensure_admission_case_conversation", fail)
    _assign(ids)
    _assign(ids, "primary_nurse")
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 1
    assert _queue(ids).request_sequence == 2
    assert _queue(ids).processed_sequence == 0
    assert db.session.query(AdmissionCareAssignment).count() == 2


def test_stale_members_denied_while_dispatch_is_pending(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    previous = _assign(ids)
    newer = make_staff(clinic, role=Role.DOCTOR)
    monkeypatch.setattr(events, "reconcile_admission_case_chat_membership", lambda **_kw: (_ for _ in ()).throw(RuntimeError("Unavailable")))
    _assign(ids, "primary_doctor", newer.id, expected_current_assignment_id=previous.id)
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_send_message(
            ids["doctor_user"], _link(ids).conversation_id,
        )
    assert _queue(ids).request_sequence > _queue(ids).processed_sequence


def test_clinical_end_also_queues_when_chat_delivery_fails(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    row = _assign(ids)
    monkeypatch.setattr(events, "reconcile_admission_case_chat_membership", lambda **_kw: (_ for _ in ()).throw(RuntimeError("Offline")))
    ended = _end(ids, row)
    assert ended.ended_at is not None
    assert _queue(ids).request_sequence == 2
    assert _queue(ids).processed_sequence == 1
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(
            ids["doctor_user"], _link(ids).conversation_id,
        )


def test_dispatch_failure_does_not_change_clinical_response(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    def fail(**_kw):
        raise RuntimeError("Dispatcher not ready")
    monkeypatch.setattr(events, "attempt_case_chat_sync_after_clinical_commit", fail)
    result = _assign(ids)
    assert result.staff_id == ids["doctor"]
    assert _queue(ids).request_sequence == 1
    assert _queue(ids).processed_sequence == 0


def test_discharge_blocks_clinical_write_and_event_queue(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    admission = db.session.get(Admission, ids["admission"])
    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = _now()
    db.session.commit()
    with pytest.raises(ConflictError):
        _assign(ids)
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 0


def test_no_new_chat_api_is_exposed_in_ct2c3(app):
    paths = {rule.rule for rule in app.url_map.iter_rules()}
    assert not any('/case-chat/' in path for path in paths)
    assert not any('/case-conversation/' in path for path in paths)

def test_foreign_clinic_without_pending_request_is_not_a_silent_noop(
    clinic, make_clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 0
    foreign = make_clinic(name="CT2C3 No Pending Foreign")
    foreign_admin = make_staff(foreign, role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        _retry(
            ids,
            clinic_id=foreign.id,
            actor_user_id=foreign_admin.user_id,
        )
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 0
