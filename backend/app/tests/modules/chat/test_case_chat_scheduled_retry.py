"""CT2D1 focused-only: durable retry worker and authorization safety."""
from datetime import datetime, timezone

import pytest

from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.extensions import celery, db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.services import case_chat_assignment_event_service as events
from app.modules.chat.services import case_conversation_creation_service as creation
from app.modules.chat.services import case_chat_membership_sync_service as sync
from app.modules.chat.tasks import case_chat_sync_task as worker
from app.modules.nursing.services import care_team_assignment_service as assignment
from app.modules.ward.models.ward_model import Admission, Bed, Ward


@pytest.fixture(autouse=True)
def capture_audit(monkeypatch):
    monkeypatch.setattr(assignment, "create_audit_log", lambda **_kw: None)
    monkeypatch.setattr(creation, "create_audit_log", lambda **_kw: None)
    monkeypatch.setattr(sync, "create_audit_log", lambda **_kw: None)


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    ward = Ward(clinic_id=clinic.id, name="CT2D1 Ward", ward_type=WardType.GENERAL, capacity=4)
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number="CT2D1-1", status=BedStatus.OCCUPIED)
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
        "clinic": clinic.id, "admin": admin.user_id,
        "doctor": doctor.id, "admission": admission.id,
    }
    db.session.commit()
    return ids


def _assign(ids, **overrides):
    args = {
        "clinic_id": ids["clinic"], "admission_id": ids["admission"],
        "actor_user_id": ids["admin"], "staff_id": ids["doctor"],
        "assignment_kind": "primary_doctor", "assignment_reason": "CT2D1 coverage",
    }
    args.update(overrides)
    return assignment.assign_primary_clinician(**args)


def _request(ids):
    return db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == ids["clinic"],
            AdmissionCaseChatSyncRequest.admission_id == ids["admission"],
        )
    ).scalar_one()


def _defer_create(monkeypatch):
    original = events.ensure_admission_case_conversation
    def unavailable(**_kw):
        raise RuntimeError("Chat unavailable")
    monkeypatch.setattr(events, "ensure_admission_case_conversation", unavailable)
    return original


def test_clinical_write_records_exact_authorized_request_actor(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer_create(monkeypatch)
    assigned = _assign(ids)
    assert assigned.id > 0
    row = _request(ids)
    assert row.requested_by_user_id == ids["admin"]
    assert (row.request_sequence, row.processed_sequence) == (1, 0)


def test_scheduled_retry_delivers_then_acknowledges(
    clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    original = _defer_create(monkeypatch)
    _assign(ids)
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    result = worker.process_pending_case_chat_sync.run(limit=10)
    assert result == {"claimed": 1, "processed": 1, "failed": 0, "still_pending": 0}
    row = _request(ids)
    assert row.request_sequence == row.processed_sequence == 1
    assert db.session.query(AdmissionCaseConversation).count() == 1


def test_repeat_scheduled_retry_is_a_noop(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    _assign(ids)
    assert _request(ids).request_sequence == _request(ids).processed_sequence
    result = worker.process_pending_case_chat_sync.run(limit=10)
    assert result == {"claimed": 0, "processed": 0, "failed": 0, "still_pending": 0}
    assert db.session.query(AdmissionCaseConversation).count() == 1


def test_worker_chat_failure_does_not_rollback_clinical_assignment(
    clinic, make_patient, make_staff, monkeypatch,
):
    from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
    ids = _case(clinic, make_patient, make_staff)
    _defer_create(monkeypatch)
    row = _assign(ids)
    outcome = worker.process_pending_case_chat_sync.run(limit=1)
    assert outcome == {"claimed": 1, "processed": 0, "failed": 1, "still_pending": 0}
    assert db.session.get(AdmissionCareAssignment, row.id) is not None
    assert _request(ids).processed_sequence == 0
    assert db.session.query(AdmissionCaseConversation).count() == 0


def test_revoked_admin_cannot_be_implicitly_replaced_by_worker(
    clinic, make_patient, make_staff, monkeypatch,
):
    from app.core.auth.user.models.user_model import User
    ids = _case(clinic, make_patient, make_staff)
    _defer_create(monkeypatch)
    _assign(ids)
    user = db.session.get(User, ids["admin"])
    user.is_active = False
    db.session.commit()
    result = worker.process_pending_case_chat_sync.run(limit=1)
    assert result["failed"] == 1
    assert _request(ids).processed_sequence == 0
    assert db.session.query(AdmissionCaseConversation).count() == 0


def test_foreign_actor_in_queued_row_is_rejected_at_worker_boundary(
    clinic, make_clinic, make_patient, make_staff, monkeypatch,
):
    ids = _case(clinic, make_patient, make_staff)
    _defer_create(monkeypatch)
    _assign(ids)
    foreign = make_clinic(name="CT2D1 Foreign")
    other_admin = make_staff(foreign, role=Role.ADMIN)
    row = _request(ids)
    row.requested_by_user_id = other_admin.user_id
    db.session.commit()
    result = worker.process_pending_case_chat_sync.run(limit=1)
    assert result["failed"] == 1
    assert _request(ids).processed_sequence == 0
    assert db.session.query(AdmissionCaseConversation).count() == 0


def test_worker_rejects_invalid_limits_without_mutation(
    clinic, make_patient, make_staff,
):
    ids = _case(clinic, make_patient, make_staff)
    for value in (0, -1, True, 101, "1"):
        with pytest.raises(ValueError):
            worker.process_pending_case_chat_sync.run(limit=value)
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 0


def test_beat_configuration_and_task_name_are_registered(app):
    schedule = celery.conf.beat_schedule
    entry = schedule["process-pending-case-chat-sync-every-minute"]
    assert entry["task"] == "process_pending_case_chat_sync"
    assert entry["schedule"] == 60.0
    assert worker.process_pending_case_chat_sync.name == "process_pending_case_chat_sync"


def test_worker_failure_isolated_and_next_request_is_attempted(
    clinic, make_clinic, make_patient, make_staff, monkeypatch,
):
    first = _case(clinic, make_patient, make_staff)
    second_clinic = make_clinic(name="CT2D1 Next")
    second = _case(second_clinic, make_patient, make_staff)
    original = _defer_create(monkeypatch)
    _assign(first)
    _assign(second)
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    original_delivery = worker.deliver_pending_case_chat_sync
    def first_fails(**kw):
        if kw["clinic_id"] == first["clinic"]:
            raise RuntimeError("Temporary failure")
        return original_delivery(**kw)
    monkeypatch.setattr(worker, "deliver_pending_case_chat_sync", first_fails)
    result = worker.process_pending_case_chat_sync.run(limit=10)
    assert result["claimed"] == 2
    assert result["processed"] == 1
    assert result["failed"] == 1
    assert _request(first).processed_sequence == 0
    assert _request(second).processed_sequence == 1


def test_worker_limit_bounds_processing_without_acknowledging_other_request(
    clinic, make_clinic, make_patient, make_staff, monkeypatch,
):
    first = _case(clinic, make_patient, make_staff)
    second_clinic = make_clinic(name="CT2D1 Bounded")
    second = _case(second_clinic, make_patient, make_staff)
    original = _defer_create(monkeypatch)
    _assign(first)
    _assign(second)
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original)
    result = worker.process_pending_case_chat_sync.run(limit=1)
    assert result["claimed"] == 1
    assert _request(first).processed_sequence + _request(second).processed_sequence == 1
    assert db.session.query(AdmissionCaseChatSyncRequest).count() == 2
