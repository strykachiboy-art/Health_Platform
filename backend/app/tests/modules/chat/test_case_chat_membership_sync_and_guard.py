"""CT2C2: focused admission case chat reconciliation and live access denial.

Does not rerun CT2A, CT2B, or CT2C1. Those regressions are deferred until
CT2 final/CT3 entry as requested.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.enums.chat_enums import (
    ConversationStatus, ConversationType, ParticipantRole, ParticipantStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import (
    ConversationParticipant,
)
from app.modules.chat.models.message_model import Message
from app.modules.chat.services import (
    case_chat_access_guard as guard,
    case_chat_membership_sync_service as sync,
    case_conversation_creation_service as creation,
)
from app.modules.chat.services.chat_security_service import ChatSecurityService
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.ward.models.ward_model import Admission, Bed, Ward


@pytest.fixture(autouse=True)
def no_audit(monkeypatch):
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
        clinic_id=clinic.id, name=f"CT2C2 Ward {patient.id}",
        ward_type=WardType.GENERAL, capacity=3,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(
        ward_id=ward.id, bed_number=f"CT2C2-{patient.id}",
        status=BedStatus.OCCUPIED,
    )
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id, bed_id=bed.id,
        admitted_by_id=doctor.id, status=AdmissionStatus.ADMITTED,
        admitted_at=_now(),
    )
    db.session.add(admission)
    db.session.flush()
    ids = {
        "clinic": clinic.id, "patient": patient.id,
        "admin": admin.user_id, "doctor": doctor.id,
        "doctor_user": doctor.user_id, "nurse": nurse.id,
        "nurse_user": nurse.user_id, "admission": admission.id,
    }
    db.session.commit()
    return ids


def _assign(ids, kind, staff_id):
    row = AdmissionCareAssignment(
        clinic_id=ids["clinic"], patient_id=ids["patient"],
        admission_id=ids["admission"], staff_id=staff_id,
        assignment_kind=kind, assigned_by_user_id=ids["admin"],
        assignment_reason="CT2C2 coverage",
    )
    db.session.add(row)
    db.session.commit()
    return row.id


def _end(ids, kind):
    row = db.session.execute(db.select(AdmissionCareAssignment).where(
        AdmissionCareAssignment.clinic_id == ids["clinic"],
        AdmissionCareAssignment.admission_id == ids["admission"],
        AdmissionCareAssignment.assignment_kind == kind,
        AdmissionCareAssignment.ended_at.is_(None),
    )).scalar_one()
    row.ended_at = max(_now(), row.assigned_at)
    row.ended_by_user_id = ids["admin"]
    row.end_reason = "Shift coverage changed"
    db.session.commit()


def _change(ids, kind, staff_id):
    _end(ids, kind)
    _assign(ids, kind, staff_id)


def _create(ids):
    return creation.ensure_admission_case_conversation(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin"],
    )[0]


def _sync(ids, **overrides):
    args = {
        "clinic_id": ids["clinic"], "admission_id": ids["admission"],
        "actor_user_id": ids["admin"],
    }
    args.update(overrides)
    return sync.reconcile_admission_case_chat_membership(**args)


def _rows(conversation_id):
    return {
        row.user_id: row
        for row in db.session.execute(
            db.select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,
            )
        ).scalars().all()
    }


def _setup(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    _assign(ids, "primary_nurse", ids["nurse"])
    conversation = _create(ids)
    return ids, conversation.id


def test_initial_members_are_eligible_at_live_chat_gate(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    for user_id in (ids["doctor_user"], ids["nurse_user"]):
        assert ChatSecurityService.ensure_user_can_access_conversation(
            user_id, conversation_id,
        ).id == conversation_id
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(ids["admin"], conversation_id)


def test_old_doctor_denied_before_reconciliation_and_new_doctor_gated(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    incoming = make_staff(clinic, role=Role.DOCTOR)
    incoming_id = incoming.user_id
    _change(ids, "primary_doctor", incoming.id)
    assert _rows(conversation_id)[ids["doctor_user"]].status == ParticipantStatus.ACCEPTED
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_send_message(ids["doctor_user"], conversation_id)
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(incoming_id, conversation_id)
    assert ChatSecurityService.ensure_user_can_access_conversation(
        ids["nurse_user"], conversation_id,
    ).id == conversation_id


def test_sync_replaces_stale_doctor_and_keeps_nurse(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    incoming = make_staff(clinic, role=Role.DOCTOR)
    new_id = incoming.user_id
    _change(ids, "primary_doctor", incoming.id)
    result = _sync(ids)
    rows = _rows(conversation_id)
    assert result["changed"] is True
    assert result["added_user_ids"] == [new_id]
    assert result["revoked_user_ids"] == [ids["doctor_user"]]
    assert rows[ids["doctor_user"]].status == ParticipantStatus.REMOVED
    assert rows[ids["doctor_user"]].removed_at is not None
    assert rows[new_id].status == ParticipantStatus.ACCEPTED
    assert rows[ids["nurse_user"]].status == ParticipantStatus.ACCEPTED
    assert ChatSecurityService.ensure_user_can_access_conversation(
        new_id, conversation_id,
    ).id == conversation_id
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(ids["doctor_user"], conversation_id)


def test_sync_retry_is_idempotent_and_does_not_duplicate_rows(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    assert _sync(ids)["changed"] is False
    assert _sync(ids)["changed"] is False
    assert len(_rows(conversation_id)) == 2
    assert db.session.query(Conversation).count() == 1
    assert db.session.query(AdmissionCaseConversation).count() == 1


def test_exactly_one_eligible_conversation_administrator_remains(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    next_nurse = make_staff(clinic, role=Role.NURSE)
    _change(ids, "primary_nurse", next_nurse.id)
    _sync(ids)
    rows = _rows(conversation_id)
    accepted = [row for row in rows.values() if row.status == ParticipantStatus.ACCEPTED]
    assert {row.user_id for row in accepted} == {ids["doctor_user"], next_nurse.user_id}
    assert sum(row.role == ParticipantRole.ADMIN for row in accepted) == 1
    assert ids["admin"] not in rows


def test_removed_participant_is_reactivated_on_new_valid_assignment(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    replacement = make_staff(clinic, role=Role.DOCTOR)
    old_id = ids["doctor_user"]
    _change(ids, "primary_doctor", replacement.id)
    _sync(ids)
    _change(ids, "primary_doctor", ids["doctor"])
    result = _sync(ids)
    rows = _rows(conversation_id)
    assert old_id in result["added_user_ids"]
    assert replacement.user_id in result["revoked_user_ids"]
    assert rows[old_id].status == ParticipantStatus.ACCEPTED
    assert rows[old_id].removed_at is None
    assert len(rows) == 3


def test_expired_members_are_excluded_from_message_recipient_delivery(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    incoming = make_staff(clinic, role=Role.DOCTOR)
    _change(ids, "primary_doctor", incoming.id)
    recipients = ChatSecurityService.get_active_conversation_recipient_user_ids(
        ids["clinic"], conversation_id,
    )
    assert recipients == [ids["nurse_user"]]
    _sync(ids)
    assert set(ChatSecurityService.get_active_conversation_recipient_user_ids(
        ids["clinic"], conversation_id,
    )) == {ids["nurse_user"], incoming.user_id}


def test_revoked_member_cannot_read_messages_or_attachments(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    message = Message(
        clinic_id=ids["clinic"], conversation_id=conversation_id,
        sender_id=ids["doctor_user"], content="Clinical case update",
    )
    db.session.add(message)
    db.session.commit()
    message_id = message.id
    new_doctor = make_staff(clinic, role=Role.DOCTOR)
    _change(ids, "primary_doctor", new_doctor.id)
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_message(ids["doctor_user"], message_id)
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_attachment(ids["doctor_user"], message_id)
    assert ChatSecurityService.ensure_user_can_access_message(
        ids["nurse_user"], message_id,
    ).id == message_id


def test_manual_unauthorized_accepted_participant_denied_and_revoked(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    outsider = make_staff(clinic, role=Role.DOCTOR)
    outsider_id = outsider.user_id
    db.session.add(ConversationParticipant(
        clinic_id=ids["clinic"], conversation_id=conversation_id,
        user_id=outsider_id, role=ParticipantRole.MEMBER,
        status=ParticipantStatus.ACCEPTED, joined_at=datetime.now(timezone.utc),
    ))
    db.session.commit()
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(outsider_id, conversation_id)
    assert outsider_id not in ChatSecurityService.get_active_conversation_recipient_user_ids(
        ids["clinic"], conversation_id,
    )
    result = _sync(ids)
    assert outsider_id in result["revoked_user_ids"]
    assert _rows(conversation_id)[outsider_id].status == ParticipantStatus.REMOVED


def test_no_valid_clinicians_removes_all_members_without_new_grant(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    _end(ids, "primary_doctor")
    _end(ids, "primary_nurse")
    result = _sync(ids)
    assert result["eligible_user_ids"] == []
    assert set(result["revoked_user_ids"]) == {ids["doctor_user"], ids["nurse_user"]}
    assert all(row.status == ParticipantStatus.REMOVED for row in _rows(conversation_id).values())
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(ids["nurse_user"], conversation_id)


def test_disabled_chat_policy_denies_access_even_for_existing_member(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    def disabled(_clinic_id):
        raise ValidationError("Chat disabled")
    monkeypatch.setattr(guard.ChatPolicyService, "ensure_chat_enabled", disabled)
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(ids["doctor_user"], conversation_id)


def test_chat_group_policy_failure_cannot_add_new_member(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    incoming = make_staff(clinic, role=Role.DOCTOR)
    _change(ids, "primary_doctor", incoming.id)
    def deny(*_args):
        raise ValidationError("Group policy denied")
    monkeypatch.setattr(sync.ChatPolicyService, "ensure_group_conversation_allowed", deny)
    with pytest.raises(ValidationError, match="Group policy denied"):
        _sync(ids)
    assert incoming.user_id not in _rows(conversation_id)
    assert _rows(conversation_id)[ids["doctor_user"]].status == ParticipantStatus.ACCEPTED
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(ids["doctor_user"], conversation_id)


def test_audit_failure_rolls_back_all_membership_changes(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    incoming = make_staff(clinic, role=Role.DOCTOR)
    _change(ids, "primary_doctor", incoming.id)
    def fail(**_kw):
        raise RuntimeError("Membership audit unavailable")
    monkeypatch.setattr(sync, "create_audit_log", fail)
    with pytest.raises(RuntimeError, match="Membership audit unavailable"):
        _sync(ids)
    rows = _rows(conversation_id)
    assert incoming.user_id not in rows
    assert rows[ids["doctor_user"]].status == ParticipantStatus.ACCEPTED
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(ids["doctor_user"], conversation_id)


def test_non_admin_cannot_synchronize(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    with pytest.raises(ValidationError):
        _sync(ids, actor_user_id=ids["doctor_user"])
    assert len(_rows(conversation_id)) == 2


def test_cross_clinic_actor_or_admission_is_rejected(
    clinic, make_clinic, make_patient, make_staff,
):
    ids, _conversation_id = _setup(clinic, make_patient, make_staff)
    other = make_clinic(name="CT2C2 Foreign")
    foreign_admin = make_staff(other, role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        _sync(ids, actor_user_id=foreign_admin.user_id)
    with pytest.raises(NotFoundError):
        _sync(ids, clinic_id=other.id, actor_user_id=foreign_admin.user_id)


def test_discharged_case_denied_at_guard_without_sync(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    admission = db.session.get(Admission, ids["admission"])
    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = _now()
    db.session.commit()
    with pytest.raises(NotFoundError):
        ChatSecurityService.ensure_user_can_access_conversation(ids["doctor_user"], conversation_id)
    with pytest.raises(ConflictError):
        _sync(ids)


def test_normal_unlinked_patient_chat_preserves_legacy_access(
    clinic, make_patient, make_staff,
):
    patient = make_patient(clinic)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    conversation = Conversation(
        clinic_id=clinic.id, conversation_type=ConversationType.PATIENT,
        status=ConversationStatus.ACTIVE,
        created_by_id=doctor.user_id, patient_id=patient.id,
    )
    db.session.add(conversation)
    db.session.flush()
    db.session.add(ConversationParticipant(
        clinic_id=clinic.id, conversation_id=conversation.id,
        user_id=doctor.user_id, status=ParticipantStatus.ACCEPTED,
        role=ParticipantRole.ADMIN, joined_at=datetime.now(timezone.utc),
    ))
    db.session.commit()
    assert ChatSecurityService.ensure_user_can_access_conversation(
        doctor.user_id, conversation.id,
    ).id == conversation.id
    assert ChatSecurityService.get_active_conversation_recipient_user_ids(
        clinic.id, conversation.id,
    ) == [doctor.user_id]


def test_sync_no_link_fails_without_creating_conversation(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    with pytest.raises(NotFoundError):
        _sync(ids)
    assert db.session.query(Conversation).count() == 0


def test_sync_preserves_single_conversation_and_link(
    clinic, make_patient, make_staff,
):
    ids, conversation_id = _setup(clinic, make_patient, make_staff)
    incoming = make_staff(clinic, role=Role.DOCTOR)
    _change(ids, "primary_doctor", incoming.id)
    _sync(ids)
    assert db.session.query(Conversation).count() == 1
    assert db.session.query(AdmissionCaseConversation).count() == 1
    assert db.session.query(Conversation).one().id == conversation_id
