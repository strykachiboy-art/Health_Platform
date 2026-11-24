"""CT3A: evidence preconditions for nurse-to-nurse primary ownership transfer."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services.handoff_primary_transfer_preconditions import (
    get_handoff_primary_transfer_preconditions as preflight,
)
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.modules.staff.models.staff_model import Staff
from app.core.auth.user.models.user_model import User


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    outgoing = make_staff(clinic, role=Role.NURSE)
    incoming = make_staff(clinic, role=Role.NURSE)
    ward = Ward(
        clinic_id=clinic.id, name="CT3A Ward", ward_type=WardType.GENERAL,
        capacity=4,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number="CT3A-01", status=BedStatus.OCCUPIED)
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id, bed_id=bed.id,
        admitted_by_id=doctor.id, status=AdmissionStatus.ADMITTED,
        admitted_at=_utcnow(),
    )
    db.session.add(admission)
    db.session.flush()
    assignment = AdmissionCareAssignment(
        clinic_id=clinic.id, patient_id=patient.id, admission_id=admission.id,
        staff_id=outgoing.id, assignment_kind="primary_nurse",
        assigned_by_user_id=admin.user_id,
        assignment_reason="CT3A initial clinical allocation", assigned_at=_utcnow(),
    )
    db.session.add(assignment)
    now = _utcnow()
    handoff = NursingHandoff(
        clinic_id=clinic.id, patient_id=patient.id, admission_id=admission.id,
        outgoing_staff_id=outgoing.id, incoming_staff_id=incoming.id,
        status=NursingHandoffStatus.READY, ready_at=now,
        shift_start_at=now, shift_end_at=now + timedelta(hours=8), version=2,
        situation="Stable", assessment="Monitoring", recommendation="Continue",
    )
    db.session.add(handoff)
    db.session.commit()
    return {
        "clinic_id": clinic.id, "patient_id": patient.id, "admission_id": admission.id,
        "handoff_id": handoff.id, "assignment_id": assignment.id,
        "doctor_id": doctor.id, "outgoing_id": outgoing.id,
        "incoming_id": incoming.id, "incoming_user_id": incoming.user_id,
    }


def _check(ids, **kwargs):
    return preflight(
        handoff_id=kwargs.get("handoff_id", ids["handoff_id"]),
        clinic_id=kwargs.get("clinic_id", ids["clinic_id"]),
        incoming_staff_id=kwargs.get("incoming_staff_id", ids["incoming_id"]),
    )


def test_ready_handoff_is_eligible_but_does_not_transfer(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    result = _check(ids)
    assert result["handoff_status"] == NursingHandoffStatus.READY.value
    assert result["eligible_for_acknowledged_transfer"] is True
    assert result["ownership_transferred"] is False
    assert result["chat_membership_changed"] is False
    assert result["current_primary_nurse_assignment_id"] == ids["assignment_id"]
    assert result["outgoing_primary_nurse_staff_id"] == ids["outgoing_id"]
    assert result["incoming_primary_nurse_staff_id"] == ids["incoming_id"]
    assert result["incoming_nurse_user_id"] == ids["incoming_user_id"]


def test_preflight_is_read_only_and_repeatable(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    first = _check(ids)
    second = _check(ids)
    assert first == second
    assert db.session.query(AdmissionCareAssignment).count() == 1
    row = db.session.get(AdmissionCareAssignment, ids["assignment_id"])
    handoff = db.session.get(NursingHandoff, ids["handoff_id"])
    assert row.staff_id == ids["outgoing_id"] and row.ended_at is None
    assert handoff.status == NursingHandoffStatus.READY
    assert handoff.acknowledged_at is None


def test_foreign_clinic_cannot_inspect_handoff(clinic, make_clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    other = make_clinic(name="CT3A Foreign")
    with pytest.raises(NotFoundError):
        _check(ids, clinic_id=other.id)


def test_missing_handoff_is_not_found(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    with pytest.raises(NotFoundError):
        _check(ids, handoff_id=ids["handoff_id"] + 100000)


@pytest.mark.parametrize("status", [
    NursingHandoffStatus.DRAFT,
    NursingHandoffStatus.ACKNOWLEDGED,
    NursingHandoffStatus.CANCELLED,
])
def test_only_ready_handoff_can_pass(clinic, make_patient, make_staff, status):
    ids = _case(clinic, make_patient, make_staff)
    handoff = db.session.get(NursingHandoff, ids["handoff_id"])
    handoff.status = status
    if status == NursingHandoffStatus.ACKNOWLEDGED:
        handoff.acknowledged_at = _utcnow()
        handoff.acknowledged_by_staff_id = ids["incoming_id"]
    if status == NursingHandoffStatus.CANCELLED:
        handoff.cancelled_at = _utcnow()
    db.session.commit()
    with pytest.raises(ConflictError):
        _check(ids)


def test_wrong_incoming_nurse_cannot_claim_handoff(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    with pytest.raises(ConflictError):
        _check(ids, incoming_staff_id=ids["outgoing_id"])


def test_outgoing_must_be_current_primary_nurse(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    other = make_staff(clinic, role=Role.NURSE)
    row = db.session.get(AdmissionCareAssignment, ids["assignment_id"])
    row.staff_id = other.id
    db.session.commit()
    with pytest.raises(ConflictError):
        _check(ids)


def test_no_explicit_primary_nurse_means_no_transfer(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    db.session.delete(db.session.get(AdmissionCareAssignment, ids["assignment_id"]))
    db.session.commit()
    with pytest.raises(ConflictError):
        _check(ids)


def test_ended_primary_assignment_cannot_be_used(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    row = db.session.get(AdmissionCareAssignment, ids["assignment_id"])
    row.ended_at = _utcnow()
    row.ended_by_user_id = row.assigned_by_user_id
    row.end_reason = "Ended"
    db.session.commit()
    with pytest.raises(ConflictError):
        _check(ids)


def test_discharge_blocks_transfer(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    admission = db.session.get(Admission, ids["admission_id"])
    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = _utcnow()
    db.session.commit()
    with pytest.raises(ConflictError):
        _check(ids)


def test_suspended_incoming_nurse_rejected(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    nurse = db.session.get(Staff, ids["incoming_id"])
    nurse.status = StaffStatus.SUSPENDED
    db.session.commit()
    with pytest.raises(ValidationError):
        _check(ids)


def test_inactive_incoming_user_rejected(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    user = db.session.get(User, ids["incoming_user_id"])
    user.is_active = False
    db.session.commit()
    with pytest.raises(ValidationError):
        _check(ids)


def test_wrong_role_incoming_rejected(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    user = db.session.get(User, ids["incoming_user_id"])
    user.role = Role.DOCTOR
    db.session.commit()
    with pytest.raises(ValidationError):
        _check(ids)


def test_foreign_clinic_user_link_rejected(clinic, make_clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    foreign = make_clinic(name="CT3A Foreign User Clinic")
    user = db.session.get(User, ids["incoming_user_id"])
    user.clinic_id = foreign.id
    db.session.commit()
    with pytest.raises(ValidationError):
        _check(ids)


def test_doctor_assignment_is_not_treated_as_primary_nurse(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    row = db.session.get(AdmissionCareAssignment, ids["assignment_id"])
    row.assignment_kind = "primary_doctor"
    db.session.commit()
    with pytest.raises(ConflictError):
        _check(ids)
