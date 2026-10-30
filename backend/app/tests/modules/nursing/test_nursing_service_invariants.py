from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskPriority,
    NursingCareTaskStatus,
    NursingHandoffStatus,
    NursingNoteStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import (
    AdmissionStatus,
    BedStatus,
    WardType,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.nursing.services import nursing_service
from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    Ward,
)


def _db_now():
    return datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )


def _setup_admission(
    *,
    clinic,
    make_patient,
    make_staff,
):
    patient = make_patient(
        clinic=clinic,
    )
    nurse = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )
    doctor = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    ward = Ward(
        clinic_id=clinic.id,
        name="Gate 8 Nursing Ward",
        ward_type=WardType.GENERAL,
        capacity=5,
    )
    db.session.add(
        ward
    )
    db.session.flush()

    bed = Bed(
        ward_id=ward.id,
        bed_number="N-001",
        status=BedStatus.OCCUPIED,
    )
    db.session.add(
        bed
    )
    db.session.flush()

    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=doctor.id,
        status=AdmissionStatus.ADMITTED,
        reason="Gate 8 nursing test",
        admitted_at=_db_now(),
    )
    db.session.add(
        admission
    )
    db.session.commit()

    return (
        patient,
        nurse,
        doctor,
        ward,
        bed,
        admission,
    )


def _silence_audit(
    monkeypatch,
):
    monkeypatch.setattr(
        nursing_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )


def _plan(
    *,
    monkeypatch,
    clinic,
    admission,
    nurse,
):
    _silence_audit(
        monkeypatch
    )

    return nursing_service.create_care_plan(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        title="Mobility support",
        problem="Reduced mobility",
        goal="Ambulate safely",
    )


def test_create_care_plan_derives_patient_from_active_admission(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = nursing_service.create_care_plan(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        title="Fall prevention",
        problem="High fall risk",
        goal="No inpatient falls",
    )

    assert plan.patient_id == patient.id
    assert plan.admission_id == admission.id
    assert plan.clinic_id == clinic.id
    assert (
        plan.created_by_staff_id
        == nurse.id
    )
    assert (
        plan.status
        == NursingCarePlanStatus.ACTIVE
    )
    assert plan.version == 1


def test_cross_clinic_admission_is_not_visible(
    monkeypatch,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        _patient,
        _nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    other = make_clinic(
        name="Gate 8 Other Clinic",
    )
    other_nurse = make_staff(
        clinic=other,
        role=Role.NURSE,
    )

    with pytest.raises(
        NotFoundError,
    ):
        nursing_service.create_care_plan(
            admission_id=admission.id,
            clinic_id=other.id,
            actor_staff_id=other_nurse.id,
            title="Wrong tenant",
            problem="Wrong tenant",
            goal="Must fail",
        )


def test_discharged_admission_blocks_new_care_plan(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    admission.status = (
        AdmissionStatus.DISCHARGED
    )
    admission.discharged_at = _db_now()
    db.session.commit()

    with pytest.raises(
        ConflictError,
    ):
        nursing_service.create_care_plan(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            title="Closed admission",
            problem="Closed",
            goal="Must fail",
        )


def test_non_nurse_actor_is_rejected(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        _patient,
        _nurse,
        doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(
        ValidationError,
    ):
        nursing_service.create_care_plan(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=doctor.id,
            title="Wrong role",
            problem="Wrong role",
            goal="Must fail",
        )


def test_inactive_nurse_actor_is_rejected(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    nurse.status = (
        StaffStatus.SUSPENDED
    )
    db.session.commit()

    with pytest.raises(
        ValidationError,
    ):
        nursing_service.create_care_plan(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            title="Inactive",
            problem="Inactive",
            goal="Must fail",
        )


def test_care_plan_hold_resume_complete_lifecycle(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = _plan(
        monkeypatch=monkeypatch,
        clinic=clinic,
        admission=admission,
        nurse=nurse,
    )

    held = nursing_service.hold_care_plan(
        plan.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
    )

    assert (
        held.status
        == NursingCarePlanStatus.ON_HOLD
    )

    resumed = nursing_service.resume_care_plan(
        plan.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
    )

    assert (
        resumed.status
        == NursingCarePlanStatus.ACTIVE
    )

    completed = (
        nursing_service
        .complete_care_plan(
            plan.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
        )
    )

    assert (
        completed.status
        == NursingCarePlanStatus.COMPLETED
    )
    assert (
        completed.completed_at
        is not None
    )
    assert completed.version == 4


def test_care_plan_cannot_complete_with_open_task(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = _plan(
        monkeypatch=monkeypatch,
        clinic=clinic,
        admission=admission,
        nurse=nurse,
    )

    nursing_service.create_care_task(
        care_plan_id=plan.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        description="Assist ambulation",
    )

    with pytest.raises(
        ConflictError,
    ):
        nursing_service.complete_care_plan(
            plan.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
        )


def test_create_task_derives_context_and_validates_assignee(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    (
        patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    assignee = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    plan = _plan(
        monkeypatch=monkeypatch,
        clinic=clinic,
        admission=admission,
        nurse=nurse,
    )

    task = nursing_service.create_care_task(
        care_plan_id=plan.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        description="Reposition patient",
        priority=(
            NursingCareTaskPriority.HIGH
        ),
        assigned_to_staff_id=assignee.id,
    )

    assert task.patient_id == patient.id
    assert task.admission_id == admission.id
    assert task.clinic_id == clinic.id
    assert task.care_plan_id == plan.id
    assert (
        task.assigned_to_staff_id
        == assignee.id
    )


def test_cross_clinic_task_assignee_is_rejected(
    monkeypatch,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
):
    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = _plan(
        monkeypatch=monkeypatch,
        clinic=clinic,
        admission=admission,
        nurse=nurse,
    )

    other = make_clinic(
        name="Gate 8 Task Other Clinic",
    )
    foreign_nurse = make_staff(
        clinic=other,
        role=Role.NURSE,
    )

    with pytest.raises(
        NotFoundError,
    ):
        nursing_service.create_care_task(
            care_plan_id=plan.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            description="Must fail",
            assigned_to_staff_id=(
                foreign_nurse.id
            ),
        )


def test_task_start_and_completion_records_actor(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = _plan(
        monkeypatch=monkeypatch,
        clinic=clinic,
        admission=admission,
        nurse=nurse,
    )

    task = nursing_service.create_care_task(
        care_plan_id=plan.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        description="Assess skin integrity",
    )

    started = nursing_service.start_care_task(
        task.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
    )

    assert (
        started.status
        == NursingCareTaskStatus.IN_PROGRESS
    )
    assert started.started_at is not None

    completed = (
        nursing_service
        .complete_care_task(
            task.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
        )
    )

    assert (
        completed.status
        == NursingCareTaskStatus.COMPLETED
    )
    assert (
        completed.completed_by_staff_id
        == nurse.id
    )
    assert completed.completed_at is not None


def test_cancelled_task_cannot_be_completed(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = _plan(
        monkeypatch=monkeypatch,
        clinic=clinic,
        admission=admission,
        nurse=nurse,
    )

    task = nursing_service.create_care_task(
        care_plan_id=plan.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        description="Cancelled task",
    )

    nursing_service.cancel_care_task(
        task.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        reason="No longer indicated",
    )

    with pytest.raises(
        ConflictError,
    ):
        nursing_service.complete_care_task(
            task.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
        )


def test_create_note_derives_context_and_starts_draft(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    note = nursing_service.create_nursing_note(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        note_text="Patient resting comfortably.",
    )

    assert note.patient_id == patient.id
    assert note.author_staff_id == nurse.id
    assert (
        note.status
        == NursingNoteStatus.DRAFT
    )


def test_signed_note_is_immutable(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    note = nursing_service.create_nursing_note(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        note_text="Initial nursing note.",
    )

    signed = nursing_service.sign_nursing_note(
        note.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
    )

    assert (
        signed.status
        == NursingNoteStatus.SIGNED
    )
    assert signed.signed_at is not None

    with pytest.raises(
        ConflictError,
    ):
        nursing_service.update_nursing_note_draft(
            note.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            note_text="Illegal edit.",
        )


def test_only_note_author_can_sign(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    other_nurse = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    note = nursing_service.create_nursing_note(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        note_text="Author-only signature.",
    )

    with pytest.raises(
        ConflictError,
    ):
        nursing_service.sign_nursing_note(
            note.id,
            clinic_id=clinic.id,
            actor_staff_id=other_nurse.id,
        )


def test_handoff_creation_derives_patient_and_outgoing_nurse(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    incoming = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    start = _db_now()
    handoff = nursing_service.create_handoff(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        incoming_staff_id=incoming.id,
        shift_start_at=start,
        shift_end_at=(
            start
            + timedelta(hours=8)
        ),
        situation="Stable overnight.",
        assessment="Vitals stable.",
        recommendation="Continue monitoring.",
    )

    assert handoff.patient_id == patient.id
    assert (
        handoff.outgoing_staff_id
        == nurse.id
    )
    assert (
        handoff.incoming_staff_id
        == incoming.id
    )
    assert (
        handoff.status
        == NursingHandoffStatus.DRAFT
    )


def test_handoff_ready_requires_incoming_and_core_sbar(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    start = _db_now()

    handoff = nursing_service.create_handoff(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        shift_start_at=start,
        shift_end_at=(
            start
            + timedelta(hours=8)
        ),
    )

    with pytest.raises(
        ValidationError,
    ):
        nursing_service.mark_handoff_ready(
            handoff.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
        )


def test_only_assigned_incoming_nurse_can_acknowledge_handoff(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        _patient,
        outgoing,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    incoming = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )
    stranger = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    start = _db_now()

    handoff = nursing_service.create_handoff(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=outgoing.id,
        incoming_staff_id=incoming.id,
        shift_start_at=start,
        shift_end_at=(
            start
            + timedelta(hours=8)
        ),
        situation="Stable.",
        assessment="No acute distress.",
        recommendation="Continue plan.",
    )

    nursing_service.mark_handoff_ready(
        handoff.id,
        clinic_id=clinic.id,
        actor_staff_id=outgoing.id,
    )

    with pytest.raises(
        ConflictError,
    ):
        nursing_service.acknowledge_handoff(
            handoff.id,
            clinic_id=clinic.id,
            actor_staff_id=stranger.id,
        )

    acknowledged = (
        nursing_service
        .acknowledge_handoff(
            handoff.id,
            clinic_id=clinic.id,
            actor_staff_id=incoming.id,
        )
    )

    assert (
        acknowledged.status
        == NursingHandoffStatus.ACKNOWLEDGED
    )
    assert (
        acknowledged
        .acknowledged_by_staff_id
        == incoming.id
    )
    assert (
        acknowledged.acknowledged_at
        is not None
    )


def test_handoff_rejects_same_outgoing_and_incoming_nurse(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(
        monkeypatch
    )

    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    start = _db_now()

    with pytest.raises(
        ValidationError,
    ):
        nursing_service.create_handoff(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            incoming_staff_id=nurse.id,
            shift_start_at=start,
            shift_end_at=(
                start
                + timedelta(hours=8)
            ),
        )


def test_discharge_blocks_task_note_and_handoff_mutations(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = _plan(
        monkeypatch=monkeypatch,
        clinic=clinic,
        admission=admission,
        nurse=nurse,
    )

    task = nursing_service.create_care_task(
        care_plan_id=plan.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        description="Pending at discharge",
    )

    admission.status = (
        AdmissionStatus.DISCHARGED
    )
    admission.discharged_at = _db_now()
    db.session.commit()

    with pytest.raises(
        ConflictError,
    ):
        nursing_service.start_care_task(
            task.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
        )

    with pytest.raises(
        ConflictError,
    ):
        nursing_service.create_nursing_note(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            note_text="Must fail.",
        )

    start = _db_now()

    with pytest.raises(
        ConflictError,
    ):
        nursing_service.create_handoff(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            shift_start_at=start,
            shift_end_at=(
                start
                + timedelta(hours=8)
            ),
        )


def test_history_lists_are_tenant_scoped_and_remain_readable_after_discharge(
    monkeypatch,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
):
    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = _plan(
        monkeypatch=monkeypatch,
        clinic=clinic,
        admission=admission,
        nurse=nurse,
    )

    nursing_service.create_nursing_note(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        note_text="Historical note.",
    )

    start = _db_now()
    nursing_service.create_handoff(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        shift_start_at=start,
        shift_end_at=(
            start
            + timedelta(hours=8)
        ),
    )

    admission.status = (
        AdmissionStatus.DISCHARGED
    )
    admission.discharged_at = _db_now()
    db.session.commit()

    assert [
        item.id
        for item in nursing_service.list_care_plans(
            admission_id=admission.id,
            clinic_id=clinic.id,
        )
    ] == [
        plan.id
    ]

    assert len(
        nursing_service.list_nursing_notes(
            admission_id=admission.id,
            clinic_id=clinic.id,
        )
    ) == 1

    assert len(
        nursing_service.list_handoffs(
            admission_id=admission.id,
            clinic_id=clinic.id,
        )
    ) == 1

    other = make_clinic(
        name="Gate 8 History Other Clinic",
    )

    with pytest.raises(
        NotFoundError,
    ):
        nursing_service.list_care_plans(
            admission_id=admission.id,
            clinic_id=other.id,
        )


def test_nursing_write_audit_carries_actor_clinic_and_entity(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    audits = []

    monkeypatch.setattr(
        nursing_service,
        "create_audit_log",
        lambda **kwargs: audits.append(
            kwargs
        ),
    )

    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = nursing_service.create_care_plan(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=nurse.id,
        title="Audit plan",
        problem="Audit",
        goal="Audit",
    )

    assert len(audits) == 1

    audit = audits[0]

    assert (
        audit["entity_type"]
        == "NursingCarePlan"
    )
    assert (
        audit["entity_id"]
        == plan.id
    )
    assert (
        audit["clinic_id"]
        == clinic.id
    )
    assert (
        audit["user_id"]
        == nurse.user_id
    )
    assert (
        audit["new_value"]["patient_id"]
        == admission.patient_id
    )
