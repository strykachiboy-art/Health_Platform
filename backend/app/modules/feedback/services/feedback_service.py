from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func

from app.core.auth.user.models.user_model import User
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_safety_enums import ClinicalRuleScope
from app.core.enums.feedback_enums import (
    FeedbackCategory,
    FeedbackPriority,
    FeedbackSource,
    FeedbackStatus,
    FeedbackType,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.core.idempotency.services.idempotency_service import normalize_idempotency_key, reserve_idempotency_operation
from app.core.offline_operations.services.disposition_service import assert_operation_not_disposed
from app.extensions import db
from app.modules.appointment.models.appointment_model import Appointment
from app.modules.asset_control.models.asset_model import Asset
from app.modules.ambulance.models.ambulance_model import AmbulanceTrip
from app.modules.billing.models.billing_model import Invoice
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.message_model import Message
from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.feedback.models.feedback_model import Feedback
from app.modules.hie.models.hie_model import HIESubmission
from app.modules.inventory.models.inventory_model import InventoryItem
from app.modules.lab.models.lab_model import LabOrder
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.prescription.models.prescription_model import Prescription
from app.modules.reports.models.reports_model import GeneratedReport
from app.modules.settings.models.clinic_settings import ClinicSettings
from app.modules.ward.models.ward_model import Admission
from app.core.clinical_safety.models.clinical_alert_model import ClinicalAlert
from app.core.clinical_safety.models.clinical_rule_model import ClinicalRule
from app.core.emergency_access.models.emergency_access_model import (
    EmergencyAccessGrant,
)
from app.core.notifications.models.notification_models import Notification
from app.modules.staff.models.staff_model import Staff

from app.modules.feedback.schemas.feedback_schema import (
    FeedbackCreateSchema,
    FeedbackManageSchema,
)
from app.modules.feedback.schemas.feedback_query_schema import (
    FeedbackListQuerySchema,
)


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500

MANAGE_ROLES = {
    Role.ADMIN,
    Role.SUPER_ADMIN,
}

TARGET_RESOURCES = {
    ("patient", "patient"): Patient,
    ("appointment", "appointment"): Appointment,
    ("consultation", "consultation"): Consultation,
    ("laboratory", "lab_order"): LabOrder,
    ("pharmacy", "drug"): Drug,
    ("prescription", "prescription"): Prescription,
    ("inventory", "inventory_item"): InventoryItem,
    ("billing", "invoice"): Invoice,
    ("ward", "admission"): Admission,
    ("ambulance", "ambulance_trip"): AmbulanceTrip,
    ("asset_control", "asset"): Asset,
    ("chat", "conversation"): Conversation,
    ("chat", "message"): Message,
    ("hie", "hie_submission"): HIESubmission,
    ("reports", "generated_report"): GeneratedReport,
    ("notifications", "notification"): Notification,
    ("settings", "clinic_settings"): ClinicSettings,
    ("staff", "staff"): Staff,
    ("emergency_access", "emergency_access_grant"): EmergencyAccessGrant,
    ("clinical_safety", "clinical_rule"): ClinicalRule,
    ("clinical_safety", "clinical_alert"): ClinicalAlert,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_positive_id(
    value: Any,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _validate_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    page = _validate_positive_id(page, "Page")
    per_page = _validate_positive_id(per_page, "Per-page")

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"Per-page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _normalize_enum(
    value: Any,
    enum_class,
    field_name: str,
):
    if isinstance(value, enum_class):
        return value

    try:
        return enum_class(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid {field_name}"
        ) from exc


def _get_actor(
    actor_user_id: int,
) -> User:
    actor_user_id = _validate_positive_id(
        actor_user_id,
        "Actor user ID",
    )

    actor = db.session.get(
        User,
        actor_user_id,
    )

    if actor is None:
        raise NotFoundError(
            f"User {actor_user_id} not found"
        )

    if not actor.is_active:
        raise ValidationError(
            "Authenticated user is inactive"
        )

    return actor


def _actor_role(actor: User) -> Role:
    try:
        return (
            actor.role
            if isinstance(actor.role, Role)
            else Role(actor.role)
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Authenticated user has an invalid role"
        ) from exc


def _resolve_actor_clinic(
    actor: User,
    clinic_id: int | None = None,
) -> int:
    role = _actor_role(actor)

    if role == Role.SUPER_ADMIN:
        if clinic_id is None:
            raise ValidationError(
                "Clinic ID is required for super-admin operations"
            )

        return _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

    if actor.clinic_id is None:
        raise ValidationError(
            "Authenticated user is not assigned to a clinic"
        )

    actor_clinic_id = _validate_positive_id(
        actor.clinic_id,
        "Actor clinic ID",
    )

    if (
        clinic_id is not None
        and clinic_id != actor_clinic_id
    ):
        raise NotFoundError(
            "Requested feedback scope was not found"
        )

    return actor_clinic_id


def _ensure_clinic_access(
    actor: User,
    clinic_id: int,
) -> None:
    role = _actor_role(actor)

    if role == Role.SUPER_ADMIN:
        return

    if actor.clinic_id != clinic_id:
        raise NotFoundError(
            "Requested feedback scope was not found"
        )


def _is_active_staff(
    user_id: int,
    clinic_id: int,
) -> bool:
    statement = db.select(Staff.id).where(
        Staff.user_id == user_id,
        Staff.clinic_id == clinic_id,
        Staff.status == StaffStatus.ACTIVE,
    )

    return (
        db.session.execute(statement)
        .scalar_one_or_none()
        is not None
    )


def _can_manage_feedback(
    actor: User,
) -> bool:
    return _actor_role(actor) in MANAGE_ROLES


def _can_view_all_feedback(
    actor: User,
    clinic_id: int,
) -> bool:
    if _can_manage_feedback(actor):
        return True

    return _is_active_staff(
        actor.id,
        clinic_id,
    )


def _get_feedback(
    feedback_id: int,
    *,
    clinic_id: int | None = None,
    lock: bool = False,
) -> Feedback:
    feedback_id = _validate_positive_id(
        feedback_id,
        "Feedback ID",
    )

    statement = db.select(
        Feedback
    ).where(
        Feedback.id == feedback_id,
    )

    if clinic_id is not None:
        statement = statement.where(
            Feedback.clinic_id == clinic_id,
        )

    if lock:
        statement = statement.with_for_update()

    feedback = (
        db.session.execute(statement)
        .scalar_one_or_none()
    )

    if feedback is None:
        raise NotFoundError(
            f"Feedback {feedback_id} not found"
        )

    return feedback


def _get_user_in_clinic(
    user_id: int,
    clinic_id: int,
    *,
    active_only: bool = False,
) -> User:
    user_id = _validate_positive_id(
        user_id,
        "User ID",
    )

    statement = db.select(User).where(
        User.id == user_id,
        User.clinic_id == clinic_id,
    )

    if active_only:
        statement = statement.where(
            User.is_active.is_(True),
        )

    user = (
        db.session.execute(statement)
        .scalar_one_or_none()
    )

    if user is None:
        raise NotFoundError(
            f"User {user_id} not found"
        )

    return user


def _validate_target_actor_access(
    *,
    actor_user_id: int,
    clinic_id: int,
    model: type,
    target: object,
) -> None:
    actor = _get_actor(actor_user_id)
    role = _actor_role(actor)

    if role == Role.SUPER_ADMIN:
        return

    _get_user_in_clinic(
        actor_user_id,
        clinic_id,
        active_only=True,
    )

    if role == Role.ADMIN:
        return

    if _is_active_staff(
        user_id=actor_user_id,
        clinic_id=clinic_id,
    ):
        return

    patient = db.session.execute(
        db.select(Patient).where(
            Patient.user_id == actor_user_id,
            Patient.clinic_id == clinic_id,
            Patient.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if patient is None:
        raise NotFoundError(
            "Feedback target resource not found"
        )

    if model is Patient:
        allowed = target.user_id == actor_user_id
    else:
        target_patient_id = getattr(
            target,
            "patient_id",
            None,
        )
        allowed = target_patient_id == patient.id

    if not allowed:
        raise NotFoundError(
            "Feedback target resource not found"
        )


def _get_target_clinic_id(
    *,
    model: type,
    target: object,
) -> int | None:
    clinic_id = getattr(
        target,
        "clinic_id",
        None,
    )

    if clinic_id is not None:
        return clinic_id

    if model is Admission:
        return (
            db.session.execute(
                db.select(Patient.clinic_id).where(
                    Patient.id == target.patient_id,
                )
            )
            .scalar_one_or_none()
        )

    return None


def _validate_target(
    *,
    actor_user_id: int,
    clinic_id: int,
    target_module: str | None,
    target_resource_type: str | None,
    target_resource_id: int | None,
) -> None:
    fields = (
        target_module,
        target_resource_type,
        target_resource_id,
    )

    supplied = sum(
        value is not None
        for value in fields
    )

    if supplied == 0:
        return

    if supplied != 3:
        raise ValidationError(
            "Target module, resource type, and resource ID "
            "must either all be provided or all be omitted"
        )

    target_module = target_module.strip().lower()
    target_resource_type = (
        target_resource_type.strip().lower()
    )

    model = TARGET_RESOURCES.get(
        (
            target_module,
            target_resource_type,
        )
    )

    if model is None:
        raise ValidationError(
            "Unsupported feedback target resource"
        )

    target_resource_id = _validate_positive_id(
        target_resource_id,
        "Target resource ID",
    )

    target = db.session.execute(
        db.select(model).where(
            model.id == target_resource_id,
        )
    ).scalar_one_or_none()

    if target is None:
        raise NotFoundError(
            "Feedback target resource not found"
        )

    model_clinic_id = _get_target_clinic_id(
        model=model,
        target=target,
    )

    if model_clinic_id is None:
        if (
            model is ClinicalRule
            and getattr(target, "scope", None)
            == ClinicalRuleScope.GLOBAL
        ):
            _validate_target_actor_access(
                actor_user_id=actor_user_id,
                clinic_id=clinic_id,
                model=model,
                target=target,
            )
            return

        raise ValidationError(
            "Feedback target resource is not clinic-scoped"
        )

    if model_clinic_id != clinic_id:
        raise NotFoundError(
            "Feedback target resource not found"
        )

    _validate_target_actor_access(
        actor_user_id=actor_user_id,
        clinic_id=clinic_id,
        model=model,
        target=target,
    )


def _validate_assignment(
    *,
    clinic_id: int,
    assigned_to_user_id: int | None,
) -> None:
    if assigned_to_user_id is None:
        return

    _get_user_in_clinic(
        assigned_to_user_id,
        clinic_id,
        active_only=True,
    )

    if not _is_active_staff(
        user_id=assigned_to_user_id,
        clinic_id=clinic_id,
    ):
        raise ValidationError(
            "Assigned user must be active clinic staff."
        )


def _validate_status_transition(
    current_status: FeedbackStatus,
    new_status: FeedbackStatus,
) -> None:
    if current_status == new_status:
        return

    allowed = {
        FeedbackStatus.OPEN: {
            FeedbackStatus.TRIAGED,
            FeedbackStatus.REJECTED,
        },
        FeedbackStatus.TRIAGED: {
            FeedbackStatus.IN_PROGRESS,
            FeedbackStatus.REJECTED,
        },
        FeedbackStatus.IN_PROGRESS: {
            FeedbackStatus.RESOLVED,
            FeedbackStatus.REJECTED,
        },
        FeedbackStatus.RESOLVED: {
            FeedbackStatus.CLOSED,
            FeedbackStatus.REOPENED,
        },
        FeedbackStatus.CLOSED: {
            FeedbackStatus.REOPENED,
        },
        FeedbackStatus.REOPENED: {
            FeedbackStatus.TRIAGED,
            FeedbackStatus.IN_PROGRESS,
        },
        FeedbackStatus.REJECTED: {
            FeedbackStatus.REOPENED,
        },
    }

    if new_status not in allowed.get(
        current_status,
        set(),
    ):
        raise ConflictError(
            f"Feedback cannot transition from "
            f"'{current_status.value}' to "
            f"'{new_status.value}'"
        )


def _apply_status_transition(
    feedback: Feedback,
    new_status: FeedbackStatus,
) -> None:
    current_status = feedback.status

    _validate_status_transition(
        current_status,
        new_status,
    )

    if current_status == new_status:
        return

    now = _utcnow()

    feedback.status = new_status

    if new_status == FeedbackStatus.RESOLVED:
        feedback.resolved_at = now
        feedback.closed_at = None

    elif new_status == FeedbackStatus.CLOSED:
        if feedback.resolved_at is None:
            feedback.resolved_at = now

        feedback.closed_at = now

    elif new_status == FeedbackStatus.REOPENED:
        feedback.resolution_note = None
        feedback.resolved_at = None
        feedback.closed_at = None

    elif new_status in (
        FeedbackStatus.TRIAGED,
        FeedbackStatus.IN_PROGRESS,
        FeedbackStatus.REJECTED,
    ):
        feedback.closed_at = None


def _feedback_snapshot(
    feedback: Feedback,
) -> dict[str, Any]:
    return {
        "status": feedback.status.value,
        "priority": feedback.priority.value,
        "assigned_to_user_id": (
            feedback.assigned_to_user_id
        ),
        "resolution_note": (
            feedback.resolution_note
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


def _create_feedback_audit(
    *,
    action: AuditAction,
    feedback: Feedback,
    actor_user_id: int,
    old_value=None,
    new_value=None,
    description: str | None = None,
) -> None:
    create_audit_log(
        action=action,
        entity_type="Feedback",
        entity_id=feedback.id,
        description=description
        or f"Feedback {feedback.id} updated",
        old_value=old_value,
        new_value=(
            new_value
            if new_value is not None
            else {
                "actor_user_id": actor_user_id,
            }
        ),
        user_id=actor_user_id,
        clinic_id=feedback.clinic_id,
    )


def _create_feedback_without_nested_transaction(
    *,
    actor_user_id: int,
    payload: FeedbackCreateSchema,
    clinic_id: int | None = None,
    source: FeedbackSource,
) -> Feedback:
    actor = _get_actor(
        actor_user_id,
    )

    role = _actor_role(actor)

    if role == Role.SUPER_ADMIN:
        resolved_clinic_id = _resolve_actor_clinic(
            actor,
            clinic_id,
        )
    else:
        resolved_clinic_id = _resolve_actor_clinic(
            actor,
        )

    ensure_clinic_active(
        resolved_clinic_id,
    )

    _validate_target(
        actor_user_id=actor_user_id,
        clinic_id=resolved_clinic_id,
        target_module=payload.target_module,
        target_resource_type=(
            payload.target_resource_type
        ),
        target_resource_id=(
            payload.target_resource_id
        ),
    )

    feedback = Feedback(
        clinic_id=resolved_clinic_id,
        submitted_by_user_id=actor.id,
        feedback_type=payload.feedback_type,
        category=payload.category,
        subject=payload.subject,
        message=payload.message,
        status=FeedbackStatus.OPEN,
        priority=FeedbackPriority.NORMAL,
        source=source,
        target_module=payload.target_module,
        target_resource_type=(
            payload.target_resource_type
        ),
        target_resource_id=(
            payload.target_resource_id
        ),
    )

    db.session.add(feedback)
    db.session.flush()

    _create_feedback_audit(
        action=AuditAction.CREATE,
        feedback=feedback,
        actor_user_id=actor.id,
        new_value={
            "clinic_id": resolved_clinic_id,
            "submitted_by_user_id": actor.id,
            "feedback_type": (
                feedback.feedback_type.value
            ),
            "category": feedback.category.value,
            "status": feedback.status.value,
            "priority": feedback.priority.value,
            "source": feedback.source.value,
        },
        description=(
            f"Feedback {feedback.id} created"
        ),
    )

    return feedback


@transactional
def create_feedback(
    *,
    actor_user_id: int,
    payload: FeedbackCreateSchema,
    clinic_id: int | None = None,
    source: FeedbackSource = FeedbackSource.API,
    idempotency_key: str | None = None,
) -> Feedback:
    source = _normalize_enum(
        source,
        FeedbackSource,
        "feedback source",
    )

    if source == FeedbackSource.SYSTEM:
        raise ValidationError(
            "System feedback must use create_system_feedback"
        )

    # Keyed contract is deliberately narrower than ordinary online feedback.
    record = None
    if idempotency_key is not None:
        actor = _get_actor(actor_user_id)
        if _actor_role(actor) == Role.SUPER_ADMIN or clinic_id is None:
            raise ValidationError("Keyed feedback requires an active clinic context")
        resolved = _resolve_actor_clinic(actor, clinic_id)
        ensure_clinic_active(resolved)
        if (source != FeedbackSource.API or payload.target_module is not None
                or payload.target_resource_type is not None or payload.target_resource_id is not None
                or payload.feedback_type not in (FeedbackType.PRODUCT_FEEDBACK, FeedbackType.BUG_REPORT, FeedbackType.FEATURE_REQUEST)
                or payload.category not in (FeedbackCategory.USABILITY, FeedbackCategory.PERFORMANCE, FeedbackCategory.ACCESSIBILITY)):
            raise ValidationError("Keyed feedback is limited to untargeted product feedback")
        idempotency_key = normalize_idempotency_key(idempotency_key)
        assert_operation_not_disposed(clinic_id=resolved, user_id=actor.id, operation_type="feedback.create", idempotency_key=idempotency_key)
        record, created = reserve_idempotency_operation(
            clinic_id=resolved, user_id=actor.id, operation="feedback.create",
            idempotency_key=idempotency_key, request_payload=payload.model_dump(mode="json"),
        )
        if not created:
            existing = db.session.get(Feedback, record.entity_id) if record.entity_type == "Feedback" and record.entity_id is not None else None
            if (existing is None or existing.clinic_id != resolved
                    or existing.submitted_by_user_id != actor.id):
                raise ConflictError("Feedback operation requires reconciliation", code="IDEMPOTENCY_RECORD_UNRESOLVED")
            return existing
    feedback = _create_feedback_without_nested_transaction(
        actor_user_id=actor_user_id,
        payload=payload,
        clinic_id=clinic_id,
        source=source,
    )
    if record is not None:
        record.entity_type = "Feedback"
        record.entity_id = feedback.id
    return feedback


@transactional
def create_system_feedback(
    *,
    actor_user_id: int,
    payload: FeedbackCreateSchema,
    clinic_id: int | None = None,
) -> Feedback:
    return _create_feedback_without_nested_transaction(
        actor_user_id=actor_user_id,
        payload=payload,
        clinic_id=clinic_id,
        source=FeedbackSource.SYSTEM,
    )


def get_feedback_for_actor(
    *,
    actor_user_id: int,
    feedback_id: int,
    clinic_id: int | None = None,
) -> Feedback:
    actor = _get_actor(
        actor_user_id,
    )

    role = _actor_role(actor)

    if role == Role.SUPER_ADMIN:
        feedback = _get_feedback(
            feedback_id,
            clinic_id=clinic_id,
        )
        return feedback

    resolved_clinic_id = _resolve_actor_clinic(
        actor,
    )

    feedback = _get_feedback(
        feedback_id,
        clinic_id=resolved_clinic_id,
    )

    if feedback.submitted_by_user_id == actor.id:
        return feedback

    if _can_view_all_feedback(
        actor,
        resolved_clinic_id,
    ):
        return feedback

    raise NotFoundError(
        f"Feedback {feedback_id} not found"
    )


def list_feedback(
    *,
    actor_user_id: int,
    query: FeedbackListQuerySchema,
    clinic_id: int | None = None,
) -> dict[str, Any]:
    actor = _get_actor(
        actor_user_id,
    )

    role = _actor_role(actor)

    page, per_page = _validate_pagination(
        query.page,
        query.per_page,
    )

    if role == Role.SUPER_ADMIN:
        resolved_clinic_id = (
            _validate_positive_id(
                clinic_id,
                "Clinic ID",
            )
            if clinic_id is not None
            else None
        )
    else:
        resolved_clinic_id = _resolve_actor_clinic(
            actor,
        )

    statement = db.select(
        Feedback
    )

    count_statement = db.select(
        func.count(Feedback.id)
    )

    if resolved_clinic_id is not None:
        statement = statement.where(
            Feedback.clinic_id
            == resolved_clinic_id,
        )

        count_statement = count_statement.where(
            Feedback.clinic_id
            == resolved_clinic_id,
        )

    if not _can_manage_feedback(actor):
        if not _can_view_all_feedback(
            actor,
            resolved_clinic_id or actor.clinic_id,
        ):
            statement = statement.where(
                Feedback.submitted_by_user_id
                == actor.id,
            )

            count_statement = count_statement.where(
                Feedback.submitted_by_user_id
                == actor.id,
            )

    if query.feedback_type is not None:
        statement = statement.where(
            Feedback.feedback_type
            == query.feedback_type,
        )
        count_statement = count_statement.where(
            Feedback.feedback_type
            == query.feedback_type,
        )

    if query.category is not None:
        statement = statement.where(
            Feedback.category
            == query.category,
        )
        count_statement = count_statement.where(
            Feedback.category
            == query.category,
        )

    if query.status is not None:
        statement = statement.where(
            Feedback.status
            == query.status,
        )
        count_statement = count_statement.where(
            Feedback.status
            == query.status,
        )

    if query.priority is not None:
        statement = statement.where(
            Feedback.priority
            == query.priority,
        )
        count_statement = count_statement.where(
            Feedback.priority
            == query.priority,
        )

    if query.source is not None:
        statement = statement.where(
            Feedback.source
            == query.source,
        )
        count_statement = count_statement.where(
            Feedback.source
            == query.source,
        )

    if query.assigned_to_user_id is not None:
        statement = statement.where(
            Feedback.assigned_to_user_id
            == query.assigned_to_user_id,
        )
        count_statement = count_statement.where(
            Feedback.assigned_to_user_id
            == query.assigned_to_user_id,
        )

    if query.submitted_by_user_id is not None:
        statement = statement.where(
            Feedback.submitted_by_user_id
            == query.submitted_by_user_id,
        )
        count_statement = count_statement.where(
            Feedback.submitted_by_user_id
            == query.submitted_by_user_id,
        )

    if query.target_module is not None:
        statement = statement.where(
            Feedback.target_module
            == query.target_module,
        )
        count_statement = count_statement.where(
            Feedback.target_module
            == query.target_module,
        )

    if query.created_from is not None:
        statement = statement.where(
            Feedback.created_at
            >= query.created_from,
        )
        count_statement = count_statement.where(
            Feedback.created_at
            >= query.created_from,
        )

    if query.created_to is not None:
        statement = statement.where(
            Feedback.created_at
            <= query.created_to,
        )
        count_statement = count_statement.where(
            Feedback.created_at
            <= query.created_to,
        )

    total = db.session.execute(
        count_statement,
    ).scalar_one()

    items = (
        db.session.execute(
            statement
            .order_by(
                Feedback.created_at.desc(),
                Feedback.id.desc(),
            )
            .offset(
                (page - 1) * per_page
            )
            .limit(per_page)
        )
        .scalars()
        .all()
    )

    pages = (
        (total + per_page - 1) // per_page
        if total
        else 0
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "has_next": page < pages,
        "has_previous": (
            page > 1 and total > 0
        ),
    }


@transactional
def manage_feedback(
    *,
    actor_user_id: int,
    feedback_id: int,
    payload: FeedbackManageSchema,
    clinic_id: int | None = None,
) -> Feedback:
    actor = _get_actor(
        actor_user_id,
    )

    if not _can_manage_feedback(
        actor,
    ):
        raise ValidationError(
            "You are not authorized to manage feedback"
        )

    role = _actor_role(actor)

    resolved_clinic_id = (
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )
        if role == Role.SUPER_ADMIN
        else _resolve_actor_clinic(
            actor,
        )
    )

    ensure_clinic_active(
        resolved_clinic_id,
    )

    feedback = _get_feedback(
        feedback_id,
        clinic_id=resolved_clinic_id,
        lock=True,
    )

    old_value = _feedback_snapshot(
        feedback,
    )

    values = payload.model_dump(
        exclude_unset=True,
    )

    if "priority" in values:
        feedback.priority = _normalize_enum(
            values["priority"],
            FeedbackPriority,
            "feedback priority",
        )

    if "assigned_to_user_id" in values:
        _validate_assignment(
            clinic_id=resolved_clinic_id,
            assigned_to_user_id=(
                values["assigned_to_user_id"]
            ),
        )

        feedback.assigned_to_user_id = (
            values["assigned_to_user_id"]
        )

    if "resolution_note" in values:
        feedback.resolution_note = (
            values["resolution_note"]
        )

    if "status" in values:
        new_status = _normalize_enum(
            values["status"],
            FeedbackStatus,
            "feedback status",
        )

        if (
            new_status == FeedbackStatus.RESOLVED
            and not feedback.resolution_note
        ):
            raise ValidationError(
                "Resolution note is required when resolving feedback"
            )

        _apply_status_transition(
            feedback,
            new_status,
        )

    new_value = _feedback_snapshot(
        feedback,
    )

    if old_value != new_value:
        action = (
            AuditAction.STATUS_CHANGE
            if old_value["status"]
            != new_value["status"]
            else AuditAction.UPDATE
        )

        _create_feedback_audit(
            action=action,
            feedback=feedback,
            actor_user_id=actor.id,
            old_value=old_value,
            new_value=new_value,
        )

    return feedback


@transactional
def resolve_feedback(
    *,
    actor_user_id: int,
    feedback_id: int,
    resolution_note: str,
    clinic_id: int | None = None,
) -> Feedback:
    resolution_note = (
        resolution_note.strip()
        if isinstance(resolution_note, str)
        else resolution_note
    )

    if not resolution_note:
        raise ValidationError(
            "Resolution note is required"
        )

    payload = FeedbackManageSchema(
        status=FeedbackStatus.RESOLVED,
        resolution_note=resolution_note,
    )

    return _manage_feedback_without_nested_transaction(
        actor_user_id=actor_user_id,
        feedback_id=feedback_id,
        payload=payload,
        clinic_id=clinic_id,
    )


@transactional
def reopen_feedback(
    *,
    actor_user_id: int,
    feedback_id: int,
    clinic_id: int | None = None,
) -> Feedback:
    payload = FeedbackManageSchema(
        status=FeedbackStatus.REOPENED,
    )

    return _manage_feedback_without_nested_transaction(
        actor_user_id=actor_user_id,
        feedback_id=feedback_id,
        payload=payload,
        clinic_id=clinic_id,
    )


@transactional
def close_feedback(
    *,
    actor_user_id: int,
    feedback_id: int,
    clinic_id: int | None = None,
) -> Feedback:
    payload = FeedbackManageSchema(
        status=FeedbackStatus.CLOSED,
    )

    return _manage_feedback_without_nested_transaction(
        actor_user_id=actor_user_id,
        feedback_id=feedback_id,
        payload=payload,
        clinic_id=clinic_id,
    )


@transactional
def reject_feedback(
    *,
    actor_user_id: int,
    feedback_id: int,
    resolution_note: str | None = None,
    clinic_id: int | None = None,
) -> Feedback:
    payload = FeedbackManageSchema(
        status=FeedbackStatus.REJECTED,
        resolution_note=resolution_note,
    )

    return _manage_feedback_without_nested_transaction(
        actor_user_id=actor_user_id,
        feedback_id=feedback_id,
        payload=payload,
        clinic_id=clinic_id,
    )


def _manage_feedback_without_nested_transaction(
    *,
    actor_user_id: int,
    feedback_id: int,
    payload: FeedbackManageSchema,
    clinic_id: int | None = None,
) -> Feedback:
    actor = _get_actor(
        actor_user_id,
    )

    if not _can_manage_feedback(actor):
        raise ValidationError(
            "You are not authorized to manage feedback"
        )

    role = _actor_role(actor)

    resolved_clinic_id = (
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )
        if role == Role.SUPER_ADMIN
        else _resolve_actor_clinic(
            actor,
        )
    )

    ensure_clinic_active(
        resolved_clinic_id,
    )

    feedback = _get_feedback(
        feedback_id,
        clinic_id=resolved_clinic_id,
        lock=True,
    )

    old_value = _feedback_snapshot(
        feedback,
    )

    values = payload.model_dump(
        exclude_unset=True,
    )

    if "resolution_note" in values:
        resolution_note = values["resolution_note"]

        if (
            resolution_note is not None
            and not resolution_note.strip()
        ):
            resolution_note = None

        feedback.resolution_note = resolution_note

    if "priority" in values:
        feedback.priority = _normalize_enum(
            values["priority"],
            FeedbackPriority,
            "feedback priority",
        )

    if "assigned_to_user_id" in values:
        _validate_assignment(
            clinic_id=resolved_clinic_id,
            assigned_to_user_id=(
                values["assigned_to_user_id"]
            ),
        )

        feedback.assigned_to_user_id = (
            values["assigned_to_user_id"]
        )

    if "status" in values:
        new_status = _normalize_enum(
            values["status"],
            FeedbackStatus,
            "feedback status",
        )

        if (
            new_status == FeedbackStatus.RESOLVED
            and not feedback.resolution_note
        ):
            raise ValidationError(
                "Resolution note is required when resolving feedback"
            )

        _apply_status_transition(
            feedback,
            new_status,
        )

    new_value = _feedback_snapshot(
        feedback,
    )

    if old_value != new_value:
        action = (
            AuditAction.STATUS_CHANGE
            if old_value["status"]
            != new_value["status"]
            else AuditAction.UPDATE
        )

        _create_feedback_audit(
            action=action,
            feedback=feedback,
            actor_user_id=actor.id,
            old_value=old_value,
            new_value=new_value,
        )

    return feedback