"""CT2C1: atomic admission chat creation with the exact CT2A eligible team."""

from datetime import datetime, timezone

import pytest

from app.core.enums.chat_enums import (
    ConversationStatus,
    ConversationType,
    ParticipantRole,
    ParticipantStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
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
from app.modules.chat.services import case_conversation_creation_service as service
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.ward.models.ward_model import Admission, Bed, Ward


@pytest.fixture(autouse=True)
def suppress_audit(monkeypatch):
    monkeypatch.setattr(service, "create_audit_log", lambda **_kwargs: None)


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    nurse = make_staff(clinic, role=Role.NURSE)
    ward = Ward(
        clinic_id=clinic.id, name=f"CT2C1 Ward {patient.id}",
        ward_type=WardType.GENERAL, capacity=3,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number=f"CT2C1-{patient.id}", status=BedStatus.OCCUPIED)
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id, bed_id=bed.id, admitted_by_id=doctor.id,
        status=AdmissionStatus.ADMITTED,
        admitted_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.session.add(admission)
    db.session.flush()
    ids = {
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
    return ids


def _assign(ids, kind, staff_id):
    db.session.add(AdmissionCareAssignment(
        clinic_id=ids["clinic"], patient_id=ids["patient"],
        admission_id=ids["admission"], staff_id=staff_id,
        assignment_kind=kind, assigned_by_user_id=ids["admin"],
        assignment_reason="Authorized CT2C1 coverage",
    ))
    db.session.commit()


def _create(ids, **kw):
    args = {
        "clinic_id": ids["clinic"],
        "admission_id": ids["admission"],
        "actor_user_id": ids["admin"],
    }
    args.update(kw)
    return service.ensure_admission_case_conversation(**args)


def _participants(conversation_id):
    return db.session.execute(
        db.select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation_id,
        ).order_by(ConversationParticipant.user_id)
    ).scalars().all()


def test_creates_exactly_one_linked_patient_conversation_with_two_assigned_clinicians(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    _assign(ids, "primary_nurse", ids["nurse"])
    conversation, link, created = _create(ids)
    assert created is True
    assert conversation.conversation_type == ConversationType.PATIENT
    assert conversation.status == ConversationStatus.ACTIVE
    assert conversation.clinic_id == ids["clinic"]
    assert conversation.patient_id == ids["patient"]
    assert link.admission_id == ids["admission"]
    assert link.conversation_id == conversation.id
    members = _participants(conversation.id)
    assert {row.user_id for row in members} == {ids["doctor_user"], ids["nurse_user"]}
    assert all(row.status == ParticipantStatus.ACCEPTED for row in members)
    assert sum(row.role == ParticipantRole.ADMIN for row in members) == 1
    assert conversation.created_by_id == ids["admin"]
    assert ids["admin"] not in {row.user_id for row in members}


def test_retry_reuses_link_without_new_conversation_or_participants(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    first, link, created = _create(ids)
    second, again, new = _create(ids)
    assert created is True and new is False
    assert first.id == second.id and link.id == again.id
    assert db.session.query(Conversation).count() == 1
    assert db.session.query(AdmissionCaseConversation).count() == 1
    assert len(_participants(first.id)) == 1


def test_no_primary_assignments_fails_without_creating_chat(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    with pytest.raises(ConflictError, match="at least one"):
        _create(ids)
    assert db.session.query(Conversation).count() == 0
    assert db.session.query(AdmissionCaseConversation).count() == 0


def test_non_admin_cannot_create_patient_case(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    other = make_staff(clinic, role=Role.DOCTOR)
    with pytest.raises(ValidationError):
        _create(ids, actor_user_id=other.user_id)
    assert db.session.query(Conversation).count() == 0


def test_cross_clinic_admin_hidden_and_no_chat_created(
    clinic, make_clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    foreign = make_clinic(name="CT2C1 Foreign Clinic")
    foreign_admin = make_staff(foreign, role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        _create(ids, actor_user_id=foreign_admin.user_id)
    assert db.session.query(Conversation).count() == 0


def test_cross_clinic_admission_hidden(
    clinic, make_clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    foreign = make_clinic(name="CT2C1 Tenant B")
    admin = make_staff(foreign, role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        _create(ids, clinic_id=foreign.id, actor_user_id=admin.user_id)


def test_discharged_admission_cannot_start_new_case(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    admission = db.session.get(Admission, ids["admission"])
    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    with pytest.raises(ConflictError):
        _create(ids)
    assert db.session.query(Conversation).count() == 0


def test_inactive_assigned_clinician_is_excluded_from_initial_chat(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    _assign(ids, "primary_nurse", ids["nurse"])
    from app.modules.staff.models.staff_model import Staff
    nurse = db.session.get(Staff, ids["nurse"])
    nurse.status = StaffStatus.SUSPENDED
    db.session.commit()
    conversation, _link, created = _create(ids)
    assert created
    assert {row.user_id for row in _participants(conversation.id)} == {ids["doctor_user"]}


def test_all_inactive_assignees_block_creation(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    from app.modules.staff.models.staff_model import Staff
    doctor = db.session.get(Staff, ids["doctor"])
    doctor.status = StaffStatus.SUSPENDED
    db.session.commit()
    with pytest.raises(ConflictError):
        _create(ids)
    assert db.session.query(AdmissionCaseConversation).count() == 0


def test_policy_disabled_aborts_before_conversation_insert(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    def deny(_clinic_id):
        raise ValidationError("Chat policy disabled")
    monkeypatch.setattr(service.ChatPolicyService, "ensure_chat_enabled", deny)
    with pytest.raises(ValidationError, match="policy disabled"):
        _create(ids)
    assert db.session.query(Conversation).count() == 0


def test_group_size_policy_blocks_creation(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    def deny(*_args):
        raise ValidationError("Group policy blocked")
    monkeypatch.setattr(service.ChatPolicyService, "ensure_group_conversation_allowed", deny)
    with pytest.raises(ValidationError, match="Group policy blocked"):
        _create(ids)
    assert db.session.query(Conversation).count() == 0


def test_audit_failure_rolls_back_both_chat_and_admission_link(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    def fail(**_kwargs):
        raise RuntimeError("Audit storage unavailable")
    monkeypatch.setattr(service, "create_audit_log", fail)
    with pytest.raises(RuntimeError, match="Audit storage unavailable"):
        _create(ids)
    assert db.session.query(Conversation).count() == 0
    assert db.session.query(ConversationParticipant).count() == 0
    assert db.session.query(AdmissionCaseConversation).count() == 0


def test_existing_link_refuses_archived_conversation(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    conversation, link, _ = _create(ids)
    conversation.status = ConversationStatus.ARCHIVED
    db.session.commit()
    with pytest.raises(ConflictError):
        _create(ids)
    assert db.session.query(Conversation).count() == 1
    assert db.session.query(AdmissionCaseConversation).count() == 1


def test_same_patient_new_admission_gets_distinct_case_channel(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_doctor", ids["doctor"])
    first, _first_link, _ = _create(ids)
    # The prior admission is now discharged before a subsequent admission.
    prior = db.session.get(Admission, ids["admission"])
    prior.status = AdmissionStatus.DISCHARGED
    prior.discharged_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    ward = Ward(clinic_id=clinic.id, name="CT2C1 Second Ward", ward_type=WardType.GENERAL, capacity=2)
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number="CT2C1-B", status=BedStatus.OCCUPIED)
    db.session.add(bed)
    db.session.flush()
    admission = Admission(patient_id=ids["patient"], bed_id=bed.id, admitted_by_id=ids["doctor"], status=AdmissionStatus.ADMITTED)
    db.session.add(admission)
    db.session.flush()
    next_id = admission.id
    db.session.add(AdmissionCareAssignment(
        clinic_id=ids["clinic"], patient_id=ids["patient"],
        admission_id=next_id, staff_id=ids["doctor"],
        assignment_kind="primary_doctor", assigned_by_user_id=ids["admin"],
        assignment_reason="New clinical admission",
    ))
    db.session.commit()
    ids["admission"] = next_id
    second, _second_link, created = _create(ids)
    assert created and first.id != second.id
    assert db.session.query(AdmissionCaseConversation).count() == 2
