"""CT4A1: fail-closed responsibility + handoff/chat provenance negative matrix.

No production changes, no migrations, no parallel care-team or chat system.
Uses the committed CT3 admission/handoff fixtures and Flask testing DB.
CT4B will exercise real Redis/worker interruptions; CT4C will exercise
PostgreSQL races.  This slice checks security semantics, not failure chaos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.auth.user.models.user_model import User
from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.services.case_chat_handoff_authorization import (
    require_acknowledged_handoff_sync_authority as require_handoff_authority,
)
from app.modules.chat.services.case_chat_membership_plan import (
    build_case_chat_membership_plan,
)
from app.modules.chat.services import case_chat_assignment_event_service as events
from app.modules.chat.services import case_conversation_creation_service as creation
from app.modules.chat.services import case_chat_membership_sync_service as membership
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services import nursing_service
from app.modules.nursing.services import handoff_primary_transfer_service as transfer
from app.modules.nursing.services import care_team_assignment_service as ownership
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission
from app.tests.modules.nursing.test_handoff_primary_transfer_preconditions import (
    _case as make_admission_handoff,
)

DENIED = (ConflictError, NotFoundError, ValidationError)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def no_external_chat_or_audit(monkeypatch):
    """Only isolate audit transport and after-commit chat, not clinical guards."""
    for module in (nursing_service, transfer, ownership, creation, membership):
        monkeypatch.setattr(module, "create_audit_log", lambda **_kw: None)

    def simulated_chat_outage(**_kw):
        raise RuntimeError("CT4A1 chat dispatch intentionally deferred")

    monkeypatch.setattr(events, "ensure_admission_case_conversation", simulated_chat_outage)


def make_case(clinic, make_patient, make_staff):
    return make_admission_handoff(clinic, make_patient, make_staff)


def acknowledge(ids, **overrides):
    kwargs = {"clinic_id": ids["clinic_id"], "actor_staff_id": ids["incoming_id"]}
    kwargs.update(overrides)
    return nursing_service.acknowledge_handoff(ids["handoff_id"], **kwargs)


def assignment_rows(ids):
    return db.session.execute(
        db.select(AdmissionCareAssignment).where(
            AdmissionCareAssignment.clinic_id == ids["clinic_id"],
            AdmissionCareAssignment.admission_id == ids["admission_id"],
            AdmissionCareAssignment.assignment_kind == "primary_nurse",
        ).order_by(AdmissionCareAssignment.id)
    ).scalars().all()


def request(ids):
    return db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == ids["clinic_id"],
            AdmissionCaseChatSyncRequest.admission_id == ids["admission_id"],
        )
    ).scalar_one_or_none()


def assert_no_transfer_or_grant(ids, *, expected_acknowledged_by=None):
    """Denials do not change prior acknowledgement evidence or clinical ownership."""
    db.session.expire_all()
    rows = assignment_rows(ids)
    assert len(rows) == 1
    assert rows[0].id == ids["assignment_id"]
    assert rows[0].ended_at is None
    assert rows[0].staff_id == ids["outgoing_id"]
    assert request(ids) is None
    assert db.session.query(AdmissionCaseConversation).count() == 0
    assert (
        db.session.get(NursingHandoff, ids["handoff_id"]).acknowledged_by_staff_id
        == expected_acknowledged_by
    )


def assert_pending_nurse_request(ids):
    row = request(ids)
    assert row is not None
    assert row.requested_by_user_id == ids["incoming_user_id"]
    assert row.authorization_handoff_id == ids["handoff_id"]
    assert row.request_sequence == 1 and row.processed_sequence == 0
    rows = assignment_rows(ids)
    assert len(rows) == 2
    assert rows[0].ended_at is not None
    assert rows[1].ended_at is None and rows[1].staff_id == ids["incoming_id"]


def test_ready_handoff_does_not_grant_incoming_chat_or_transfer(
    clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    plan = build_case_chat_membership_plan(
        clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
    )
    outgoing_user = db.session.get(Staff, ids["outgoing_id"]).user_id
    assert plan["eligible_user_ids"] == [outgoing_user]
    assert ids["incoming_user_id"] not in plan["eligible_user_ids"]
    with pytest.raises(NotFoundError):
        require_handoff_authority(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"], handoff_id=ids["handoff_id"],
        )
    assert_no_transfer_or_grant(ids)


@pytest.mark.parametrize("actor", ["outgoing", "doctor", "foreign_nurse"])
def test_unauthorized_acknowledging_staff_fails_closed(
    clinic, make_clinic, make_patient, make_staff, actor,
):
    ids = make_case(clinic, make_patient, make_staff)
    if actor == "outgoing":
        staff_id = ids["outgoing_id"]
    elif actor == "doctor":
        staff_id = ids["doctor_id"]
    else:
        foreign = make_clinic(name="CT4A1 Other Clinic")
        staff_id = make_staff(foreign, role=Role.NURSE).id
    with pytest.raises(DENIED):
        acknowledge(ids, actor_staff_id=staff_id)
    assert_no_transfer_or_grant(ids)


def test_foreign_clinic_id_never_discloses_or_acknowledges(
    clinic, make_clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    other = make_clinic(name="CT4A1 Foreign Admission")
    with pytest.raises(DENIED):
        acknowledge(ids, clinic_id=other.id)
    assert_no_transfer_or_grant(ids)


@pytest.mark.parametrize("status", [
    NursingHandoffStatus.DRAFT,
    NursingHandoffStatus.CANCELLED,
    NursingHandoffStatus.ACKNOWLEDGED,
])
def test_non_ready_status_never_transfers_or_enqueues(
    clinic, make_patient, make_staff, status,
):
    ids = make_case(clinic, make_patient, make_staff)
    handoff = db.session.get(NursingHandoff, ids["handoff_id"])
    handoff.status = status
    if status == NursingHandoffStatus.CANCELLED:
        handoff.cancelled_at = utcnow()
    elif status == NursingHandoffStatus.ACKNOWLEDGED:
        # Use a schema-valid acknowledged row. The test deliberately keeps
        # clinical ownership unchanged to prove acknowledgement cannot be replayed.
        handoff.acknowledged_at = utcnow()
        handoff.acknowledged_by_staff_id = ids["incoming_id"]
    db.session.commit()
    with pytest.raises(DENIED):
        acknowledge(ids)
    assert_no_transfer_or_grant(
        ids,
        expected_acknowledged_by=(
            ids["incoming_id"]
            if status == NursingHandoffStatus.ACKNOWLEDGED
            else None
        ),
    )


@pytest.mark.parametrize("tamper", ["missing_ready_at", "premature_ack_time", "premature_ack_actor"])
def test_forged_ready_metadata_cannot_trigger_primary_transfer(
    clinic, make_patient, make_staff, tamper,
):
    ids = make_case(clinic, make_patient, make_staff)
    handoff = db.session.get(NursingHandoff, ids["handoff_id"])
    if tamper == "missing_ready_at":
        # READY without ready_at is forbidden by a database CHECK constraint.
        # Assert the database rejects it rather than expecting a service denial
        # for a state that cannot be persisted through normal writes.
        handoff.ready_at = None
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert db.session.get(NursingHandoff, ids["handoff_id"]).ready_at is not None
        assert_no_transfer_or_grant(ids)
        return
    elif tamper == "premature_ack_time":
        handoff.acknowledged_at = utcnow()
    else:
        handoff.acknowledged_by_staff_id = ids["incoming_id"]
    db.session.commit()
    with pytest.raises(DENIED):
        acknowledge(ids)
    assert len(assignment_rows(ids)) == 1
    assert assignment_rows(ids)[0].ended_at is None
    assert request(ids) is None


@pytest.mark.parametrize("status", [
    StaffStatus.ON_LEAVE, StaffStatus.SUSPENDED, StaffStatus.TERMINATED,
])
def test_non_active_incoming_staff_never_claims_primary(
    clinic, make_patient, make_staff, status,
):
    ids = make_case(clinic, make_patient, make_staff)
    db.session.get(Staff, ids["incoming_id"]).status = status
    db.session.commit()
    with pytest.raises(DENIED):
        acknowledge(ids)
    assert_no_transfer_or_grant(ids)


@pytest.mark.parametrize("mutation", ["disabled", "doctor_role"])
def test_incoming_account_must_be_active_linked_nurse(
    clinic, make_patient, make_staff, mutation,
):
    ids = make_case(clinic, make_patient, make_staff)
    user = db.session.get(User, ids["incoming_user_id"])
    if mutation == "disabled":
        user.is_active = False
    else:
        user.role = Role.DOCTOR
    db.session.commit()
    with pytest.raises(DENIED):
        acknowledge(ids)
    assert_no_transfer_or_grant(ids)


@pytest.mark.parametrize("status", [
    AdmissionStatus.DISCHARGED, AdmissionStatus.TRANSFERRED,
])
def test_non_active_admission_cannot_accept_handoff(
    clinic, make_patient, make_staff, status,
):
    ids = make_case(clinic, make_patient, make_staff)
    db.session.get(Admission, ids["admission_id"]).status = status
    db.session.commit()
    with pytest.raises(DENIED):
        acknowledge(ids)
    assert_no_transfer_or_grant(ids)


@pytest.mark.parametrize("mutation", ["removed", "ended", "different_owner"])
def test_primary_assignment_must_be_explicit_active_and_matching(
    clinic, make_patient, make_staff, mutation,
):
    ids = make_case(clinic, make_patient, make_staff)
    row = db.session.get(AdmissionCareAssignment, ids["assignment_id"])
    if mutation == "removed":
        db.session.delete(row)
    elif mutation == "ended":
        row.ended_at = utcnow()
        row.ended_by_user_id = row.assigned_by_user_id
        row.end_reason = "CT4A1 ended before handoff"
    else:
        alternate = make_staff(clinic, role=Role.NURSE)
        row.staff_id = alternate.id
    db.session.commit()
    with pytest.raises(DENIED):
        acknowledge(ids)
    handoff = db.session.get(NursingHandoff, ids["handoff_id"])
    assert handoff.status == NursingHandoffStatus.READY
    assert handoff.acknowledged_by_staff_id is None
    assert request(ids) is None


def test_cross_patient_handoff_evidence_is_not_transfer_authority(
    clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    other_patient = make_patient(clinic)
    handoff = db.session.get(NursingHandoff, ids["handoff_id"])
    handoff.patient_id = other_patient.id
    db.session.commit()
    with pytest.raises(DENIED):
        acknowledge(ids)
    assert_no_transfer_or_grant(ids)


def test_wrong_idempotency_user_cannot_impersonate_the_incoming_nurse(
    clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    outgoing_user = db.session.get(Staff, ids["outgoing_id"]).user_id
    with pytest.raises(DENIED):
        acknowledge(
            ids, idempotency_key="ct4a1-spoofed-user",
            idempotency_user_id=outgoing_user,
        )
    assert_no_transfer_or_grant(ids)


def test_out_of_order_second_ready_handoff_cannot_reclaim_old_primary(
    clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    first = acknowledge(ids)
    assert first.status == NursingHandoffStatus.ACKNOWLEDGED
    assert_pending_nurse_request(ids)
    now = utcnow()
    second = NursingHandoff(
        clinic_id=ids["clinic_id"], patient_id=ids["patient_id"],
        admission_id=ids["admission_id"],
        outgoing_staff_id=ids["outgoing_id"],
        incoming_staff_id=ids["incoming_id"],
        status=NursingHandoffStatus.READY, ready_at=now,
        shift_start_at=now, shift_end_at=now + timedelta(hours=8),
        version=2, situation="Delayed", assessment="Stale",
        recommendation="Do not overwrite current primary nurse",
    )
    db.session.add(second)
    db.session.commit()
    with pytest.raises(DENIED):
        nursing_service.acknowledge_handoff(
            second.id, clinic_id=ids["clinic_id"], actor_staff_id=ids["incoming_id"],
        )
    assert db.session.get(NursingHandoff, second.id).status == NursingHandoffStatus.READY
    assert_pending_nurse_request(ids)


def test_duplicate_ack_does_not_requeue_or_create_third_assignment(
    clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    acknowledge(ids)
    with pytest.raises(ConflictError):
        acknowledge(ids)
    assert_pending_nurse_request(ids)


@pytest.mark.parametrize("mutation", [
    "user_disabled", "staff_suspended", "role_changed", "handoff_unacknowledged",
    "assignment_owner_changed", "assignment_reason_forged", "admission_transferred",
])
def test_completed_handoff_retry_revalidates_live_authority(
    clinic, make_patient, make_staff, mutation,
):
    ids = make_case(clinic, make_patient, make_staff)
    acknowledge(ids)
    assert_pending_nurse_request(ids)
    user = db.session.get(User, ids["incoming_user_id"])
    staff = db.session.get(Staff, ids["incoming_id"])
    handoff = db.session.get(NursingHandoff, ids["handoff_id"])
    current = assignment_rows(ids)[1]
    admission = db.session.get(Admission, ids["admission_id"])
    if mutation == "user_disabled":
        user.is_active = False
    elif mutation == "staff_suspended":
        staff.status = StaffStatus.SUSPENDED
    elif mutation == "role_changed":
        user.role = Role.DOCTOR
    elif mutation == "handoff_unacknowledged":
        handoff.status = NursingHandoffStatus.READY
    elif mutation == "assignment_owner_changed":
        alternate = make_staff(clinic, role=Role.NURSE)
        current.staff_id = alternate.id
    elif mutation == "assignment_reason_forged":
        current.assignment_reason = "CT4A1 deliberately invalid provenance"
    else:
        admission.status = AdmissionStatus.TRANSFERRED
    db.session.commit()
    # A transferred admission fails _get_admission(require_active=True) with
    # ConflictError. Other invalid credentials/provenance fail with NotFoundError.
    # Both are fail-closed; do not loosen production authorization or accept
    # a generic exception that could hide a service regression.
    expected_denial = (
        ConflictError if mutation == "admission_transferred" else NotFoundError
    )
    with pytest.raises(expected_denial):
        require_handoff_authority(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"], handoff_id=ids["handoff_id"],
        )
    row = request(ids)
    assert row.request_sequence == 1 and row.processed_sequence == 0


@pytest.mark.parametrize("bad_id", [True, False, 0, -17, "1", None])
def test_forged_handoff_provenance_identifier_is_not_authority(
    clinic, make_patient, make_staff, bad_id,
):
    ids = make_case(clinic, make_patient, make_staff)
    acknowledge(ids)
    with pytest.raises(NotFoundError):
        require_handoff_authority(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"], handoff_id=bad_id,
        )
    assert_pending_nurse_request(ids)


def test_outgoing_user_cannot_replay_incoming_handoff_authorization(
    clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    acknowledge(ids)
    old_user = db.session.get(Staff, ids["outgoing_id"]).user_id
    with pytest.raises(NotFoundError):
        require_handoff_authority(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=old_user, handoff_id=ids["handoff_id"],
        )
    assert_pending_nurse_request(ids)


def test_admin_cannot_consume_nurse_originated_sync_request(
    clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    acknowledge(ids)
    admin = make_staff(clinic, role=Role.ADMIN)
    with pytest.raises(NotFoundError):
        events.deliver_pending_case_chat_sync(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=admin.user_id,
        )
    assert_pending_nurse_request(ids)


def test_forged_queue_actor_cannot_turn_nurse_authority_into_admin_authority(
    clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    acknowledge(ids)
    admin = make_staff(clinic, role=Role.ADMIN)
    row = request(ids)
    row.requested_by_user_id = admin.user_id
    db.session.commit()
    with pytest.raises(NotFoundError):
        events.deliver_pending_case_chat_sync(
            clinic_id=ids["clinic_id"], admission_id=ids["admission_id"],
            actor_user_id=admin.user_id,
        )
    assert request(ids).processed_sequence == 0
    assert request(ids).authorization_handoff_id == ids["handoff_id"]


def test_foreign_clinic_id_cannot_consume_acknowledged_handoff_request(
    clinic, make_clinic, make_patient, make_staff,
):
    ids = make_case(clinic, make_patient, make_staff)
    acknowledge(ids)
    foreign = make_clinic(name="CT4A1 request isolation")
    with pytest.raises(NotFoundError):
        events.deliver_pending_case_chat_sync(
            clinic_id=foreign.id, admission_id=ids["admission_id"],
            actor_user_id=ids["incoming_user_id"],
        )
    assert_pending_nurse_request(ids)
