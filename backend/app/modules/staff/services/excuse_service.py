from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

from app.extensions import db

from app.core.audit.services.audit_service import create_audit_log
from app.core.auth.user.models.user_model import User
from app.core.enums.audit_enums import AuditAction
from app.core.enums.excuse_enums import (
    ExcuseStatus,
    ExcuseType,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.modules.staff.services.staff_service import require_platform_review_context

from app.modules.staff.models.excuse_model import Excuse
from app.modules.staff.models.staff_model import (
    LeaveRequest,
    Staff,
)


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500

MAX_DESCRIPTION_LENGTH = 2000
MAX_DOCUMENT_URL_LENGTH = 500
MAX_REJECTION_REASON_LENGTH = 2000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _db_now() -> datetime:
    return _utcnow().replace(tzinfo=None)


def _validate_positive_id(
    value,
    field_name: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    if value <= 0:
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _validate_pagination(
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> tuple[int, int]:
    page = _validate_positive_id(
        page,
        "page",
    )

    per_page = _validate_positive_id(
        per_page,
        "per_page",
    )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"per_page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _paginate(
    statement,
    count_statement,
    page: int,
    per_page: int,
) -> dict:
    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    total = db.session.execute(
        count_statement
    ).scalar_one()

    items = list(
        db.session.execute(
            statement
            .offset(
                (page - 1) * per_page
            )
            .limit(per_page)
        ).scalars()
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


def _ensure_active_clinic(
    clinic_id: int,
) -> None:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    from app.modules.clinic.models.clinic_model import Clinic

    clinic = db.session.get(
        Clinic,
        clinic_id,
    )

    if clinic is None:
        raise NotFoundError(
            f"Clinic {clinic_id} not found"
        )

    status = getattr(
        clinic.status,
        "value",
        clinic.status,
    )

    if status != "active":
        raise ConflictError(
            f"Clinic {clinic_id} is not active"
        )


def _get_staff(
    staff_id: int,
    clinic_id: Optional[int] = None,
    lock: bool = False,
) -> Staff:
    staff_id = _validate_positive_id(
        staff_id,
        "staff_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

    statement = select(Staff).where(
        Staff.id == staff_id,
    )

    if clinic_id is not None:
        statement = statement.where(
            Staff.clinic_id == clinic_id,
        )

    if lock:
        statement = statement.with_for_update()

    staff = db.session.execute(
        statement
    ).scalar_one_or_none()

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    return staff


def _get_user(
    user_id: int,
    clinic_id: Optional[int] = None,
) -> User:
    user_id = _validate_positive_id(
        user_id,
        "user_id",
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


def _get_excuse(
    excuse_id: int,
    clinic_id: Optional[int] = None,
    lock: bool = False,
) -> Excuse:
    excuse_id = _validate_positive_id(
        excuse_id,
        "excuse_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

    statement = (
        select(Excuse)
        .join(
            Staff,
            Excuse.staff_id == Staff.id,
        )
        .where(
            Excuse.id == excuse_id,
        )
    )

    if clinic_id is not None:
        statement = statement.where(
            Staff.clinic_id == clinic_id,
        )

    if lock:
        statement = statement.with_for_update()

    excuse = db.session.execute(
        statement
    ).scalar_one_or_none()

    if excuse is None:
        raise NotFoundError(
            f"Excuse {excuse_id} not found"
        )

    return excuse


def _get_leave_request(
    leave_request_id: int,
    clinic_id: int,
    lock: bool = False,
) -> LeaveRequest:
    leave_request_id = _validate_positive_id(
        leave_request_id,
        "leave_request_id",
    )

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = (
        select(LeaveRequest)
        .join(
            Staff,
            LeaveRequest.staff_id == Staff.id,
        )
        .where(
            LeaveRequest.id == leave_request_id,
            Staff.clinic_id == clinic_id,
        )
    )

    if lock:
        statement = statement.with_for_update()

    leave_request = db.session.execute(
        statement
    ).scalar_one_or_none()

    if leave_request is None:
        raise NotFoundError(
            f"Leave request {leave_request_id} not found"
        )

    return leave_request


def _validate_staff_can_submit_excuse(
    staff: Staff,
) -> None:
    if staff.status in (
        StaffStatus.SUSPENDED,
        StaffStatus.TERMINATED,
    ):
        raise ConflictError(
            f"Staff {staff.id} cannot submit an excuse "
            f"while in '{staff.status.value}' status"
        )


def _validate_excuse_type(
    excuse_type: ExcuseType,
) -> None:
    if not isinstance(
        excuse_type,
        ExcuseType,
    ):
        raise ValidationError(
            "Invalid excuse type"
        )


def _validate_description(
    description: str,
) -> str:
    if not isinstance(
        description,
        str,
    ):
        raise ValidationError(
            "Excuse description is required"
        )

    description = description.strip()

    if not description:
        raise ValidationError(
            "Excuse description is required"
        )

    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValidationError(
            "Excuse description cannot exceed "
            f"{MAX_DESCRIPTION_LENGTH} characters"
        )

    return description


def _validate_document_url(
    document_url: Optional[str],
) -> Optional[str]:
    if document_url is None:
        return None

    if not isinstance(
        document_url,
        str,
    ):
        raise ValidationError(
            "Document URL must be a string"
        )

    document_url = document_url.strip()

    if not document_url:
        return None

    if len(document_url) > MAX_DOCUMENT_URL_LENGTH:
        raise ValidationError(
            "Document URL cannot exceed "
            f"{MAX_DOCUMENT_URL_LENGTH} characters"
        )

    return document_url


def _validate_rejection_reason(
    reason: Optional[str],
) -> Optional[str]:
    if reason is None:
        return None

    if not isinstance(
        reason,
        str,
    ):
        raise ValidationError(
            "Rejection reason must be a string"
        )

    reason = reason.strip()

    if not reason:
        raise ValidationError(
            "Rejection reason cannot be empty"
        )

    if len(reason) > MAX_REJECTION_REASON_LENGTH:
        raise ValidationError(
            "Rejection reason cannot exceed "
            f"{MAX_REJECTION_REASON_LENGTH} characters"
        )

    return reason


def _validate_reviewer(
    reviewer_user_id: int,
    clinic_id: int,
) -> User:
    reviewer_user_id = _validate_positive_id(
        reviewer_user_id,
        "reviewer_user_id",
    )

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    reviewer = _get_user(reviewer_user_id)
    if reviewer.role is Role.SUPER_ADMIN:
        require_platform_review_context(reviewer, clinic_id)
    else:
        reviewer = _get_user(reviewer_user_id, clinic_id=clinic_id)

    if not reviewer.is_active:
        raise ValidationError(
            "Inactive users cannot review excuses"
        )

    role = getattr(
        reviewer.role,
        "value",
        reviewer.role,
    )

    if role not in (Role.ADMIN.value, Role.SUPER_ADMIN.value):
        raise ValidationError(
            "Only administrators can review excuses"
        )

    return reviewer


def _validate_leave_request_for_staff(
    leave_request: LeaveRequest,
    staff: Staff,
) -> None:
    if leave_request.staff_id != staff.id:
        raise ConflictError(
            "Leave request does not belong to this staff member"
        )


@transactional
def create_excuse(
    *,
    clinic_id: int,
    staff_id: int,
    excuse_type: ExcuseType,
    description: str,
    leave_request_id: Optional[int] = None,
    document_url: Optional[str] = None,
) -> Excuse:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    _ensure_active_clinic(
        clinic_id
    )

    staff = _get_staff(
        staff_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _validate_staff_can_submit_excuse(
        staff
    )

    _validate_excuse_type(
        excuse_type
    )

    description = _validate_description(
        description
    )

    document_url = _validate_document_url(
        document_url
    )

    leave_request = None

    if leave_request_id is not None:
        leave_request_id = _validate_positive_id(
            leave_request_id,
            "leave_request_id",
        )

        leave_request = _get_leave_request(
            leave_request_id,
            clinic_id=clinic_id,
            lock=True,
        )

        _validate_leave_request_for_staff(
            leave_request,
            staff,
        )

    excuse = Excuse(
        staff_id=staff.id,
        leave_request_id=(
            leave_request.id
            if leave_request is not None
            else None
        ),
        excuse_type=excuse_type,
        status=ExcuseStatus.PENDING,
        description=description,
        document_url=document_url,
        rejection_reason=None,
    )

    db.session.add(excuse)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Excuse",
        entity_id=excuse.id,
        description=(
            f"Excuse created for staff {staff.id}"
        ),
        new_value={
            "staff_id": staff.id,
            "leave_request_id": excuse.leave_request_id,
            "excuse_type": excuse.excuse_type.value,
            "status": excuse.status.value,
        },
        user_id=(
            staff.user_id
            if staff.user_id is not None
            else None
        ),
    )

    return excuse


def get_excuse(
    excuse_id: int,
    clinic_id: int,
) -> Excuse:
    return _get_excuse(
        excuse_id,
        clinic_id=clinic_id,
    )


def list_excuses(
    *,
    clinic_id: int,
    staff_id: Optional[int] = None,
    leave_request_id: Optional[int] = None,
    excuse_type: Optional[ExcuseType] = None,
    status: Optional[ExcuseStatus] = None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> dict:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = (
        select(Excuse)
        .join(
            Staff,
            Excuse.staff_id == Staff.id,
        )
        .where(
            Staff.clinic_id == clinic_id,
        )
    )

    count_statement = (
        select(
            func.count(
                Excuse.id
            )
        )
        .join(
            Staff,
            Excuse.staff_id == Staff.id,
        )
        .where(
            Staff.clinic_id == clinic_id,
        )
    )

    if staff_id is not None:
        staff_id = _validate_positive_id(
            staff_id,
            "staff_id",
        )

        _get_staff(
            staff_id,
            clinic_id=clinic_id,
        )

        statement = statement.where(
            Excuse.staff_id == staff_id,
        )

        count_statement = count_statement.where(
            Excuse.staff_id == staff_id,
        )

    if leave_request_id is not None:
        leave_request_id = _validate_positive_id(
            leave_request_id,
            "leave_request_id",
        )

        statement = statement.where(
            Excuse.leave_request_id == leave_request_id,
        )

        count_statement = count_statement.where(
            Excuse.leave_request_id == leave_request_id,
        )

    if excuse_type is not None:
        _validate_excuse_type(
            excuse_type
        )

        statement = statement.where(
            Excuse.excuse_type == excuse_type,
        )

        count_statement = count_statement.where(
            Excuse.excuse_type == excuse_type,
        )

    if status is not None:
        if not isinstance(
            status,
            ExcuseStatus,
        ):
            raise ValidationError(
                "Invalid excuse status"
            )

        statement = statement.where(
            Excuse.status == status,
        )

        count_statement = count_statement.where(
            Excuse.status == status,
        )

    statement = statement.order_by(
        Excuse.created_at.desc(),
        Excuse.id.desc(),
    )

    return _paginate(
        statement=statement,
        count_statement=count_statement,
        page=page,
        per_page=per_page,
    )


def list_staff_excuses(
    *,
    clinic_id: int,
    staff_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> dict:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    staff_id = _validate_positive_id(
        staff_id,
        "staff_id",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    _get_staff(
        staff_id,
        clinic_id=clinic_id,
    )

    statement = (
        select(Excuse)
        .where(
            Excuse.staff_id == staff_id,
        )
        .order_by(
            Excuse.created_at.desc(),
            Excuse.id.desc(),
        )
    )

    count_statement = select(
        func.count(
            Excuse.id
        )
    ).where(
        Excuse.staff_id == staff_id,
    )

    return _paginate(
        statement=statement,
        count_statement=count_statement,
        page=page,
        per_page=per_page,
    )


def get_my_excuses(
    *,
    clinic_id: int,
    staff_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> dict:
    return list_staff_excuses(
        clinic_id=clinic_id,
        staff_id=staff_id,
        page=page,
        per_page=per_page,
    )


@transactional
def approve_excuse(
    *,
    excuse_id: int,
    clinic_id: int,
    reviewer_user_id: int,
) -> Excuse:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    _ensure_active_clinic(
        clinic_id
    )

    excuse_id = _validate_positive_id(
        excuse_id,
        "excuse_id",
    )

    reviewer_user_id = _validate_positive_id(
        reviewer_user_id,
        "reviewer_user_id",
    )

    excuse = _get_excuse(
        excuse_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if excuse.status != ExcuseStatus.PENDING:
        raise ConflictError(
            f"Excuse {excuse.id} is already "
            f"'{excuse.status.value}' and cannot be approved"
        )

    reviewer = _validate_reviewer(
        reviewer_user_id,
        clinic_id,
    )

    now = _db_now()
    old_status = excuse.status

    excuse.status = ExcuseStatus.APPROVED
    excuse.rejection_reason = None
    excuse.reviewed_by_user_id = reviewer.id
    excuse.reviewed_at = now

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Excuse",
        entity_id=excuse.id,
        description=(
            f"Excuse {excuse.id} approved by "
            f"administrator {reviewer.id}"
        ),
        old_value={
            "status": old_status.value,
        },
        new_value={
            **({"clinic_id": clinic_id} if reviewer.role is Role.SUPER_ADMIN else {}),
            "status": excuse.status.value,
            "reviewed_by_user_id": reviewer.id,
            "reviewed_at": now.isoformat(),
            "rejection_reason": None,
        },
        user_id=reviewer.id,
        **({"clinic_id": clinic_id if reviewer.clinic_id in (None, clinic_id) else None}
           if reviewer.role is Role.SUPER_ADMIN else {}),
    )

    return excuse


@transactional
def reject_excuse(
    *,
    excuse_id: int,
    clinic_id: int,
    reviewer_user_id: int,
    reason: Optional[str] = None,
) -> Excuse:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    _ensure_active_clinic(
        clinic_id
    )

    excuse_id = _validate_positive_id(
        excuse_id,
        "excuse_id",
    )

    reviewer_user_id = _validate_positive_id(
        reviewer_user_id,
        "reviewer_user_id",
    )

    excuse = _get_excuse(
        excuse_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if excuse.status != ExcuseStatus.PENDING:
        raise ConflictError(
            f"Excuse {excuse.id} is already "
            f"'{excuse.status.value}' and cannot be rejected"
        )

    reviewer = _validate_reviewer(
        reviewer_user_id,
        clinic_id,
    )

    reason = _validate_rejection_reason(
        reason
    )

    now = _db_now()
    old_status = excuse.status

    excuse.status = ExcuseStatus.REJECTED
    excuse.rejection_reason = reason
    excuse.reviewed_by_user_id = reviewer.id
    excuse.reviewed_at = now

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Excuse",
        entity_id=excuse.id,
        description=(
            f"Excuse {excuse.id} rejected by "
            f"administrator {reviewer.id}"
        ),
        old_value={
            "status": old_status.value,
            "rejection_reason": None,
        },
        new_value={
            **({"clinic_id": clinic_id} if reviewer.role is Role.SUPER_ADMIN else {}),
            "status": excuse.status.value,
            "reviewed_by_user_id": reviewer.id,
            "reviewed_at": now.isoformat(),
            "rejection_reason": reason,
        },
        user_id=reviewer.id,
        **({"clinic_id": clinic_id if reviewer.clinic_id in (None, clinic_id) else None}
           if reviewer.role is Role.SUPER_ADMIN else {}),
    )

    return excuse