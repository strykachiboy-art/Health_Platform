"""CT1B2: authorized, admission-scoped primary clinician transitions."""

from datetime import datetime, timezone

import pytest

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.services import care_team_assignment_service as service
from app.modules.ward.models.ward_model import Admission, Bed, Ward


@pytest.fixture(autouse=True)
def capture_audit_by_default(monkeypatch):
    monkeypatch.setattr(service, "create_audit_log", lambda **_kwargs: None)


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    nurse = make_staff(clinic, role=Role.NURSE)
    ward = Ward(
        clinic_id=clinic.id,
        name="CT1B2 Ward",
        ward_type=WardType.GENERAL,
        capacity=5,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(
        ward_id=ward.id,
        bed_number="CT1B2-01",
        status=BedStatus.OCCUPIED,
    )
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=doctor.id,
        status=AdmissionStatus.ADMITTED,
        admitted_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.session.add(admission)
    db.session.flush()
    ids = {
        "clinic": clinic.id,
        "patient": patient.id,
        "admin_user": admin.user_id,
        "admin_staff": admin.id,
        "doctor": doctor.id,
        "nurse": nurse.id,
        "admission": admission.id,
    }
    db.session.commit()
    return ids


def _assign(ids, staff_id=None, kind=service.PRIMARY_DOCTOR, **overrides):
    values = {
        "clinic_id": ids["clinic"],
        "admission_id": ids["admission"],
        "actor_user_id": ids["admin_user"],
        "staff_id": ids["doctor"] if staff_id is None else staff_id,
        "assignment_kind": kind,
        "assignment_reason": "Clinical allocation",
    }
    values.update(overrides)
    return service.assign_primary_clinician(**values)


def test_initial_assignments_are_authoritative_and_role_distinct(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    doctor = _assign(ids)
    nurse = _assign(ids, staff_id=ids["nurse"], kind=service.PRIMARY_NURSE)
    active = service.get_primary_assignments(
        admission_id=ids["admission"], clinic_id=ids["clinic"],
    )
    assert active["primary_doctor"].id == doctor.id
    assert active["primary_nurse"].id == nurse.id
    assert doctor.patient_id == ids["patient"]
    assert nurse.assigned_by_user_id == ids["admin_user"]


def test_duplicate_active_primary_assignment_rejected(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    first = _assign(ids)
    another = make_staff(clinic, role=Role.DOCTOR)
    another_id = another.id
    with pytest.raises(ConflictError):
        _assign(ids, staff_id=another_id)
    with pytest.raises(ConflictError):
        _assign(ids, staff_id=ids["doctor"], expected_current_assignment_id=first.id)
    assert db.session.query(AdmissionCareAssignment).count() == 1


def test_explicit_reassignment_ends_old_row_and_preserves_history(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    first = _assign(ids)
    first_id = first.id
    another = make_staff(clinic, role=Role.DOCTOR)
    next_staff_id = another.id
    next_row = _assign(
        ids,
        staff_id=next_staff_id,
        assignment_reason="On-call physician changed",
        expected_current_assignment_id=first_id,
    )
    history = service.list_assignment_history(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
    )
    assert len(history) == 2
    old = db.session.get(AdmissionCareAssignment, first_id)
    assert old.ended_at is not None
    assert old.ended_by_user_id == ids["admin_user"]
    assert old.end_reason == "On-call physician changed"
    assert next_row.ended_at is None
    assert next_row.staff_id == next_staff_id


def test_stale_assignment_version_cannot_replace_new_owner(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    first = _assign(ids)
    first_id = first.id
    another = make_staff(clinic, role=Role.DOCTOR)
    _assign(
        ids,
        staff_id=another.id,
        expected_current_assignment_id=first_id,
    )
    third = make_staff(clinic, role=Role.DOCTOR)
    with pytest.raises(ConflictError):
        _assign(
            ids,
            staff_id=third.id,
            expected_current_assignment_id=first_id,
        )
    assert db.session.query(AdmissionCareAssignment).count() == 2


def test_non_admin_is_not_allowed_to_assign(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    with pytest.raises(ValidationError):
        _assign(ids, actor_user_id=doctor.user_id)
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_foreign_actor_and_foreign_clinician_are_hidden(
    clinic, make_clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    other = make_clinic(name="CT1B2 Other Clinic")
    foreign_admin = make_staff(other, role=Role.ADMIN)
    foreign_doctor = make_staff(other, role=Role.DOCTOR)
    with pytest.raises(NotFoundError):
        _assign(ids, actor_user_id=foreign_admin.user_id)
    with pytest.raises(NotFoundError):
        _assign(ids, staff_id=foreign_doctor.id)
    with pytest.raises(NotFoundError):
        service.get_primary_assignments(
            admission_id=ids["admission"], clinic_id=other.id,
        )
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_wrong_role_or_inactive_staff_rejected(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    with pytest.raises(ValidationError):
        _assign(ids, staff_id=ids["nurse"], kind=service.PRIMARY_DOCTOR)
    suspended = make_staff(clinic, role=Role.DOCTOR, status=StaffStatus.SUSPENDED)
    with pytest.raises(ValidationError):
        _assign(ids, staff_id=suspended.id)
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_discharged_admission_rejects_mutation(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    admission = db.session.get(Admission, ids["admission"])
    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    with pytest.raises(ConflictError):
        _assign(ids)
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_explicit_end_releases_slot_and_retain_audit_evidence(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    first = _assign(ids)
    first_id = first.id
    ended = service.end_primary_clinician_assignment(
        clinic_id=ids["clinic"],
        admission_id=ids["admission"],
        actor_user_id=ids["admin_user"],
        assignment_kind=service.PRIMARY_DOCTOR,
        expected_current_assignment_id=first_id,
        end_reason="No longer covering",
    )
    assert ended.ended_at is not None
    assert ended.end_reason == "No longer covering"
    assert service.get_primary_assignments(
        admission_id=ids["admission"], clinic_id=ids["clinic"],
    )["primary_doctor"] is None
    another = make_staff(clinic, role=Role.DOCTOR)
    _assign(ids, staff_id=another.id)
    assert db.session.query(AdmissionCareAssignment).count() == 2


def test_invalid_transition_inputs_rejected(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    with pytest.raises(ValidationError):
        _assign(ids, assignment_kind="historical_consultant")
    with pytest.raises(ValidationError):
        _assign(ids, assignment_reason=" ")
    with pytest.raises(ValidationError):
        _assign(ids, expected_current_assignment_id=True)
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_audit_failure_rolls_back_primary_assignment(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    def fail_audit(**_kwargs):
        raise RuntimeError("Audit writer unavailable")
    monkeypatch.setattr(service, "create_audit_log", fail_audit)
    with pytest.raises(RuntimeError, match="Audit writer unavailable"):
        _assign(ids)
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_audit_payload_identifies_exact_actor_patient_and_admission(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    evidence = []
    monkeypatch.setattr(service, "create_audit_log", lambda **kw: evidence.append(kw))
    result = _assign(ids)
    assert len(evidence) == 1
    event = evidence[0]
    assert event["entity_id"] == result.id
    assert event["user_id"] == ids["admin_user"]
    assert event["clinic_id"] == ids["clinic"]
    assert event["patient_id"] == ids["patient"]
    assert event["new_value"]["admission_id"] == ids["admission"]


def test_reassignment_audit_failure_restores_original_owner(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    first = _assign(ids)
    original_id = first.id
    another = make_staff(clinic, role=Role.DOCTOR)
    next_id = another.id

    def fail_audit(**_kwargs):
        raise RuntimeError("Audit write failed during reassignment")

    monkeypatch.setattr(service, "create_audit_log", fail_audit)
    with pytest.raises(RuntimeError, match="Audit write failed during reassignment"):
        _assign(
            ids,
            staff_id=next_id,
            expected_current_assignment_id=original_id,
        )
    active = service.get_primary_assignments(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
    )
    assert active["primary_doctor"].id == original_id
    assert active["primary_doctor"].ended_at is None
    assert db.session.query(AdmissionCareAssignment).count() == 1


def test_end_requires_current_assignment_id_and_rejects_stale_id(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    first = _assign(ids)
    with pytest.raises(ConflictError):
        service.end_primary_clinician_assignment(
            clinic_id=ids["clinic"],
            admission_id=ids["admission"],
            actor_user_id=ids["admin_user"],
            assignment_kind=service.PRIMARY_DOCTOR,
            expected_current_assignment_id=first.id + 100,
            end_reason="Reassigned",
        )
    assert service.get_primary_assignments(
        clinic_id=ids["clinic"], admission_id=ids["admission"],
    )["primary_doctor"].id == first.id
