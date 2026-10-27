from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.extensions import db, celery
from app.core.enums.role_enums import Role
from app.modules.staff.models.staff_model import Staff
from app.modules.patient.models.patient_model import Patient

from app.core.notifications.providers.factory import (
    get_notification_provider,
)
from app.core.audit.services.audit_service import (
    create_audit_log,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.notification_enums import (
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.resilience.circuit_breaker import (
    CircuitBreakerOpenError,
)
from app.core.utils.decorators import transactional

from app.core.auth.user.models.user_model import User
from app.core.notifications.models.notification_models import (
    Notification,
)
from app.modules.clinic.services.clinic_service import (
    get_clinic,
)


# ============================================================================
# CONSTANTS
# ============================================================================

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500
MAX_DELIVERY_RETRIES = 5


# ============================================================================
# UTILITIES
# ============================================================================


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_positive_id(
    value,
    field_name,
):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )


def _normalize_required_string(
    value,
    field_name,
    max_length=None,
):
    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        raise ValidationError(
            f"{field_name} is required"
        )

    if (
        max_length is not None
        and len(value) > max_length
    ):
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _normalize_optional_string(
    value,
    field_name,
    max_length=None,
):
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        return None

    if (
        max_length is not None
        and len(value) > max_length
    ):
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _normalize_enum(
    value,
    enum_class,
    field_name,
):
    if isinstance(value, enum_class):
        return value

    if isinstance(value, str):
        value = value.strip().lower()

    try:
        return enum_class(value)
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            f"Invalid {field_name}"
        ) from exc


def _validate_pagination(
    page,
    per_page,
):
    """
    Validate standard offset pagination parameters.

    Defaults:
        page=1
        per_page=50

    Maximum:
        per_page=500
    """

    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page <= 0
    ):
        raise ValidationError(
            "Page must be a positive integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or per_page <= 0
    ):
        raise ValidationError(
            "Per-page must be a positive integer"
        )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"Per-page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _validate_bool(
    value,
    field_name,
):
    if not isinstance(value, bool):
        raise ValidationError(
            f"{field_name} must be a boolean"
        )

    return value


# ============================================================================
# USER / CLINIC HELPERS
# ============================================================================


def _get_user(
    user_id,
    *,
    clinic_id=None,
):
    _validate_positive_id(
        user_id,
        "User ID",
    )

    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        raise NotFoundError(
            f"User {user_id} not found"
        )

    if (
        clinic_id is not None
        and user.clinic_id != clinic_id
    ):
        raise NotFoundError(
            f"User {user_id} not found"
        )

    return user


def _validate_notification_target(
    clinic_id,
    user_id,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    clinic = get_clinic(
        clinic_id,
    )

    user = _get_user(
        user_id,
        clinic_id=clinic_id,
    )

    if not user.is_active:
        raise ValidationError(
            f"User {user.id} is inactive"
        )

    return clinic, user
# ============================================================================
# NOTIFICATION HELPERS
# ============================================================================




def _get_notification(
    notification_id,
    *,
    clinic_id=None,
    lock=False,
):
    _validate_positive_id(
        notification_id,
        "Notification ID",
    )

    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

    notification = db.session.get(
        Notification,
        notification_id,
        with_for_update=lock,
    )

    if notification is None:
        raise NotFoundError(
            f"Notification {notification_id} not found"
        )

    if (
        clinic_id is not None
        and notification.clinic_id != clinic_id
    ):
        raise NotFoundError(
            f"Notification {notification_id} not found"
        )

    return notification


def _normalize_channel(channel):
    return _normalize_enum(
        channel,
        NotificationChannel,
        "notification channel",
    )


def _normalize_priority(priority):
    return _normalize_enum(
        priority,
        NotificationPriority,
        "notification priority",
    )


def _normalize_type(notification_type):
    return _normalize_enum(
        notification_type,
        NotificationType,
        "notification type",
    )


# ============================================================================
# PROVIDER BOUNDARY
# ============================================================================


def _notification_idempotency_key(notification):
    return (
        f"clinic-notification-"
        f"{notification.clinic_id}-"
        f"{notification.id}"
    )


def _deliver_with_provider(notification):
    channel = notification.channel

    if channel == NotificationChannel.IN_APP:
        return False

    idempotency_key = _notification_idempotency_key(
        notification
    )

    try:
        provider = get_notification_provider(
            channel,
            clinic_id=notification.clinic_id,
        )
    except ValueError as exc:
        raise ValidationError(
            str(exc)
        ) from exc

    user = db.session.get(
        User,
        notification.user_id,
    )

    if user is None:
        raise NotFoundError(
            f"Notification recipient "
            f"{notification.user_id} not found"
        )

    if (
        user.clinic_id is not None
        and user.clinic_id != notification.clinic_id
    ):
        raise NotFoundError(
            f"Notification recipient "
            f"{notification.user_id} not found"
        )

    if not user.is_active:
        raise ValidationError(
            f"Notification recipient "
            f"{user.id} is inactive"
        )

    if channel == NotificationChannel.EMAIL:
        if user.role == Role.PATIENT:
            patient = user.patient

            if patient is None:
                raise NotFoundError(
                    f"Patient profile for user "
                    f"{user.id} not found"
                )

            if patient.clinic_id != notification.clinic_id:
                raise NotFoundError(
                    f"Patient profile for user "
                    f"{user.id} not found"
                )

            email = patient.email or user.email

        else:
            staff = user.staff

            if staff is None:
                raise NotFoundError(
                    f"Staff profile for user "
                    f"{user.id} not found"
                )

            if staff.clinic_id != notification.clinic_id:
                raise NotFoundError(
                    f"Staff profile for user "
                    f"{user.id} not found"
                )

            email = staff.email or user.email

        if not email:
            raise ValidationError(
                "Notification recipient email "
                "is not configured"
            )

        return provider.send(
            notification=notification,
            email=email,
            idempotency_key=idempotency_key,
        )

    if channel == NotificationChannel.SMS:
        if user.role == Role.PATIENT:
            patient = user.patient

            if patient is None:
                raise NotFoundError(
                    f"Patient profile for user "
                    f"{user.id} not found"
                )

            if patient.clinic_id != notification.clinic_id:
                raise NotFoundError(
                    f"Patient profile for user "
                    f"{user.id} not found"
                )

            phone = patient.phone

        else:
            staff = user.staff

            if staff is None:
                raise NotFoundError(
                    f"Staff profile for user "
                    f"{user.id} not found"
                )

            if staff.clinic_id != notification.clinic_id:
                raise NotFoundError(
                    f"Staff profile for user "
                    f"{user.id} not found"
                )

            phone = staff.phone

        if not phone:
            raise ValidationError(
                "Notification recipient phone number "
                "is not configured"
            )

        return provider.send(
            notification=notification,
            phone=phone,
            idempotency_key=idempotency_key,
        )

    if channel == NotificationChannel.PUSH:
        return provider.send(
            notification=notification,
            idempotency_key=idempotency_key,
        )

    raise ValidationError(
        "Unsupported notification channel for delivery"
    )


# ============================================================================
# CREATE NOTIFICATION
# ============================================================================


@transactional
def create_notification(
    clinic_id,
    user_id,
    title,
    message,
    notification_type,
    priority=NotificationPriority.NORMAL,
    channel=NotificationChannel.IN_APP,
    reference_type=None,
    reference_id=None,
    actor_user_id=None,
):
    """
    Create a notification for a clinic-owned user.

    Notification status is controlled by the service and
    delivery layer. Clients cannot manually create a
    SENT/DELIVERED/READ notification.
    """

    clinic, user = _validate_notification_target(
        clinic_id,
        user_id,
    )

    title = _normalize_required_string(
        title,
        "Notification title",
        max_length=255,
    )

    message = _normalize_required_string(
        message,
        "Notification message",
    )

    notification_type = _normalize_type(
        notification_type,
    )

    priority = _normalize_priority(
        priority,
    )

    channel = _normalize_channel(
        channel,
    )

    reference_type = _normalize_optional_string(
        reference_type,
        "Reference type",
        max_length=50,
    )

    if reference_id is not None:
        _validate_positive_id(
            reference_id,
            "Reference ID",
        )

    notification = Notification(
        clinic_id=clinic.id,
        user_id=user.id,
        title=title,
        message=message,
        notification_type=notification_type,
        priority=priority,
        channel=channel,
        status=NotificationStatus.PENDING,
        reference_type=reference_type,
        reference_id=reference_id,
        is_read=False,
        retry_count=0,
    )

    db.session.add(notification)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        user_id=actor_user_id,
        entity_type="Notification",
        entity_id=notification.id,
        description=(
            f"Notification created for user "
            f"{user.id}"
        ),
        new_value={
            "clinic_id": clinic.id,
            "user_id": user.id,
            "notification_type": (
                notification_type.value
            ),
            "priority": priority.value,
            "channel": channel.value,
            "status": (
                notification.status.value
            ),
            "reference_type": reference_type,
            "reference_id": reference_id,
        },
    )

    return notification


# ============================================================================
# QUEUE NOTIFICATION DELIVERY
# ============================================================================


def queue_notification_delivery(
    notification_id,
    *,
    clinic_id=None,
    user_id=None,
):
    """
    Queue an external notification for asynchronous
    delivery.

    IN_APP notifications do not require Celery delivery.
    """

    notification = _get_notification(
        notification_id,
        clinic_id=clinic_id,
    )

    if user_id is not None:
        _validate_positive_id(
            user_id,
            "User ID",
        )

        if notification.user_id != user_id:
            raise NotFoundError(
                f"Notification {notification.id} not found"
            )

    if notification.channel == NotificationChannel.IN_APP:
        return notification

    if notification.status not in (
        NotificationStatus.PENDING,
        NotificationStatus.FAILED,
    ):
        raise ConflictError(
            f"Notification {notification.id} "
            f"cannot be queued from status "
            f"'{notification.status.value}'"
        )

    if (
        notification.status == NotificationStatus.FAILED
        and notification.retry_count >= MAX_DELIVERY_RETRIES
    ):
        raise ConflictError(
            f"Notification {notification.id} has reached "
            f"the maximum delivery retry limit of "
            f"{MAX_DELIVERY_RETRIES}"
        )

    deliver_notification.delay(
        notification.clinic_id,
        notification.id,
    )

    return notification


# ============================================================================
# CELERY DELIVERY ENTRYPOINT
# ============================================================================


@celery.task(
    name="deliver_notification",
)
def deliver_notification(
    clinic_id,
    notification_id,
):
    """
    Background notification delivery entrypoint.

    Celery is responsible for asynchronous execution.

    Provider-specific delivery is isolated behind
    _deliver_with_provider().

    The task is intentionally not exposed directly through
    ordinary HTTP routes.
    """

    try:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

        _validate_positive_id(
            notification_id,
            "Notification ID",
        )
    except ValidationError:
        return False

    try:
        notification = _get_notification(
            notification_id,
            clinic_id=clinic_id,
            lock=True,
        )
    except NotFoundError:
        return False

    # Terminal states are idempotent.
    if notification.status in (
        NotificationStatus.DELIVERED,
        NotificationStatus.READ,
    ):
        return True

    # In-app notifications are persisted directly and do not
    # require an external provider.
    if notification.channel == NotificationChannel.IN_APP:
        return False

    try:
        # --------------------------------------------------------------------
        # Mark the delivery attempt as SENT.
        # --------------------------------------------------------------------

        notification.status = NotificationStatus.SENT
        notification.sent_at = _utcnow()

        db.session.flush()

        # --------------------------------------------------------------------
        # Provider boundary.
        # --------------------------------------------------------------------

        provider_success = _deliver_with_provider(
            notification,
        )

        if not provider_success:
            raise RuntimeError(
                "Notification provider rejected delivery"
            )

        # --------------------------------------------------------------------
        # Provider accepted the notification.
        # --------------------------------------------------------------------

        notification.status = (
            NotificationStatus.DELIVERED
        )
        notification.delivered_at = _utcnow()
        notification.error_message = None

        db.session.commit()

        return True

    except CircuitBreakerOpenError as exc:
        db.session.rollback()

        try:
            notification = db.session.get(
                Notification,
                notification_id,
            )

            if notification is not None:
                notification.status = (
                    NotificationStatus.FAILED
                )
                notification.failed_at = _utcnow()
                notification.error_message = str(
                    exc
                )[:2000]

                db.session.commit()

        except Exception:
            db.session.rollback()

        raise

    except Exception as exc:
        db.session.rollback()

        try:
            notification = db.session.get(
                Notification,
                notification_id,
            )

            if notification is not None:
                notification.status = (
                    NotificationStatus.FAILED
                )
                notification.failed_at = _utcnow()
                notification.retry_count = (
                    notification.retry_count + 1
                )
                notification.error_message = str(
                    exc
                )[:2000]

                db.session.commit()

        except Exception:
            db.session.rollback()

        raise


# ============================================================================
# USER NOTIFICATIONS
# ============================================================================


def get_user_notifications(
    user_id,
    clinic_id,
    *,
    unread_only=False,
    page=DEFAULT_PAGE,
    per_page=DEFAULT_PER_PAGE,
):
    """
    Return paginated notifications belonging to a
    clinic-owned user.

    Pagination:
        page=1
        per_page=50
        maximum per_page=500

    Results are ordered newest-first using both
    created_at and id for deterministic ordering.
    """

    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _get_user(
        user_id,
        clinic_id=clinic_id,
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    unread_only = _validate_bool(
        unread_only,
        "Unread-only",
    )

    filters = [
        Notification.clinic_id == clinic_id,
        Notification.user_id == user_id,
    ]

    if unread_only:
        filters.append(
            Notification.is_read.is_(False),
        )

    count_statement = (
        select(
            func.count(Notification.id)
        )
        .where(*filters)
    )

    total = db.session.execute(
        count_statement
    ).scalar_one()

    offset = (page - 1) * per_page

    items_statement = (
        select(Notification)
        .where(*filters)
        .order_by(
            Notification.created_at.desc(),
            Notification.id.desc(),
        )
        .offset(offset)
        .limit(per_page)
    )

    items = list(
        db.session.execute(
            items_statement
        ).scalars()
    )

    pages = (
        (total + per_page - 1)
        // per_page
        if total
        else 0
    )

    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_next": (
            offset + len(items)
            < total
        ),
        "has_prev": page > 1,
    }


# ============================================================================
# GET SINGLE NOTIFICATION
# ============================================================================


def get_notification_for_user(
    notification_id,
    user_id,
    clinic_id,
):
    _validate_positive_id(
        user_id,
        "User ID",
    )

    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    notification = _get_notification(
        notification_id,
        clinic_id=clinic_id,
    )

    if notification.user_id != user_id:
        raise NotFoundError(
            f"Notification {notification_id} not found"
        )

    return notification


# ============================================================================
# MARK NOTIFICATION READ
# ============================================================================


@transactional
def mark_notification_read(
    notification_id,
    user_id,
    clinic_id,
):
    """
    Mark one notification as read.
    """

    _validate_positive_id(
        user_id,
        "User ID",
    )

    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    notification = _get_notification(
        notification_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if notification.user_id != user_id:
        raise NotFoundError(
            f"Notification {notification_id} not found"
        )

    if notification.is_read:
        return notification

    old_status = notification.status
    old_is_read = notification.is_read

    notification.is_read = True
    notification.read_at = _utcnow()
    notification.status = NotificationStatus.READ

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Notification",
        entity_id=notification.id,
        description="Notification marked as read",
        old_value={
            "status": old_status.value,
            "is_read": old_is_read,
        },
        new_value={
            "status": NotificationStatus.READ.value,
            "is_read": True,
            "read_at": notification.read_at.isoformat(),
        },
    )

    return notification


# ============================================================================
# MARK ALL NOTIFICATIONS READ
# ============================================================================


@transactional
def mark_all_notifications_read(
    user_id,
    clinic_id,
):
    """
    Mark every unread notification for a user as read.

    This remains a bulk mutation rather than a paginated
    collection endpoint.
    """

    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _get_user(
        user_id,
        clinic_id=clinic_id,
    )

    statement = (
        select(Notification)
        .where(
            Notification.clinic_id == clinic_id,
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
        .with_for_update()
    )

    notifications = list(
        db.session.execute(
            statement
        ).scalars()
    )

    if not notifications:
        return 0

    read_at = _utcnow()

    for notification in notifications:
        notification.is_read = True
        notification.read_at = read_at
        notification.status = NotificationStatus.READ

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="Notification",
        entity_id=user_id,
        description=(
            f"{len(notifications)} notifications "
            f"marked as read for user {user_id}"
        ),
        new_value={
            "user_id": user_id,
            "count": len(notifications),
        },
    )

    return len(notifications)


# ============================================================================
# DELIVERY STATUS
# ============================================================================


@transactional
def update_notification_delivery_status(
    notification_id,
    status,
    *,
    error_message=None,
):
    """
    Internal operation used by delivery workers.

    This should not be exposed directly to ordinary
    authenticated HTTP clients.
    """

    notification = _get_notification(
        notification_id,
        lock=True,
    )

    status = _normalize_enum(
        status,
        NotificationStatus,
        "notification status",
    )

    error_message = _normalize_optional_string(
        error_message,
        "Error message",
        max_length=2000,
    )

    old_status = notification.status

    notification.status = status

    now = _utcnow()

    if status == NotificationStatus.SENT:
        notification.sent_at = (
            notification.sent_at or now
        )

    elif status == NotificationStatus.DELIVERED:
        notification.sent_at = (
            notification.sent_at or now
        )
        notification.delivered_at = now
        notification.error_message = None

    elif status == NotificationStatus.READ:
        notification.is_read = True
        notification.read_at = (
            notification.read_at or now
        )
        notification.delivered_at = (
            notification.delivered_at or now
        )
        notification.error_message = None

    elif status == NotificationStatus.FAILED:
        notification.failed_at = now
        notification.retry_count += 1
        notification.error_message = error_message

    elif status == NotificationStatus.PENDING:
        notification.error_message = None

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Notification",
        entity_id=notification.id,
        description=(
            "Notification delivery status changed"
        ),
        old_value={
            "status": old_status.value,
        },
        new_value={
            "status": notification.status.value,
            "error_message": notification.error_message,
            "retry_count": notification.retry_count,
        },
    )

    return notification


# ============================================================================
# RETRY FAILED NOTIFICATION
# ============================================================================


@transactional
def retry_notification(
    notification_id,
):
    """
    Reset a failed notification to pending.

    The caller may queue delivery after the transaction
    has successfully committed.
    """

    notification = _get_notification(
        notification_id,
        lock=True,
    )

    if notification.status != NotificationStatus.FAILED:
        raise ConflictError(
            "Only failed notifications can be retried"
        )

    if notification.channel == NotificationChannel.IN_APP:
        raise ConflictError(
            "In-app notifications do not require delivery retry"
        )

    old_status = notification.status

    notification.status = NotificationStatus.PENDING
    notification.sent_at = None
    notification.delivered_at = None
    notification.failed_at = None
    notification.error_message = None

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Notification",
        entity_id=notification.id,
        description=(
            "Failed notification reset for retry"
        ),
        old_value={
            "status": old_status.value,
        },
        new_value={
            "status": notification.status.value,
            "retry_count": notification.retry_count,
        },
    )

    db.session.flush()

    return notification
