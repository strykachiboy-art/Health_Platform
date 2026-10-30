from __future__ import annotations

from app import models_registry  # noqa: F401
from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskPriority,
    NursingCareTaskStatus,
    NursingHandoffStatus,
    NursingNoteStatus,
    NursingNoteType,
)
from app.extensions import db
from app.modules.nursing.models.nursing_model import (
    NursingCarePlan,
    NursingCareTask,
    NursingHandoff,
    NursingNote,
)


def _column_names(model):
    return {
        column.name
        for column in model.__table__.columns
    }


def _constraint_names(model):
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if constraint.name is not None
    }


def _index_names(model):
    return {
        index.name
        for index in model.__table__.indexes
        if index.name is not None
    }


def test_nursing_models_are_registered_with_expected_tables():
    assert (
        NursingCarePlan.__tablename__
        == "nursing_care_plans"
    )
    assert (
        NursingCareTask.__tablename__
        == "nursing_care_tasks"
    )
    assert (
        NursingNote.__tablename__
        == "nursing_notes"
    )
    assert (
        NursingHandoff.__tablename__
        == "nursing_handoffs"
    )

    for table_name in (
        "nursing_care_plans",
        "nursing_care_tasks",
        "nursing_notes",
        "nursing_handoffs",
    ):
        assert table_name in db.metadata.tables


def test_care_plan_is_tenant_patient_admission_and_actor_scoped():
    columns = _column_names(
        NursingCarePlan
    )

    for name in (
        "clinic_id",
        "patient_id",
        "admission_id",
        "created_by_staff_id",
        "title",
        "problem",
        "goal",
        "status",
        "version",
    ):
        assert name in columns


def test_care_plan_has_lifecycle_evidence_constraints():
    names = _constraint_names(
        NursingCarePlan
    )

    assert (
        "ck_nursing_care_plans_completed_evidence"
        in names
    )
    assert (
        "ck_nursing_care_plans_cancelled_evidence"
        in names
    )
    assert (
        "ck_nursing_care_plans_terminal_exclusive"
        in names
    )
    assert (
        "ck_nursing_care_plans_target_after_start"
        in names
    )


def test_care_plan_has_operational_indexes():
    names = _index_names(
        NursingCarePlan
    )

    assert (
        "ix_nursing_care_plans_clinic_admission_status"
        in names
    )
    assert (
        "ix_nursing_care_plans_patient_admission_created"
        in names
    )


def test_care_task_keeps_plan_tenant_admission_assignment_context():
    columns = _column_names(
        NursingCareTask
    )

    for name in (
        "care_plan_id",
        "clinic_id",
        "patient_id",
        "admission_id",
        "created_by_staff_id",
        "assigned_to_staff_id",
        "completed_by_staff_id",
        "priority",
        "status",
        "due_at",
        "version",
    ):
        assert name in columns


def test_care_task_has_completion_and_cancellation_evidence_constraints():
    names = _constraint_names(
        NursingCareTask
    )

    assert (
        "ck_nursing_care_tasks_completed_evidence"
        in names
    )
    assert (
        "ck_nursing_care_tasks_cancelled_evidence"
        in names
    )
    assert (
        "ck_nursing_care_tasks_terminal_exclusive"
        in names
    )


def test_nursing_note_is_admission_scoped_signable_documentation():
    columns = _column_names(
        NursingNote
    )

    for name in (
        "clinic_id",
        "patient_id",
        "admission_id",
        "author_staff_id",
        "note_type",
        "status",
        "note_text",
        "observed_at",
        "signed_at",
        "version",
    ):
        assert name in columns


def test_nursing_note_has_signed_evidence_constraint():
    assert (
        "ck_nursing_notes_signed_evidence"
        in _constraint_names(
            NursingNote
        )
    )


def test_handoff_is_structured_sbar_and_shift_scoped():
    columns = _column_names(
        NursingHandoff
    )

    for name in (
        "clinic_id",
        "patient_id",
        "admission_id",
        "outgoing_staff_id",
        "incoming_staff_id",
        "shift_start_at",
        "shift_end_at",
        "situation",
        "background",
        "assessment",
        "recommendation",
        "safety_concerns",
        "pending_actions",
    ):
        assert name in columns


def test_handoff_has_acknowledgement_and_terminal_evidence():
    columns = _column_names(
        NursingHandoff
    )
    constraints = _constraint_names(
        NursingHandoff
    )

    for name in (
        "acknowledged_by_staff_id",
        "ready_at",
        "acknowledged_at",
        "cancelled_at",
        "version",
    ):
        assert name in columns

    assert (
        "ck_nursing_handoffs_ack_evidence"
        in constraints
    )
    assert (
        "ck_nursing_handoffs_ready_evidence"
        in constraints
    )
    assert (
        "ck_nursing_handoffs_cancelled_evidence"
        in constraints
    )
    assert (
        "ck_nursing_handoffs_shift_window"
        in constraints
    )


def test_nursing_enum_contracts_are_explicit_and_stable():
    assert {
        value.value
        for value in NursingCarePlanStatus
    } == {
        "active",
        "on_hold",
        "completed",
        "cancelled",
    }

    assert {
        value.value
        for value in NursingCareTaskStatus
    } == {
        "pending",
        "in_progress",
        "completed",
        "cancelled",
    }

    assert {
        value.value
        for value in NursingCareTaskPriority
    } == {
        "routine",
        "high",
        "urgent",
    }

    assert {
        value.value
        for value in NursingNoteType
    } == {
        "assessment",
        "progress",
        "intervention",
        "education",
        "safety",
        "other",
    }

    assert {
        value.value
        for value in NursingNoteStatus
    } == {
        "draft",
        "signed",
    }

    assert {
        value.value
        for value in NursingHandoffStatus
    } == {
        "draft",
        "ready",
        "acknowledged",
        "cancelled",
    }


def test_nursing_domain_has_no_delete_orphan_clinical_record_cascade():
    assert (
        "delete-orphan"
        not in str(
            NursingCarePlan.tasks.property.cascade
        )
    )
