from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import DomainError, NotFoundError, ValidationError
from app.core.utils.decorators import (
    login_required,
    role_required,
    get_current_clinic_id,
)
from app.core.enums.role_enums import Role

from app.modules.feedback.schemas.feedback_comment_schema import (
    FeedbackCommentCreateSchema,
)
from app.modules.feedback.schemas.feedback_query_schema import (
    FeedbackListQuerySchema,
)
from app.modules.feedback.schemas.feedback_schema import (
    FeedbackCreateSchema,
    FeedbackManageSchema,
)
from app.modules.feedback.services.feedback_comment_service import (
    create_feedback_comment,
    get_feedback_comment,
    list_feedback_comments,
    update_feedback_comment,
)
from app.modules.feedback.services.feedback_service import (
    close_feedback,
    create_feedback,
    get_feedback_for_actor,
    list_feedback,
    manage_feedback,
    reject_feedback,
    reopen_feedback,
    resolve_feedback,
)


feedback_bp = Blueprint(
    "feedback",
    __name__,
    url_prefix="/feedback",
)


FEEDBACK_MANAGE_ROLES = (
    Role.ADMIN,
    Role.SUPER_ADMIN,
)


def _get_current_user_id() -> int:
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Invalid authentication identity"
        ) from exc

    if user_id <= 0:
        raise ValidationError(
            "Invalid authentication identity"
        )

    return user_id


def _get_optional_clinic_id():
    raw = request.args.get("clinic_id")

    if raw is None or raw == "":
        return None

    claims = get_jwt()
    raw_role = claims.get("role")

    try:
        role = (
            raw_role
            if isinstance(raw_role, Role)
            else Role(raw_role)
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Invalid authentication role"
        ) from exc

    if role != Role.SUPER_ADMIN:
        raise NotFoundError(
            "Requested feedback scope was not found"
        )

    try:
        clinic_id = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Invalid clinic_id"
        ) from exc

    if clinic_id <= 0:
        raise ValidationError(
            "Invalid clinic_id"
        )

    return clinic_id



def _get_json_object() -> dict:
    payload = request.get_json(
        silent=True,
    )

    if payload is None:
        return {}

    if not isinstance(payload, dict):
        raise ValidationError(
            "Request body must be a JSON object"
        )

    return payload


def _build_feedback_list_query():
    values = request.args.to_dict()
    values.pop("clinic_id", None)

    integer_fields = (
        "page",
        "per_page",
        "assigned_to_user_id",
        "submitted_by_user_id",
    )

    for field in integer_fields:
        if field not in values:
            continue

        try:
            values[field] = int(values[field])
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"{field} must be an integer"
            ) from exc

    return FeedbackListQuerySchema.model_validate(values)


def _handle_route_error(exc):
    if isinstance(exc, DomainError):
        return jsonify({
            "success": False,
            "error": str(exc),
        }), exc.status_code

    return jsonify({
        "success": False,
        "error": "An unexpected error occurred",
    }), 400


def _validation_error_response(
    exc: PydanticValidationError,
):
    details = []

    for error in exc.errors():
        clean_error = {
            "type": error.get("type"),
            "loc": list(error.get("loc", ())),
            "msg": error.get("msg"),
        }

        if "input" in error:
            clean_error["input"] = error["input"]

        details.append(clean_error)

    return jsonify({
        "success": False,
        "error": "Validation failed",
        "details": details,
    }), 422


def _serialize_feedback(feedback):
    return {
        "id": feedback.id,
        "clinic_id": feedback.clinic_id,
        "submitted_by_user_id": feedback.submitted_by_user_id,
        "feedback_type": feedback.feedback_type.value,
        "category": feedback.category.value,
        "subject": feedback.subject,
        "message": feedback.message,
        "status": feedback.status.value,
        "priority": feedback.priority.value,
        "source": feedback.source.value,
        "target_module": feedback.target_module,
        "target_resource_type": feedback.target_resource_type,
        "target_resource_id": feedback.target_resource_id,
        "assigned_to_user_id": feedback.assigned_to_user_id,
        "resolution_note": feedback.resolution_note,
        "created_at": (
            feedback.created_at.isoformat()
            if feedback.created_at
            else None
        ),
        "updated_at": (
            feedback.updated_at.isoformat()
            if feedback.updated_at
            else None
        ),
        "resolved_at": (
            feedback.resolved_at.isoformat()
            if feedback.resolved_at
            else None
        ),
        "closed_at": (
            feedback.closed_at.isoformat()
            if feedback.closed_at
            else None
        ),
    }


def _serialize_comment(comment):
    return {
        "id": comment.id,
        "feedback_id": comment.feedback_id,
        "author_user_id": comment.author_user_id,
        "body": comment.body,
        "created_at": (
            comment.created_at.isoformat()
            if comment.created_at
            else None
        ),
        "updated_at": (
            comment.updated_at.isoformat()
            if comment.updated_at
            else None
        ),
    }


def _serialize_feedback_page(result):
    return {
        "items": [
            _serialize_feedback(item)
            for item in result["items"]
        ],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
        "pages": result["pages"],
        "has_next": result["has_next"],
        "has_previous": result["has_previous"],
    }


def _serialize_comment_page(result):
    return {
        "items": [
            _serialize_comment(item)
            for item in result["items"]
        ],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
        "pages": result["pages"],
        "has_next": result["has_next"],
        "has_previous": result["has_previous"],
    }


@feedback_bp.post("")
@login_required
def create_feedback_route():
    try:
        actor_user_id = _get_current_user_id()

        payload = FeedbackCreateSchema.model_validate(
            _get_json_object()
        )

        key = request.headers.get("Idempotency-Key")
        if key is None:
            feedback = create_feedback(actor_user_id=actor_user_id, payload=payload)
        else:
            feedback = create_feedback(
                actor_user_id=actor_user_id, payload=payload,
                clinic_id=get_current_clinic_id(required=True), idempotency_key=key,
            )

        return jsonify({
            "success": True,
            "message": "Feedback created successfully",
            "data": _serialize_feedback(feedback),
        }), 201

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.get("")
@login_required
def list_feedback_route():
    try:
        actor_user_id = _get_current_user_id()

        query = _build_feedback_list_query()

        clinic_id = _get_optional_clinic_id()

        result = list_feedback(
            actor_user_id=actor_user_id,
            query=query,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "data": _serialize_feedback_page(result),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.get("/<int:feedback_id>")
@login_required
def get_feedback_route(feedback_id: int):
    try:
        actor_user_id = _get_current_user_id()

        clinic_id = _get_optional_clinic_id()

        feedback = get_feedback_for_actor(
            actor_user_id=actor_user_id,
            feedback_id=feedback_id,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "data": _serialize_feedback(feedback),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.patch("/<int:feedback_id>")
@role_required(*FEEDBACK_MANAGE_ROLES)
def manage_feedback_route(feedback_id: int):
    try:
        actor_user_id = _get_current_user_id()

        payload = FeedbackManageSchema.model_validate(
            _get_json_object()
        )

        clinic_id = _get_optional_clinic_id()

        feedback = manage_feedback(
            actor_user_id=actor_user_id,
            feedback_id=feedback_id,
            payload=payload,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Feedback updated successfully",
            "data": _serialize_feedback(feedback),
        }), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.post("/<int:feedback_id>/resolve")
@role_required(*FEEDBACK_MANAGE_ROLES)
def resolve_feedback_route(feedback_id: int):
    try:
        actor_user_id = _get_current_user_id()

        payload = _get_json_object()
        resolution_note = payload.get("resolution_note")

        clinic_id = _get_optional_clinic_id()

        feedback = resolve_feedback(
            actor_user_id=actor_user_id,
            feedback_id=feedback_id,
            resolution_note=resolution_note,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Feedback resolved successfully",
            "data": _serialize_feedback(feedback),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.post("/<int:feedback_id>/reopen")
@role_required(*FEEDBACK_MANAGE_ROLES)
def reopen_feedback_route(feedback_id: int):
    try:
        actor_user_id = _get_current_user_id()

        clinic_id = _get_optional_clinic_id()

        feedback = reopen_feedback(
            actor_user_id=actor_user_id,
            feedback_id=feedback_id,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Feedback reopened successfully",
            "data": _serialize_feedback(feedback),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.post("/<int:feedback_id>/close")
@role_required(*FEEDBACK_MANAGE_ROLES)
def close_feedback_route(feedback_id: int):
    try:
        actor_user_id = _get_current_user_id()

        clinic_id = _get_optional_clinic_id()

        feedback = close_feedback(
            actor_user_id=actor_user_id,
            feedback_id=feedback_id,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Feedback closed successfully",
            "data": _serialize_feedback(feedback),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.post("/<int:feedback_id>/reject")
@role_required(*FEEDBACK_MANAGE_ROLES)
def reject_feedback_route(feedback_id: int):
    try:
        actor_user_id = _get_current_user_id()

        clinic_id = _get_optional_clinic_id()

        feedback = reject_feedback(
            actor_user_id=actor_user_id,
            feedback_id=feedback_id,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Feedback rejected successfully",
            "data": _serialize_feedback(feedback),
        }), 200


    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.post("/<int:feedback_id>/comments")
@login_required
def create_feedback_comment_route(feedback_id: int):
    try:
        actor_user_id = _get_current_user_id()

        payload = FeedbackCommentCreateSchema.model_validate(
            _get_json_object()
        )

        clinic_id = _get_optional_clinic_id()

        comment = create_feedback_comment(
            actor_user_id=actor_user_id,
            feedback_id=feedback_id,
            payload=payload,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Feedback comment created successfully",
            "data": _serialize_comment(comment),
        }), 201

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.get("/<int:feedback_id>/comments")
@login_required
def list_feedback_comments_route(feedback_id: int):
    try:
        actor_user_id = _get_current_user_id()

        try:
            page = int(request.args.get("page", 1))
            per_page = int(request.args.get("per_page", 50))
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "Invalid pagination parameters"
            ) from exc

        clinic_id = _get_optional_clinic_id()

        result = list_feedback_comments(
            actor_user_id=actor_user_id,
            feedback_id=feedback_id,
            page=page,
            per_page=per_page,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "data": _serialize_comment_page(result),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.get(
    "/<int:feedback_id>/comments/<int:comment_id>"
)
@login_required
def get_feedback_comment_route(
    feedback_id: int,
    comment_id: int,
):
    try:
        actor_user_id = _get_current_user_id()

        clinic_id = _get_optional_clinic_id()

        comment = get_feedback_comment(
            actor_user_id=actor_user_id,
            comment_id=comment_id,
            feedback_id=feedback_id,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "data": _serialize_comment(comment),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)


@feedback_bp.patch(
    "/<int:feedback_id>/comments/<int:comment_id>"
)
@login_required
def update_feedback_comment_route(
    feedback_id: int,
    comment_id: int,
):
    try:
        actor_user_id = _get_current_user_id()

        payload = _get_json_object()

        body = payload.get("body")

        clinic_id = _get_optional_clinic_id()

        comment = update_feedback_comment(
            actor_user_id=actor_user_id,
            comment_id=comment_id,
            body=body,
            feedback_id=feedback_id,
            clinic_id=clinic_id,
        )

        return jsonify({
            "success": True,
            "message": "Feedback comment updated successfully",
            "data": _serialize_comment(comment),
        }), 200

    except Exception as exc:
        return _handle_route_error(exc)
