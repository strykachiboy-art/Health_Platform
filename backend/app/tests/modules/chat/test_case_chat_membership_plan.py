"""CT2A: fail-closed eligible membership calculation only."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.core.exceptions import ConflictError, NotFoundError
from app.extensions import db
from app.modules.chat.services.case_chat_membership_plan import (
    build_case_chat_membership_plan,
)
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services import care_team_assignment_service as assignments
from app.modules.ward.models.ward_model import Admission, Bed, Ward


@pytest.fixture(autouse=True)
def _stub_assignment_audit(monkeypatch):
    monkeypatch.setattr(assignments, "create_audit_log", lambda **_kw: None)


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    nurse = make_staff(clinic, role=Role.NURSE)
    ward = Ward(
        clinic_id=clinic.id, name="CT2A Ward",
        ward_type=WardType.GENERAL, capacity=5,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number="CT2A-1", status=BedStatus.OCCUPIED)
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id, bed_id=bed.id, admitted_by_id=doctor.id,
        status=AdmissionStatus.ADMITTED,
    )
    db.session.add(admission)
    db.session.flush()
    ids = {
        "clinic": clinic.id, "patient": patient.id,
        "admin_user": admin.user_id, "doctor": doctor.id,
        "doctor_user": doctor.user_id, "nurse": nurse.id,
        "nurse_user": nurse.user_id, "admission": admission.id,
    }
    db.session.commit()
    return ids


def _assign(ids, kind="primary_doctor", staff_id=None):
    if staff_id is None:
        staff_id = ids["doctor"] if kind == "primary_doctor" else ids["nurse"]
    return assignments.assign_primary_clinician(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin_user"], staff_id=staff_id,
        assignment_kind=kind, assignment_reason="CT2A clinical allocation",
    )


def _plan(ids):
    return build_case_chat_membership_plan(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
    )


def test_no_assignments_never_infer_membership(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    result = _plan(ids)
    assert result["eligible_user_ids"] == []
    assert result["missing_primary_assignment_kinds"] == [
        "primary_doctor", "primary_nurse",
    ]
    assert result["has_eligible_member"] is False
    assert result["plan_only"] is True


def test_doctor_assignment_is_eligible(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    row = _assign(ids)
    plan = _plan(ids)
    assert plan["eligible_user_ids"] == [ids["doctor_user"]]
    assert plan["eligible_members"][0]["assignment_id"] == row.id
    assert plan["missing_primary_assignment_kinds"] == ["primary_nurse"]


def test_nurse_assignment_is_eligible(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_nurse")
    assert _plan(ids)["eligible_user_ids"] == [ids["nurse_user"]]


def test_both_primary_roles_produce_deterministic_unique_members(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_nurse")
    _assign(ids)
    p = _plan(ids)
    assert p["eligible_user_ids"] == sorted([ids["nurse_user"], ids["doctor_user"]])
    assert [r["assignment_kind"] for r in p["eligible_members"]] == [
        "primary_doctor", "primary_nurse",
    ]
    assert p["missing_primary_assignment_kinds"] == []
    assert p["excluded_inactive_or_invalid_kinds"] == []


def test_inactive_staff_excluded(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    from app.modules.staff.models.staff_model import Staff
    db.session.get(Staff, ids["doctor"]).status = StaffStatus.SUSPENDED
    db.session.commit()
    assert _plan(ids)["excluded_inactive_or_invalid_kinds"] == ["primary_doctor"]
    assert _plan(ids)["eligible_user_ids"] == []


def test_inactive_user_excluded(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    from app.core.auth.user.models.user_model import User
    db.session.get(User, ids["doctor_user"]).is_active = False
    db.session.commit()
    assert _plan(ids)["eligible_user_ids"] == []
    assert _plan(ids)["excluded_inactive_or_invalid_kinds"] == ["primary_doctor"]


def test_user_role_change_excluded(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    from app.core.auth.user.models.user_model import User
    db.session.get(User, ids["doctor_user"]).role = Role.NURSE
    db.session.commit()
    assert _plan(ids)["eligible_user_ids"] == []


def test_foreign_clinic_cannot_read_admission(
    clinic, make_clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    foreign = make_clinic(name="CT2A Foreign Clinic")
    with pytest.raises(NotFoundError):
        build_case_chat_membership_plan(
            clinic_id=foreign.id, admission_id=ids["admission"],
        )


def test_discharged_admission_blocks_plan(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    admission = db.session.get(Admission, ids["admission"])
    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    with pytest.raises(ConflictError):
        _plan(ids)


def test_ended_assignment_not_eligible(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    row = _assign(ids)
    assignments.end_primary_clinician_assignment(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
        actor_user_id=ids["admin_user"], assignment_kind="primary_doctor",
        expected_current_assignment_id=row.id, end_reason="Coverage ended",
    )
    assert _plan(ids)["eligible_user_ids"] == []


def test_ready_handoff_does_not_add_incoming_nurse(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids, "primary_nurse")
    incoming = make_staff(clinic, role=Role.NURSE)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.add(NursingHandoff(
        clinic_id=ids["clinic"], patient_id=ids["patient"],
        admission_id=ids["admission"],
        outgoing_staff_id=ids["nurse"], incoming_staff_id=incoming.id,
        shift_start_at=now, shift_end_at=now+timedelta(hours=8),
        status=NursingHandoffStatus.READY, ready_at=now,
        version=1,
    ))
    db.session.commit()
    assert _plan(ids)["eligible_user_ids"] == [ids["nurse_user"]]
    assert incoming.user_id not in _plan(ids)["eligible_user_ids"]


def test_read_only_plan_does_not_create_chat_or_change_assignments(
    clinic, make_patient, make_staff,
):
    from app.modules.chat.models.conversation_model import Conversation
    from app.modules.chat.models.conversation_participant_model import ConversationParticipant
    from app.modules.nursing.models.admission_care_assignment_model import (
        AdmissionCareAssignment,
    )
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    a_before = db.session.query(AdmissionCareAssignment).count()
    c_before = db.session.query(Conversation).count()
    p_before = db.session.query(ConversationParticipant).count()
    p = _plan(ids)
    assert p["conversation_created"] is False
    assert p["participants_changed"] is False
    assert p["chat_policy_checked"] is False
    assert db.session.query(AdmissionCareAssignment).count() == a_before
    assert db.session.query(Conversation).count() == c_before
    assert db.session.query(ConversationParticipant).count() == p_before


def test_inactive_patient_blocks_plan(clinic, make_patient, make_staff):
    from app.modules.patient.models.patient_model import Patient
    ids = _case(clinic, make_patient, make_staff)
    db.session.get(Patient, ids["patient"]).is_active = False
    db.session.commit()
    with pytest.raises(ConflictError):
        _plan(ids)


def test_nonexistent_admission_not_found(clinic):
    with pytest.raises(NotFoundError):
        build_case_chat_membership_plan(clinic_id=clinic.id, admission_id=987654321)
