from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.utils.decorators import get_current_clinic_id, role_required

from app.modules.nursing.schemas.nursing_schema import (
    NursingCarePlanCreateSchema,
    NursingCarePlanListResponseSchema,
    NursingCarePlanResponseSchema,
    NursingCareTaskCancelSchema,
    NursingCareTaskCreateSchema,
    NursingCareTaskListResponseSchema,
    NursingCareTaskResponseSchema,
    NursingHandoffCreateSchema,
    NursingHandoffListResponseSchema,
    NursingHandoffResponseSchema,
    NursingHandoffUpdateSchema,
    NursingNoteCreateSchema,
    NursingNoteListResponseSchema,
    NursingNoteResponseSchema,
    NursingNoteUpdateSchema,
)
from app.modules.nursing.services.nursing_service import (
    acknowledge_handoff,
    cancel_care_plan,
    cancel_care_task,
    cancel_handoff,
    complete_care_plan,
    complete_care_task,
    create_care_plan,
    create_care_task,
    create_handoff,
    create_nursing_note,
    get_care_plan,
    get_care_task,
    get_handoff,
    get_nursing_note,
    hold_care_plan,
    list_care_plans,
    list_care_tasks,
    list_handoffs,
    list_nursing_notes,
    mark_handoff_ready,
    resume_care_plan,
    sign_nursing_note,
    start_care_task,
    update_handoff_draft,
    update_nursing_note_draft,
)


nursing_bp = Blueprint("nursing", __name__, url_prefix="/nursing")

NURSING_WRITE_ROLES = (Role.NURSE,)
NURSING_VIEW_ROLES = (Role.ADMIN, Role.DOCTOR, Role.NURSE)


def _current_user() -> User:
    identity = get_jwt_identity()
    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        raise ValidationError("Invalid authentication identity")

    if user_id <= 0:
        raise ValidationError("Invalid authentication identity")

    user = db.session.get(User, user_id)
    if user is None:
        raise ValidationError("Authenticated user could not be resolved")
    if not user.is_active:
        raise ValidationError("User account is inactive")
    return user


def _current_staff_id() -> int:
    user = _current_user()
    if user.staff is None:
        raise ValidationError(
            "Authenticated user is not linked to a staff record"
        )
    return user.staff.id


def _current_clinic_id() -> int:
    return get_current_clinic_id(required=True)


def _request_json() -> dict:
    payload = request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object")
    return payload


def _serialize(schema, value):
    return schema.model_validate(value).model_dump(mode="json")


def _serialize_list(response_schema, item_schema, items):
    payload = {
        "items": [_serialize(item_schema, item) for item in items],
        "total": len(items),
    }
    return response_schema.model_validate(payload).model_dump(mode="json")


@nursing_bp.post("/admissions/<int:admission_id>/care-plans")
@role_required(*NURSING_WRITE_ROLES)
def create_care_plan_route(admission_id: int):
    payload = NursingCarePlanCreateSchema.model_validate(_request_json())
    plan = create_care_plan(
        admission_id=admission_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        title=payload.title,
        problem=payload.problem,
        goal=payload.goal,
        target_at=payload.target_at,
    )
    return jsonify({
        "message": "Nursing care plan created",
        "care_plan": _serialize(NursingCarePlanResponseSchema, plan),
    }), 201


@nursing_bp.get("/admissions/<int:admission_id>/care-plans")
@role_required(*NURSING_VIEW_ROLES)
def list_care_plans_route(admission_id: int):
    items = list_care_plans(
        admission_id=admission_id,
        clinic_id=_current_clinic_id(),
        status=request.args.get("status"),
    )
    return jsonify(_serialize_list(
        NursingCarePlanListResponseSchema,
        NursingCarePlanResponseSchema,
        items,
    )), 200


@nursing_bp.get("/care-plans/<int:care_plan_id>")
@role_required(*NURSING_VIEW_ROLES)
def get_care_plan_route(care_plan_id: int):
    plan = get_care_plan(care_plan_id, clinic_id=_current_clinic_id())
    return jsonify(_serialize(NursingCarePlanResponseSchema, plan)), 200


def _plan_action(action, message, care_plan_id):
    plan = action(
        care_plan_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
    )
    return jsonify({
        "message": message,
        "care_plan": _serialize(NursingCarePlanResponseSchema, plan),
    }), 200


@nursing_bp.post("/care-plans/<int:care_plan_id>/hold")
@role_required(*NURSING_WRITE_ROLES)
def hold_care_plan_route(care_plan_id: int):
    return _plan_action(
        hold_care_plan,
        "Nursing care plan placed on hold",
        care_plan_id,
    )


@nursing_bp.post("/care-plans/<int:care_plan_id>/resume")
@role_required(*NURSING_WRITE_ROLES)
def resume_care_plan_route(care_plan_id: int):
    return _plan_action(
        resume_care_plan,
        "Nursing care plan resumed",
        care_plan_id,
    )


@nursing_bp.post("/care-plans/<int:care_plan_id>/complete")
@role_required(*NURSING_WRITE_ROLES)
def complete_care_plan_route(care_plan_id: int):
    return _plan_action(
        complete_care_plan,
        "Nursing care plan completed",
        care_plan_id,
    )


@nursing_bp.post("/care-plans/<int:care_plan_id>/cancel")
@role_required(*NURSING_WRITE_ROLES)
def cancel_care_plan_route(care_plan_id: int):
    return _plan_action(
        cancel_care_plan,
        "Nursing care plan cancelled",
        care_plan_id,
    )


@nursing_bp.post("/care-plans/<int:care_plan_id>/tasks")
@role_required(*NURSING_WRITE_ROLES)
def create_care_task_route(care_plan_id: int):
    payload = NursingCareTaskCreateSchema.model_validate(_request_json())
    task = create_care_task(
        care_plan_id=care_plan_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        description=payload.description,
        instructions=payload.instructions,
        priority=payload.priority,
        assigned_to_staff_id=payload.assigned_to_staff_id,
        due_at=payload.due_at,
    )
    return jsonify({
        "message": "Nursing care task created",
        "task": _serialize(NursingCareTaskResponseSchema, task),
    }), 201


@nursing_bp.get("/admissions/<int:admission_id>/tasks")
@role_required(*NURSING_VIEW_ROLES)
def list_care_tasks_route(admission_id: int):
    items = list_care_tasks(
        admission_id=admission_id,
        clinic_id=_current_clinic_id(),
    )
    return jsonify(_serialize_list(
        NursingCareTaskListResponseSchema,
        NursingCareTaskResponseSchema,
        items,
    )), 200


@nursing_bp.get("/tasks/<int:task_id>")
@role_required(*NURSING_VIEW_ROLES)
def get_care_task_route(task_id: int):
    task = get_care_task(task_id, clinic_id=_current_clinic_id())
    return jsonify(_serialize(NursingCareTaskResponseSchema, task)), 200


def _task_action(action, message, task_id):
    task = action(
        task_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
    )
    return jsonify({
        "message": message,
        "task": _serialize(NursingCareTaskResponseSchema, task),
    }), 200


@nursing_bp.post("/tasks/<int:task_id>/start")
@role_required(*NURSING_WRITE_ROLES)
def start_care_task_route(task_id: int):
    return _task_action(
        start_care_task,
        "Nursing care task started",
        task_id,
    )


@nursing_bp.post("/tasks/<int:task_id>/complete")
@role_required(*NURSING_WRITE_ROLES)
def complete_care_task_route(task_id: int):
    return _task_action(
        complete_care_task,
        "Nursing care task completed",
        task_id,
    )


@nursing_bp.post("/tasks/<int:task_id>/cancel")
@role_required(*NURSING_WRITE_ROLES)
def cancel_care_task_route(task_id: int):
    payload = NursingCareTaskCancelSchema.model_validate(_request_json())
    task = cancel_care_task(
        task_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        reason=payload.reason,
    )
    return jsonify({
        "message": "Nursing care task cancelled",
        "task": _serialize(NursingCareTaskResponseSchema, task),
    }), 200


@nursing_bp.post("/admissions/<int:admission_id>/notes")
@role_required(*NURSING_WRITE_ROLES)
def create_nursing_note_route(admission_id: int):
    payload = NursingNoteCreateSchema.model_validate(_request_json())
    note = create_nursing_note(
        admission_id=admission_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        note_text=payload.note_text,
        note_type=payload.note_type,
        observed_at=payload.observed_at,
    )
    return jsonify({
        "message": "Nursing note draft created",
        "note": _serialize(NursingNoteResponseSchema, note),
    }), 201


@nursing_bp.get("/admissions/<int:admission_id>/notes")
@role_required(*NURSING_VIEW_ROLES)
def list_nursing_notes_route(admission_id: int):
    items = list_nursing_notes(
        admission_id=admission_id,
        clinic_id=_current_clinic_id(),
    )
    return jsonify(_serialize_list(
        NursingNoteListResponseSchema,
        NursingNoteResponseSchema,
        items,
    )), 200


@nursing_bp.get("/notes/<int:note_id>")
@role_required(*NURSING_VIEW_ROLES)
def get_nursing_note_route(note_id: int):
    note = get_nursing_note(note_id, clinic_id=_current_clinic_id())
    return jsonify(_serialize(NursingNoteResponseSchema, note)), 200


@nursing_bp.patch("/notes/<int:note_id>")
@role_required(*NURSING_WRITE_ROLES)
def update_nursing_note_route(note_id: int):
    payload = NursingNoteUpdateSchema.model_validate(_request_json())
    note = update_nursing_note_draft(
        note_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        note_text=payload.note_text,
        note_type=payload.note_type,
        observed_at=payload.observed_at,
    )
    return jsonify({
        "message": "Nursing note draft updated",
        "note": _serialize(NursingNoteResponseSchema, note),
    }), 200


@nursing_bp.post("/notes/<int:note_id>/sign")
@role_required(*NURSING_WRITE_ROLES)
def sign_nursing_note_route(note_id: int):
    note = sign_nursing_note(
        note_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
    )
    return jsonify({
        "message": "Nursing note signed",
        "note": _serialize(NursingNoteResponseSchema, note),
    }), 200


@nursing_bp.post("/admissions/<int:admission_id>/handoffs")
@role_required(*NURSING_WRITE_ROLES)
def create_handoff_route(admission_id: int):
    payload = NursingHandoffCreateSchema.model_validate(_request_json())
    handoff = create_handoff(
        admission_id=admission_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        shift_start_at=payload.shift_start_at,
        shift_end_at=payload.shift_end_at,
        incoming_staff_id=payload.incoming_staff_id,
        situation=payload.situation,
        background=payload.background,
        assessment=payload.assessment,
        recommendation=payload.recommendation,
        safety_concerns=payload.safety_concerns,
        pending_actions=payload.pending_actions,
    )
    return jsonify({
        "message": "Nursing handoff draft created",
        "handoff": _serialize(NursingHandoffResponseSchema, handoff),
    }), 201


@nursing_bp.get("/admissions/<int:admission_id>/handoffs")
@role_required(*NURSING_VIEW_ROLES)
def list_handoffs_route(admission_id: int):
    items = list_handoffs(
        admission_id=admission_id,
        clinic_id=_current_clinic_id(),
    )
    return jsonify(_serialize_list(
        NursingHandoffListResponseSchema,
        NursingHandoffResponseSchema,
        items,
    )), 200


@nursing_bp.get("/handoffs/<int:handoff_id>")
@role_required(*NURSING_VIEW_ROLES)
def get_handoff_route(handoff_id: int):
    handoff = get_handoff(handoff_id, clinic_id=_current_clinic_id())
    return jsonify(_serialize(NursingHandoffResponseSchema, handoff)), 200


@nursing_bp.patch("/handoffs/<int:handoff_id>")
@role_required(*NURSING_WRITE_ROLES)
def update_handoff_route(handoff_id: int):
    payload = NursingHandoffUpdateSchema.model_validate(_request_json())
    handoff = update_handoff_draft(
        handoff_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
        incoming_staff_id=payload.incoming_staff_id,
        situation=payload.situation,
        background=payload.background,
        assessment=payload.assessment,
        recommendation=payload.recommendation,
        safety_concerns=payload.safety_concerns,
        pending_actions=payload.pending_actions,
    )
    return jsonify({
        "message": "Nursing handoff draft updated",
        "handoff": _serialize(NursingHandoffResponseSchema, handoff),
    }), 200


def _handoff_action(action, message, handoff_id):
    handoff = action(
        handoff_id,
        clinic_id=_current_clinic_id(),
        actor_staff_id=_current_staff_id(),
    )
    return jsonify({
        "message": message,
        "handoff": _serialize(NursingHandoffResponseSchema, handoff),
    }), 200


@nursing_bp.post("/handoffs/<int:handoff_id>/ready")
@role_required(*NURSING_WRITE_ROLES)
def mark_handoff_ready_route(handoff_id: int):
    return _handoff_action(
        mark_handoff_ready,
        "Nursing handoff marked ready",
        handoff_id,
    )


@nursing_bp.post("/handoffs/<int:handoff_id>/acknowledge")
@role_required(*NURSING_WRITE_ROLES)
def acknowledge_handoff_route(handoff_id: int):
    return _handoff_action(
        acknowledge_handoff,
        "Nursing handoff acknowledged",
        handoff_id,
    )


@nursing_bp.post("/handoffs/<int:handoff_id>/cancel")
@role_required(*NURSING_WRITE_ROLES)
def cancel_handoff_route(handoff_id: int):
    return _handoff_action(
        cancel_handoff,
        "Nursing handoff cancelled",
        handoff_id,
    )
