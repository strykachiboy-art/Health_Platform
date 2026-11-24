"""CT3B focused: acknowledgement and primary nurse ownership are one transaction."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services import nursing_service
from app.modules.nursing.services import handoff_primary_transfer_service
from app.modules.nursing.services.nursing_service import acknowledge_handoff
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission
from app.tests.modules.nursing.test_handoff_primary_transfer_preconditions import (
    _case,
)


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch):
    monkeypatch.setattr(nursing_service, "create_audit_log", lambda **_kw: None)
    monkeypatch.setattr(handoff_primary_transfer_service, "create_audit_log", lambda **_kw: None)


def _ack(ids, **kw):
    arguments = {
        "clinic_id": ids["clinic_id"],
        "actor_staff_id": ids["incoming_id"],
    }
    arguments.update(kw)
    return acknowledge_handoff(ids["handoff_id"], **arguments)


def _rows(ids):
    return db.session.execute(
        db.select(AdmissionCareAssignment).where(
            AdmissionCareAssignment.clinic_id == ids["clinic_id"],
            AdmissionCareAssignment.admission_id == ids["admission_id"],
            AdmissionCareAssignment.assignment_kind == "primary_nurse",
        ).order_by(AdmissionCareAssignment.id)
    ).scalars().all()


def test_acknowledgement_atomically_transfers_primary_nurse(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    result = _ack(ids)
    rows = _rows(ids)
    assert result.status == NursingHandoffStatus.ACKNOWLEDGED
    assert result.acknowledged_by_staff_id == ids["incoming_id"]
    assert result.acknowledged_at is not None
    assert len(rows) == 2
    old, new = rows
    assert old.id == ids["assignment_id"]
    assert old.staff_id == ids["outgoing_id"]
    assert old.ended_at is not None
    assert old.ended_at >= old.assigned_at
    assert old.ended_by_user_id == ids["incoming_user_id"]
    assert str(ids["handoff_id"]) in old.end_reason
    assert new.staff_id == ids["incoming_id"]
    assert new.assigned_by_user_id == ids["incoming_user_id"]
    assert new.assigned_at == old.ended_at
    assert new.ended_at is None
    assert str(ids["handoff_id"]) in new.assignment_reason


def test_ready_state_alone_never_transfers(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    row = _rows(ids)
    assert len(row) == 1 and row[0].staff_id == ids["outgoing_id"]
    assert db.session.get(NursingHandoff, ids["handoff_id"]).status == NursingHandoffStatus.READY


def test_second_acknowledgement_is_conflict_not_double_transfer(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    _ack(ids)
    with pytest.raises(ConflictError):
        _ack(ids)
    assert len(_rows(ids)) == 2


def test_same_idempotency_key_replays_without_extra_assignment(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    args = {"idempotency_key": "ct3b-ack-once", "idempotency_user_id": ids["incoming_user_id"]}
    first = _ack(ids, **args)
    second = _ack(ids, **args)
    assert second.id == first.id
    assert len(_rows(ids)) == 2


def test_wrong_nurse_cannot_acknowledge_or_transfer(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    with pytest.raises(ConflictError):
        _ack(ids, actor_staff_id=ids["outgoing_id"])
    assert len(_rows(ids)) == 1
    assert db.session.get(NursingHandoff, ids["handoff_id"]).status == NursingHandoffStatus.READY


def test_missing_primary_assignment_rolls_back_acknowledgement(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    db.session.delete(db.session.get(AdmissionCareAssignment, ids["assignment_id"]))
    db.session.commit()
    with pytest.raises(ConflictError):
        _ack(ids)
    assert db.session.get(NursingHandoff, ids["handoff_id"]).status == NursingHandoffStatus.READY


def test_reassigned_outgoing_nurse_causes_conflict(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    alternate = make_staff(clinic, role=Role.NURSE)
    current = db.session.get(AdmissionCareAssignment, ids["assignment_id"])
    current.staff_id = alternate.id
    db.session.commit()
    with pytest.raises(ConflictError):
        _ack(ids)
    assert len(_rows(ids)) == 1
    assert db.session.get(NursingHandoff, ids["handoff_id"]).status == NursingHandoffStatus.READY


def test_discharged_admission_cannot_transfer(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    admission = db.session.get(Admission, ids["admission_id"])
    admission.status = AdmissionStatus.DISCHARGED
    db.session.commit()
    with pytest.raises(ConflictError):
        _ack(ids)
    assert len(_rows(ids)) == 1


def test_inactive_incoming_nurse_cannot_acknowledge(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    incoming = db.session.get(Staff, ids["incoming_id"])
    incoming.status = StaffStatus.SUSPENDED
    db.session.commit()
    with pytest.raises(ValidationError):
        _ack(ids)
    assert len(_rows(ids)) == 1


def test_foreign_clinic_is_not_found(clinic, make_clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    other = make_clinic(name="CT3B foreign clinic")
    with pytest.raises(NotFoundError):
        _ack(ids, clinic_id=other.id)
    assert len(_rows(ids)) == 1


def test_successor_insert_failure_rolls_back_predecessor_and_handoff(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    original = handoff_primary_transfer_service.create_audit_log

    def reject_audit(**_kwargs):
        raise RuntimeError("simulated transfer audit database outage")

    monkeypatch.setattr(handoff_primary_transfer_service, "create_audit_log", reject_audit)
    with pytest.raises(RuntimeError):
        _ack(ids)
    assert len(_rows(ids)) == 1
    assert _rows(ids)[0].ended_at is None
    assert db.session.get(NursingHandoff, ids["handoff_id"]).status == NursingHandoffStatus.READY
    monkeypatch.setattr(handoff_primary_transfer_service, "create_audit_log", original)


def test_handoff_audit_failure_rolls_back_transfer(clinic, make_patient, make_staff, monkeypatch):
    ids = _case(clinic, make_patient, make_staff)

    def reject_audit(**_kwargs):
        raise RuntimeError("simulated handoff audit failure")

    monkeypatch.setattr(nursing_service, "create_audit_log", reject_audit)
    with pytest.raises(RuntimeError):
        _ack(ids)
    assert len(_rows(ids)) == 1
    assert _rows(ids)[0].ended_at is None
    assert db.session.get(NursingHandoff, ids["handoff_id"]).status == NursingHandoffStatus.READY


def test_does_not_update_doctor_assignment(clinic, make_patient, make_staff):
    ids = _case(clinic, make_patient, make_staff)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor_row = AdmissionCareAssignment(
        clinic_id=ids["clinic_id"], patient_id=ids["patient_id"],
        admission_id=ids["admission_id"], staff_id=doctor.id,
        assigned_by_user_id=admin.user_id, assignment_kind="primary_doctor",
        assignment_reason="doctor remains", assigned_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.session.add(doctor_row)
    db.session.commit()
    _ack(ids)
    assert db.session.get(AdmissionCareAssignment, doctor_row.id).ended_at is None
    assert len(_rows(ids)) == 2


def test_acknowledged_handoff_writes_durable_nurse_chat_request(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _ack(ids)
    request = db.session.query(AdmissionCaseChatSyncRequest).one()
    assert request.request_sequence == 1
    assert request.requested_by_user_id == ids["incoming_user_id"]
    assert request.authorization_handoff_id == ids["handoff_id"]
