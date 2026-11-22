"""CT2B: canonical admission/chat linkage and fail-closed context checks."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.chat_enums import ConversationStatus, ConversationType
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.services.case_conversation_link_service import (
    get_admission_case_conversation_link,
    validate_case_conversation_context,
)
from app.modules.ward.models.ward_model import Admission, Bed, Ward


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    creator = make_staff(clinic, role=Role.DOCTOR)
    ward = Ward(
        clinic_id=clinic.id, name=f"CT2B Ward {patient.id}",
        ward_type=WardType.GENERAL, capacity=2,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(
        ward_id=ward.id, bed_number=f"CT2B-{patient.id}",
        status=BedStatus.OCCUPIED,
    )
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id, bed_id=bed.id,
        admitted_by_id=creator.id, status=AdmissionStatus.ADMITTED,
    )
    db.session.add(admission)
    conversation = Conversation(
        clinic_id=clinic.id, patient_id=patient.id,
        created_by_id=creator.user_id,
        conversation_type=ConversationType.PATIENT,
        status=ConversationStatus.ACTIVE,
        title="Case channel",
    )
    db.session.add(conversation)
    db.session.flush()
    ids = {
        "clinic": clinic.id,
        "patient": patient.id,
        "admission": admission.id,
        "conversation": conversation.id,
        "creator": creator.user_id,
    }
    db.session.commit()
    return ids


def _link(ids, *, conversation_id=None, admission_id=None):
    return AdmissionCaseConversation(
        clinic_id=ids["clinic"],
        patient_id=ids["patient"],
        admission_id=(ids["admission"] if admission_id is None else admission_id),
        conversation_id=(ids["conversation"] if conversation_id is None else conversation_id),
        linked_by_user_id=ids["creator"],
    )


def test_registered_table_has_both_unique_canonical_keys():
    assert "admission_case_conversations" in db.metadata.tables
    table = AdmissionCaseConversation.__table__
    names = {item.name for item in table.constraints}
    assert "uq_admission_case_conversations_admission" in names
    assert "uq_admission_case_conversations_conversation" in names
    assert {"clinic_id", "patient_id", "admission_id", "conversation_id", "linked_by_user_id", "linked_at"}.issubset(set(table.columns.keys()))


def test_valid_patient_conversation_matches_active_admission(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    admission, conversation = validate_case_conversation_context(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        conversation_id=ids["conversation"],
    )
    assert admission.id == ids["admission"]
    assert conversation.patient_id == ids["patient"]


def test_other_clinic_cannot_resolve_admission(
    clinic, make_clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    other = make_clinic(name="CT2B Other Clinic")
    with pytest.raises(NotFoundError):
        validate_case_conversation_context(
            clinic_id=other.id, admission_id=ids["admission"],
            conversation_id=ids["conversation"],
        )
    with pytest.raises(NotFoundError):
        get_admission_case_conversation_link(
            clinic_id=other.id, admission_id=ids["admission"],
        )


def test_wrong_patient_conversation_rejected(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    unrelated = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    wrong = Conversation(
        clinic_id=clinic.id, patient_id=unrelated.id,
        created_by_id=admin.user_id,
        conversation_type=ConversationType.PATIENT,
        status=ConversationStatus.ACTIVE,
    )
    db.session.add(wrong)
    db.session.commit()
    with pytest.raises(NotFoundError):
        validate_case_conversation_context(
            clinic_id=ids["clinic"], admission_id=ids["admission"],
            conversation_id=wrong.id,
        )


def test_group_chat_cannot_be_used_as_patient_case_conversation(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    conversation = db.session.get(Conversation, ids["conversation"])
    conversation.conversation_type = ConversationType.GROUP
    db.session.commit()
    with pytest.raises(ConflictError):
        validate_case_conversation_context(
            clinic_id=ids["clinic"], admission_id=ids["admission"],
            conversation_id=ids["conversation"],
        )


def test_archived_conversation_cannot_be_newly_linked(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    db.session.get(Conversation, ids["conversation"]).status = ConversationStatus.ARCHIVED
    db.session.commit()
    with pytest.raises(ConflictError):
        validate_case_conversation_context(
            clinic_id=ids["clinic"], admission_id=ids["admission"],
            conversation_id=ids["conversation"],
        )


def test_discharged_admission_cannot_be_newly_linked(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    admission = db.session.get(Admission, ids["admission"])
    admission.status = AdmissionStatus.DISCHARGED
    db.session.commit()
    with pytest.raises(ConflictError):
        validate_case_conversation_context(
            clinic_id=ids["clinic"], admission_id=ids["admission"],
            conversation_id=ids["conversation"],
        )


def test_missing_link_is_not_silently_created(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    assert get_admission_case_conversation_link(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
    ) is None
    assert db.session.query(AdmissionCaseConversation).count() == 0


def test_persisted_link_is_readable_and_does_not_grant_chat_access(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    db.session.add(_link(ids))
    db.session.commit()
    linked = get_admission_case_conversation_link(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
    )
    assert linked.conversation_id == ids["conversation"]
    assert linked.patient_id == ids["patient"]
    assert linked.linked_at is not None
    from app.modules.chat.models.conversation_participant_model import ConversationParticipant
    assert db.session.query(ConversationParticipant).count() == 0


def test_reused_admission_cannot_receive_second_case_conversation(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    db.session.add(_link(ids))
    db.session.commit()
    second = Conversation(
        clinic_id=ids["clinic"], patient_id=ids["patient"],
        created_by_id=ids["creator"],
        conversation_type=ConversationType.PATIENT,
        status=ConversationStatus.ACTIVE,
    )
    db.session.add(second)
    db.session.flush()
    db.session.add(_link(ids, conversation_id=second.id))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_conversation_cannot_be_reused_for_different_admission(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    db.session.add(_link(ids))
    db.session.commit()
    other = _case(clinic, make_patient, make_staff)
    db.session.add(_link(other, conversation_id=ids["conversation"]))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_invalid_conversation_fk_is_rejected(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    db.session.add(_link(ids, conversation_id=987654321))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_invalid_ids_do_not_resolve_conversation(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    with pytest.raises(ValidationError):
        validate_case_conversation_context(
            clinic_id=ids["clinic"], admission_id=True,
            conversation_id=ids["conversation"],
        )
