from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.notifications.schemas.notification_schema import (
    NotificationCreateSchema,
    NotificationListQuerySchema,
    NotificationReadSchema,
)
from app.core.notifications.services.notification_service import (
    create_notification,
    get_notification_for_user,
    get_user_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)
from app.core.utils.decorators import get_current_clinic_id, role_required
from app.extensions import db


notification_bp = Blueprint(
    "notification",
    __name__,
    url_prefix="/notifications",
)


NOTIFICATION_ROLES = (
    Role.DOCTOR,
    Role.NURSE,
    Role.PATIENT,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.RECEPTIONIST,
    Role.ADMIN,
    Role.ACCOUNTANT,
    Role.PARAMEDIC,
    Role.EMT,
    Role.DRIVER,
    Role.AMBULANCE_DISPATCHER,
    Role.AMBULANCE_COORDINATOR,
    Role.OTHER,
)

# Read access for a super admin is clinic-context scoped, never global.
NOTIFICATION_INBOX_ROLES = (*NOTIFICATION_ROLES, Role.SUPER_ADMIN)


def _payload(schema):
    return schema.model_validate(
        request.get_json(silent=True) or {}
    )


def _query_payload(schema):
    return schema.model_validate(
        request.args.to_dict()
    )


def _get_authenticated_user():
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

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        raise NotFoundError(
            "Authenticated user not found"
        )

    if not user.is_active:
        raise ValidationError(
            "Authenticated user is inactive"
        )

    if user.clinic_id is None and user.role is not Role.SUPER_ADMIN:
        raise ValidationError(
            "Authenticated user is not assigned to a clinic"
        )

    return user


def _inbox_clinic_id(user):
    if user.role is Role.SUPER_ADMIN:
        # This is a selected, active clinic verified through JWT context.
        # Never use a client-supplied clinic or a cross-clinic global feed.
        from flask import g
        if g.current_user_role != Role.SUPER_ADMIN.value:
            raise ValidationError("Super admin role context mismatch")
        return get_current_clinic_id(required=True)
    return user.clinic_id


def _serialize_notification(notification):
    return {
        "id": notification.id,
        "clinic_id": notification.clinic_id,
        "user_id": notification.user_id,
        "title": notification.title,
        "message": notification.message,
        "notification_type": (
            notification.notification_type.value
        ),
        "priority": notification.priority.value,
        "channel": notification.channel.value,
        "status": notification.status.value,
        "reference_type": notification.reference_type,
        "reference_id": notification.reference_id,
        "is_read": notification.is_read,
        "read_at": (
            notification.read_at.isoformat()
            if notification.read_at
            else None
        ),
        "sent_at": (
            notification.sent_at.isoformat()
            if notification.sent_at
            else None
        ),
        "delivered_at": (
            notification.delivered_at.isoformat()
            if notification.delivered_at
            else None
        ),
        "failed_at": (
            notification.failed_at.isoformat()
            if notification.failed_at
            else None
        ),
        "error_message": notification.error_message,
        "retry_count": notification.retry_count,
        "created_at": (
            notification.created_at.isoformat()
            if notification.created_at
            else None
        ),
        "updated_at": (
            notification.updated_at.isoformat()
            if notification.updated_at
            else None
        ),
    }


def _serialize_notification_page(result):
    return {
        "items": [
            _serialize_notification(notification)
            for notification in result["items"]
        ],
        "page": result["page"],
        "per_page": result["per_page"],
        "total": result["total"],
        "pages": result["pages"],
        "has_next": result["has_next"],
        "has_prev": result["has_prev"],
    }


def _domain_error_response(exc):
    if isinstance(exc, NotFoundError):
        status_code = 404
    elif isinstance(exc, ConflictError):
        status_code = 409
    elif isinstance(exc, ValidationError):
        status_code = 422
    else:
        status_code = 400

    return jsonify({
        "success": False,
        "error": str(exc),
    }), status_code


@notification_bp.post("/")
@role_required(*NOTIFICATION_ROLES)
def create():
    payload = _payload(
        NotificationCreateSchema
    )

    try:
        user = _get_authenticated_user()

        # No untrusted client can send a notification to a platform administrator.
        target = db.session.get(User, payload.user_id)
        if target is not None and target.role is Role.SUPER_ADMIN:
            raise NotFoundError("Notification recipient not found")

        notification = create_notification(
            clinic_id=user.clinic_id,
            actor_user_id=user.id,
            **payload.model_dump(),
        )

        return jsonify({
            "success": True,
            "data": _serialize_notification(
                notification
            ),
        }), 201

    except (
        NotFoundError,
        ValidationError,
        ConflictError,
    ) as exc:
        return _domain_error_response(exc)


@notification_bp.get("/")
@role_required(*NOTIFICATION_INBOX_ROLES)
def list_notifications():
    query_payload = _query_payload(
        NotificationListQuerySchema
    )

    try:
        user = _get_authenticated_user()

        result = get_user_notifications(
            user_id=user.id,
            clinic_id=_inbox_clinic_id(user),
            unread_only=False,
            page=query_payload.page,
            per_page=query_payload.per_page,
        )

        return jsonify({
            "success": True,
            "data": _serialize_notification_page(
                result
            ),
        }), 200

    except (
        NotFoundError,
        ValidationError,
        ConflictError,
    ) as exc:
        return _domain_error_response(exc)


@notification_bp.get("/unread")
@role_required(*NOTIFICATION_INBOX_ROLES)
def unread_notifications():
    query_payload = _query_payload(
        NotificationListQuerySchema
    )

    try:
        user = _get_authenticated_user()

        result = get_user_notifications(
            user_id=user.id,
            clinic_id=_inbox_clinic_id(user),
            unread_only=True,
            page=query_payload.page,
            per_page=query_payload.per_page,
        )

        return jsonify({
            "success": True,
            "data": _serialize_notification_page(
                result
            ),
        }), 200

    except (
        NotFoundError,
        ValidationError,
        ConflictError,
    ) as exc:
        return _domain_error_response(exc)


@notification_bp.get("/<int:notification_id>")
@role_required(*NOTIFICATION_INBOX_ROLES)
def get(notification_id: int):
    try:
        user = _get_authenticated_user()

        notification = get_notification_for_user(
            notification_id=notification_id,
            user_id=user.id,
            clinic_id=_inbox_clinic_id(user),
        )

        return jsonify({
            "success": True,
            "data": _serialize_notification(
                notification
            ),
        }), 200

    except (
        NotFoundError,
        ValidationError,
        ConflictError,
    ) as exc:
        return _domain_error_response(exc)


@notification_bp.post("/<int:notification_id>/read")
@role_required(*NOTIFICATION_INBOX_ROLES)
def read(notification_id: int):
    payload = _payload(
        NotificationReadSchema
    )

    try:
        user = _get_authenticated_user()

        notification = mark_notification_read(
            notification_id=notification_id,
            user_id=user.id,
            clinic_id=_inbox_clinic_id(user),
        )

        return jsonify({
            "success": True,
            "data": _serialize_notification(
                notification
            ),
        }), 200

    except (
        NotFoundError,
        ValidationError,
        ConflictError,
    ) as exc:
        return _domain_error_response(exc)


@notification_bp.post("/read-all")
@role_required(*NOTIFICATION_INBOX_ROLES)
def read_all():
    payload = _payload(
        NotificationReadSchema
    )

    try:
        user = _get_authenticated_user()

        count = mark_all_notifications_read(
            user_id=user.id,
            clinic_id=_inbox_clinic_id(user),
        )

        return jsonify({
            "success": True,
            "data": {
                "updated_count": count,
            },
        }), 200

    except (
        NotFoundError,
        ValidationError,
        ConflictError,
    ) as exc:
        return _domain_error_response(exc)