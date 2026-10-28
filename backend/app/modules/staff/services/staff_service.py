from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from app.extensions import db

from app.core.auth.user.models.user_model import User
from app.core.auth.user.services.token_service import revoke_user_tokens
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import (
    LeaveStatus,
    LeaveType,
    StaffStatus,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional

from app.modules.staff.models.staff_model import (
    LeaveRequest,
    PayrollRecord,
    Staff,
)


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500

_EDITABLE_STAFF_FIELDS = {
    "first_name",
    "last_name",
    "specialty",
    "phone",
    "email",
    "hired_at",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_positive_id(
    value,
    field_name: str,
) -> int:
    if isinstance(value, bool):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"{field_name} must be a positive integer"
        ) from exc

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


def _serialize_enum(value):
    return (
        value.value
        if hasattr(value, "value")
        else value
    )


def _normalize_decimal(
    value,
    field_name: str,
) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError(
            f"{field_name} must be a valid decimal"
        )

    if isinstance(value, Decimal):
        result = value
    else:
        try:
            result = Decimal(str(value))
        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ) as exc:
            raise ValidationError(
                f"{field_name} must be a valid decimal"
            ) from exc

    if not result.is_finite():
        raise ValidationError(
            f"{field_name} must be finite"
        )

    return result


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


def get_staff(
    staff_id: int,
    clinic_id: Optional[int] = None,
) -> Staff:
    return _get_staff(
        staff_id=staff_id,
        clinic_id=clinic_id,
    )


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

    status = clinic.status

    if getattr(
        status,
        "value",
        status,
    ) != "active":
        raise ConflictError(
            f"Clinic {clinic_id} is not active"
        )


def _paginate_statement(
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


def list_staff(
    clinic_id: int,
    status: StaffStatus | None = None,
    search: str | None = None,
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
        select(Staff)
        .options(
            selectinload(
                Staff.department_memberships,
            )
        )
        .where(
            Staff.clinic_id == clinic_id,
        )
    )

    count_statement = select(
        func.count(Staff.id)
    ).where(
        Staff.clinic_id == clinic_id,
    )

    if status is not None:
        if not isinstance(
            status,
            StaffStatus,
        ):
            raise ValidationError(
                "Invalid staff status"
            )

        statement = statement.where(
            Staff.status == status,
        )

        count_statement = count_statement.where(
            Staff.status == status,
        )

    if search is not None:
        if not isinstance(search, str):
            raise ValidationError(
                "search must be a string"
            )

        search_value = search.strip()

        if search_value:
            like = f"%{search_value}%"

            search_filter = or_(
                Staff.first_name.ilike(like),
                Staff.last_name.ilike(like),
            )

            statement = statement.where(
                search_filter
            )

            count_statement = count_statement.where(
                search_filter
            )

    statement = statement.order_by(
        Staff.last_name.asc(),
        Staff.first_name.asc(),
        Staff.id.asc(),
    )

    return _paginate_statement(
        statement=statement,
        count_statement=count_statement,
        page=page,
        per_page=per_page,
    )


@transactional
def create_staff(
    clinic_id: int,
    first_name: str,
    last_name: str,
    user_id: Optional[int] = None,
    specialty=None,
    phone=None,
    email=None,
    hired_at=None,
) -> Staff:
    _ensure_active_clinic(
        clinic_id
    )

    if (
        not isinstance(first_name, str)
        or not first_name.strip()
    ):
        raise ValidationError(
            "First name is required"
        )

    if (
        not isinstance(last_name, str)
        or not last_name.strip()
    ):
        raise ValidationError(
            "Last name is required"
        )

    if user_id is not None:
        user_id = _validate_positive_id(
            user_id,
            "user_id",
        )

        user = _get_user(
            user_id=user_id,
            clinic_id=clinic_id,
        )

        if not getattr(
            user,
            "is_active",
            True,
        ):
            raise ValidationError(
                "User account is inactive"
            )

        existing = db.session.execute(
            select(Staff.id).where(
                Staff.user_id == user_id,
            )
        ).scalar_one_or_none()

        if existing is not None:
            raise ConflictError(
                f"User {user_id} is already linked to staff {existing}"
            )

    staff = Staff(
        clinic_id=clinic_id,
        user_id=user_id,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        specialty=specialty,
        phone=phone,
        email=email,
        hired_at=hired_at,
        status=StaffStatus.ACTIVE,
    )

    db.session.add(staff)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Staff",
        entity_id=staff.id,
        description=(
            f"Staff '{staff.first_name} "
            f"{staff.last_name}' created"
        ),
        user_id=None,
        new_value={
            "clinic_id": clinic_id,
            "user_id": user_id,
            "first_name": staff.first_name,
            "last_name": staff.last_name,
        },
    )

    return staff


@transactional
def update_staff(
    staff_id: int,
    clinic_id: int,
    **fields,
) -> Staff:
    _ensure_active_clinic(
        clinic_id
    )

    staff = _get_staff(
        staff_id=staff_id,
        clinic_id=clinic_id,
        lock=True,
    )

    unknown = (
        set(fields)
        - _EDITABLE_STAFF_FIELDS
    )

    if unknown:
        raise ValidationError(
            "Unknown staff field(s): "
            + ", ".join(sorted(unknown))
        )

    old_value = {}
    new_value = {}

    for key, new_value_raw in fields.items():
        current_value = getattr(
            staff,
            key,
        )

        if key in {
            "first_name",
            "last_name",
        }:
            if (
                new_value_raw is None
                or not str(
                    new_value_raw
                ).strip()
            ):
                raise ValidationError(
                    f"{key.replace('_', ' ').title()} is required"
                )

            new_value_raw = str(
                new_value_raw
            ).strip()

        if (
            current_value
            == new_value_raw
        ):
            continue

        old_value[key] = _serialize_enum(
            current_value
        )

        new_value[key] = _serialize_enum(
            new_value_raw
        )

        setattr(
            staff,
            key,
            new_value_raw,
        )

    if new_value:
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="Staff",
            entity_id=staff.id,
            description=(
                f"Staff '{staff.first_name} "
                f"{staff.last_name}' updated"
            ),
            old_value=old_value,
            new_value=new_value,
        )

    return staff


@transactional
def change_staff_status(
    staff_id: int,
    clinic_id: int,
    new_status: StaffStatus,
) -> Staff:
    _ensure_active_clinic(
        clinic_id
    )

    if not isinstance(
        new_status,
        StaffStatus,
    ):
        raise ValidationError(
            "Invalid staff status"
        )

    staff = _get_staff(
        staff_id=staff_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if staff.status == new_status:
        return staff

    old_status = _serialize_enum(
        staff.status
    )

    staff.status = new_status

    if (
        staff.user_id is not None
        and new_status in (
            StaffStatus.SUSPENDED,
            StaffStatus.TERMINATED,
        )
    ):
        revoke_user_tokens(staff.user_id)

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Staff",
        entity_id=staff.id,
        description=(
            f"Staff status changed to "
            f"'{new_status.value}'"
        ),
        old_value={
            "status": old_status,
        },
        new_value={
            "status": new_status.value,
        },
    )

    return staff


def _get_leave_request(
    leave_id: int,
    clinic_id: Optional[int] = None,
    lock: bool = False,
) -> LeaveRequest:
    leave_id = _validate_positive_id(
        leave_id,
        "leave_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

    statement = (
        select(LeaveRequest)
        .join(
            Staff,
            Staff.id == LeaveRequest.staff_id,
        )
        .where(
            LeaveRequest.id == leave_id,
        )
    )

    if clinic_id is not None:
        statement = statement.where(
            Staff.clinic_id == clinic_id,
        )

    if lock:
        statement = statement.with_for_update()

    leave = db.session.execute(
        statement
    ).scalar_one_or_none()

    if leave is None:
        raise NotFoundError(
            f"Leave request {leave_id} not found"
        )

    return leave


def get_leave_request(
    leave_id: int,
    clinic_id: Optional[int] = None,
) -> LeaveRequest:
    return _get_leave_request(
        leave_id=leave_id,
        clinic_id=clinic_id,
    )


def list_leave_requests(
    clinic_id: int,
    staff_id: Optional[int] = None,
    status: LeaveStatus | None = None,
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
        select(LeaveRequest)
        .join(
            Staff,
            Staff.id == LeaveRequest.staff_id,
        )
        .where(
            Staff.clinic_id == clinic_id,
        )
    )

    count_statement = (
        select(
            func.count(
                LeaveRequest.id
            )
        )
        .join(
            Staff,
            Staff.id == LeaveRequest.staff_id,
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
            staff_id=staff_id,
            clinic_id=clinic_id,
        )

        statement = statement.where(
            LeaveRequest.staff_id == staff_id,
        )

        count_statement = count_statement.where(
            LeaveRequest.staff_id == staff_id,
        )

    if status is not None:
        if not isinstance(
            status,
            LeaveStatus,
        ):
            raise ValidationError(
                "Invalid leave status"
            )

        statement = statement.where(
            LeaveRequest.status == status,
        )

        count_statement = count_statement.where(
            LeaveRequest.status == status,
        )

    statement = statement.order_by(
        LeaveRequest.start_date.desc(),
        LeaveRequest.created_at.desc(),
        LeaveRequest.id.desc(),
    )

    return _paginate_statement(
        statement=statement,
        count_statement=count_statement,
        page=page,
        per_page=per_page,
    )


def _has_overlapping_approved_leave(
    staff_id: int,
    start_date: date,
    end_date: date,
    exclude_leave_id: Optional[int] = None,
) -> bool:
    staff_id = _validate_positive_id(
        staff_id,
        "staff_id",
    )

    statement = select(
        LeaveRequest.id
    ).where(
        LeaveRequest.staff_id == staff_id,
        LeaveRequest.status
        == LeaveStatus.APPROVED,
        LeaveRequest.start_date <= end_date,
        LeaveRequest.end_date >= start_date,
    )

    if exclude_leave_id is not None:
        exclude_leave_id = _validate_positive_id(
            exclude_leave_id,
            "exclude_leave_id",
        )

        statement = statement.where(
            LeaveRequest.id != exclude_leave_id,
        )

    return (
        db.session.execute(
            statement.limit(1)
        ).scalar_one_or_none()
        is not None
    )


@transactional
def request_leave(
    clinic_id: int,
    staff_id: int,
    actor_user_id: int,
    leave_type: LeaveType,
    start_date: date,
    end_date: date,
    reason: Optional[str] = None,
) -> LeaveRequest:
    _ensure_active_clinic(
        clinic_id
    )

    if not isinstance(
        leave_type,
        LeaveType,
    ):
        raise ValidationError(
            "Invalid leave type"
        )

    if not isinstance(
        start_date,
        date,
    ) or not isinstance(
        end_date,
        date,
    ):
        raise ValidationError(
            "Leave dates must be valid dates"
        )

    staff = _get_staff(
        staff_id=staff_id,
        clinic_id=clinic_id,
    )

    user = _get_user(
        user_id=actor_user_id,
        clinic_id=clinic_id,
    )

    if staff.user_id != user.id:
        raise ValidationError(
            "Staff member does not belong to the authenticated user"
        )

    if end_date < start_date:
        raise ValidationError(
            "Leave end date cannot be before start date"
        )

    if staff.status in (
        StaffStatus.SUSPENDED,
        StaffStatus.TERMINATED,
    ):
        raise ConflictError(
            "Inactive staff members cannot request leave"
        )

    pending_statement = select(
        LeaveRequest.id
    ).where(
        LeaveRequest.staff_id == staff.id,
        LeaveRequest.status
        == LeaveStatus.PENDING,
        LeaveRequest.start_date <= end_date,
        LeaveRequest.end_date >= start_date,
    )

    overlapping_pending = db.session.execute(
        pending_statement.limit(1)
    ).scalar_one_or_none()

    if overlapping_pending is not None:
        raise ConflictError(
            "Staff member already has a pending "
            "leave request overlapping this period"
        )

    if _has_overlapping_approved_leave(
        staff_id=staff.id,
        start_date=start_date,
        end_date=end_date,
    ):
        raise ConflictError(
            "Staff member already has approved leave "
            "overlapping this period"
        )

    leave = LeaveRequest(
        staff_id=staff.id,
        leave_type=leave_type,
        status=LeaveStatus.PENDING,
        start_date=start_date,
        end_date=end_date,
        reason=(
            reason.strip()
            if isinstance(reason, str)
            else reason
        ),
    )

    db.session.add(leave)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="LeaveRequest",
        entity_id=leave.id,
        description=(
            f"Leave requested for staff {staff.id}"
        ),
        user_id=actor_user_id,
        new_value={
            "clinic_id": clinic_id,
            "staff_id": staff.id,
            "leave_type": leave_type.value,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )

    return leave


@transactional
def approve_leave_request(
    leave_id: int,
    clinic_id: int,
    reviewer_user_id: int,
) -> LeaveRequest:
    _ensure_active_clinic(
        clinic_id
    )

    leave = _get_leave_request(
        leave_id=leave_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if leave.status != LeaveStatus.PENDING:
        raise ConflictError(
            f"Leave request {leave_id} is already "
            f"'{leave.status.value}'"
        )

    reviewer = _get_user(
        user_id=reviewer_user_id,
        clinic_id=clinic_id,
    )

    if reviewer.role != Role.ADMIN:
        raise ValidationError(
            "Only an administrator can approve leave requests"
        )

    if not getattr(
        reviewer,
        "is_active",
        True,
    ):
        raise ValidationError(
            "Reviewer account is inactive"
        )

    if _has_overlapping_approved_leave(
        staff_id=leave.staff_id,
        start_date=leave.start_date,
        end_date=leave.end_date,
        exclude_leave_id=leave.id,
    ):
        raise ConflictError(
            f"Staff {leave.staff_id} already has "
            "approved leave overlapping this period"
        )

    old_status = leave.status.value

    leave.status = LeaveStatus.APPROVED
    leave.reviewed_by_user_id = reviewer.id
    leave.reviewed_at = _utcnow()

    today = date.today()

    if (
        leave.start_date
        <= today
        <= leave.end_date
    ):
        staff = _get_staff(
            staff_id=leave.staff_id,
            clinic_id=clinic_id,
            lock=True,
        )

        staff.status = StaffStatus.ON_LEAVE

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="LeaveRequest",
        entity_id=leave.id,
        description=(
            f"Leave request approved for "
            f"staff {leave.staff_id}"
        ),
        user_id=reviewer.id,
        old_value={
            "status": old_status,
        },
        new_value={
            "status": leave.status.value,
            "reviewed_by_user_id": reviewer.id,
        },
    )

    return leave


@transactional
def reject_leave_request(
    leave_id: int,
    clinic_id: int,
    reviewer_user_id: int,
    reason: Optional[str] = None,
) -> LeaveRequest:
    _ensure_active_clinic(
        clinic_id
    )

    leave = _get_leave_request(
        leave_id=leave_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if leave.status != LeaveStatus.PENDING:
        raise ConflictError(
            f"Leave request {leave_id} is already "
            f"'{leave.status.value}'"
        )

    reviewer = _get_user(
        user_id=reviewer_user_id,
        clinic_id=clinic_id,
    )

    if reviewer.role != Role.ADMIN:
        raise ValidationError(
            "Only an administrator can reject leave requests"
        )

    if not getattr(
        reviewer,
        "is_active",
        True,
    ):
        raise ValidationError(
            "Reviewer account is inactive"
        )

    normalized_reason = (
        reason.strip()
        if isinstance(reason, str)
        else reason
    )

    if (
        normalized_reason is not None
        and not normalized_reason
    ):
        raise ValidationError(
            "Rejection reason cannot be empty"
        )

    old_status = leave.status.value

    leave.status = LeaveStatus.REJECTED
    leave.reviewed_by_user_id = reviewer.id
    leave.reviewed_at = _utcnow()

    if normalized_reason:
        if leave.reason:
            leave.reason = (
                f"{leave.reason}\n"
                f"Rejection note: "
                f"{normalized_reason}"
            ).strip()
        else:
            leave.reason = (
                f"Rejection note: "
                f"{normalized_reason}"
            )

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="LeaveRequest",
        entity_id=leave.id,
        description=(
            f"Leave request rejected for "
            f"staff {leave.staff_id}"
        ),
        user_id=reviewer.id,
        old_value={
            "status": old_status,
        },
        new_value={
            "status": leave.status.value,
            "reviewed_by_user_id": reviewer.id,
            "reason": normalized_reason,
        },
    )

    return leave


def restore_staff_from_expired_leave() -> int:
    today = date.today()

    approved_leave_exists = (
        select(LeaveRequest.id)
        .where(
            LeaveRequest.staff_id == Staff.id,
            LeaveRequest.status
            == LeaveStatus.APPROVED,
            LeaveRequest.start_date <= today,
            LeaveRequest.end_date >= today,
        )
        .exists()
    )

    statement = select(Staff).where(
        Staff.status == StaffStatus.ON_LEAVE,
        ~approved_leave_exists,
    )

    staff_list = list(
        db.session.execute(
            statement
        ).scalars()
    )

    restored = 0

    for staff in staff_list:
        old_status = staff.status.value

        staff.status = StaffStatus.ACTIVE
        restored += 1

        create_audit_log(
            action=AuditAction.STATUS_CHANGE,
            entity_type="Staff",
            entity_id=staff.id,
            description=(
                "Staff restored to active status "
                "after approved leave ended"
            ),
            old_value={
                "status": old_status,
            },
            new_value={
                "status": StaffStatus.ACTIVE.value,
            },
        )

    db.session.commit()

    return restored


def get_payroll_record(
    record_id: int,
    clinic_id: Optional[int] = None,
) -> PayrollRecord:
    record_id = _validate_positive_id(
        record_id,
        "record_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

    statement = (
        select(PayrollRecord)
        .join(
            Staff,
            Staff.id == PayrollRecord.staff_id,
        )
        .where(
            PayrollRecord.id == record_id,
        )
    )

    if clinic_id is not None:
        statement = statement.where(
            Staff.clinic_id == clinic_id,
        )

    record = db.session.execute(
        statement
    ).scalar_one_or_none()

    if record is None:
        raise NotFoundError(
            f"Payroll record {record_id} not found"
        )

    return record


def list_payroll(
    clinic_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> dict:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = (
        select(PayrollRecord)
        .join(
            Staff,
            Staff.id == PayrollRecord.staff_id,
        )
        .where(
            Staff.clinic_id == clinic_id,
        )
        .order_by(
            PayrollRecord.pay_period_start.desc(),
            PayrollRecord.id.desc(),
        )
    )

    count_statement = (
        select(
            func.count(
                PayrollRecord.id
            )
        )
        .join(
            Staff,
            Staff.id == PayrollRecord.staff_id,
        )
        .where(
            Staff.clinic_id == clinic_id,
        )
    )

    return _paginate_statement(
        statement=statement,
        count_statement=count_statement,
        page=page,
        per_page=per_page,
    )


def list_payroll_for_staff(
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

    _get_staff(
        staff_id=staff_id,
        clinic_id=clinic_id,
    )

    statement = (
        select(PayrollRecord)
        .where(
            PayrollRecord.staff_id == staff_id,
        )
        .order_by(
            PayrollRecord.pay_period_start.desc(),
            PayrollRecord.id.desc(),
        )
    )

    count_statement = select(
        func.count(
            PayrollRecord.id
        )
    ).where(
        PayrollRecord.staff_id == staff_id,
    )

    return _paginate_statement(
        statement=statement,
        count_statement=count_statement,
        page=page,
        per_page=per_page,
    )


@transactional
def create_payroll_record(
    clinic_id: int,
    staff_id: int,
    pay_period_start: date,
    pay_period_end: date,
    base_salary: Decimal,
    bonuses: Decimal = Decimal("0"),
    deductions: Decimal = Decimal("0"),
) -> PayrollRecord:
    _ensure_active_clinic(
        clinic_id
    )

    staff = _get_staff(
        staff_id=staff_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if not isinstance(
        pay_period_start,
        date,
    ) or not isinstance(
        pay_period_end,
        date,
    ):
        raise ValidationError(
            "Pay period dates must be valid dates"
        )

    if pay_period_end < pay_period_start:
        raise ValidationError(
            "Pay period end cannot be before start"
        )

    base_salary = _normalize_decimal(
        base_salary,
        "base_salary",
    )

    bonuses = _normalize_decimal(
        bonuses,
        "bonuses",
    )

    deductions = _normalize_decimal(
        deductions,
        "deductions",
    )

    if base_salary < 0:
        raise ValidationError(
            "Base salary cannot be negative"
        )

    if bonuses < 0:
        raise ValidationError(
            "Bonuses cannot be negative"
        )

    if deductions < 0:
        raise ValidationError(
            "Deductions cannot be negative"
        )

    net_pay = (
        base_salary
        + bonuses
        - deductions
    )

    if net_pay < 0:
        raise ValidationError(
            "Net pay cannot be negative"
        )

    duplicate_statement = select(
        PayrollRecord.id
    ).where(
        PayrollRecord.staff_id == staff.id,
        PayrollRecord.pay_period_start
        == pay_period_start,
        PayrollRecord.pay_period_end
        == pay_period_end,
    )

    duplicate = db.session.execute(
        duplicate_statement.limit(1)
    ).scalar_one_or_none()

    if duplicate is not None:
        raise ConflictError(
            "Payroll record already exists for "
            f"staff {staff.id} for this period"
        )

    record = PayrollRecord(
        staff_id=staff.id,
        pay_period_start=pay_period_start,
        pay_period_end=pay_period_end,
        base_salary=base_salary,
        bonuses=bonuses,
        deductions=deductions,
        net_pay=net_pay,
    )

    db.session.add(record)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="PayrollRecord",
        entity_id=record.id,
        description=(
            f"Payroll record created for "
            f"staff {staff.id}"
        ),
        new_value={
            "clinic_id": clinic_id,
            "staff_id": staff.id,
            "pay_period_start": (
                pay_period_start.isoformat()
            ),
            "pay_period_end": (
                pay_period_end.isoformat()
            ),
            "base_salary": str(base_salary),
            "bonuses": str(bonuses),
            "deductions": str(deductions),
            "net_pay": str(net_pay),
        },
    )

    return record


@transactional
def generate_payroll_for_period(
    clinic_id: int,
    pay_period_start: date,
    pay_period_end: date,
    salary_lookup: dict[int, Decimal],
) -> list[PayrollRecord]:
    _ensure_active_clinic(
        clinic_id
    )

    if not isinstance(
        pay_period_start,
        date,
    ) or not isinstance(
        pay_period_end,
        date,
    ):
        raise ValidationError(
            "Pay period dates must be valid dates"
        )

    if pay_period_end < pay_period_start:
        raise ValidationError(
            "Pay period end cannot be before start"
        )

    if not isinstance(
        salary_lookup,
        dict,
    ):
        raise ValidationError(
            "salary_lookup must be a dictionary"
        )

    normalized_salary_lookup: dict[
        int,
        Decimal,
    ] = {}

    for staff_id, salary in salary_lookup.items():
        normalized_staff_id = _validate_positive_id(
            staff_id,
            "staff_id",
        )

        normalized_salary_lookup[
            normalized_staff_id
        ] = _normalize_decimal(
            salary,
            f"salary for staff {normalized_staff_id}",
        )

    staff_statement = select(Staff).where(
        Staff.clinic_id == clinic_id,
        Staff.status == StaffStatus.ACTIVE,
    ).order_by(
        Staff.id.asc()
    )

    staff_list = list(
        db.session.execute(
            staff_statement
        ).scalars()
    )

    if not staff_list:
        return []

    staff_ids = [
        staff.id
        for staff in staff_list
    ]

    existing_statement = select(
        PayrollRecord.staff_id
    ).where(
        PayrollRecord.staff_id.in_(staff_ids),
        PayrollRecord.pay_period_start
        == pay_period_start,
        PayrollRecord.pay_period_end
        == pay_period_end,
    ).with_for_update()

    existing_staff_ids = set(
        db.session.execute(
            existing_statement
        ).scalars()
    )

    created: list[PayrollRecord] = []

    for staff in staff_list:
        if staff.id in existing_staff_ids:
            continue

        base_salary = normalized_salary_lookup.get(
            staff.id
        )

        if base_salary is None:
            continue

        if base_salary < 0:
            raise ValidationError(
                f"Base salary cannot be negative for staff {staff.id}"
            )

        record = PayrollRecord(
            staff_id=staff.id,
            pay_period_start=pay_period_start,
            pay_period_end=pay_period_end,
            base_salary=base_salary,
            bonuses=Decimal("0"),
            deductions=Decimal("0"),
            net_pay=base_salary,
        )

        db.session.add(record)
        created.append(record)

    db.session.flush()

    for record in created:
        create_audit_log(
            action=AuditAction.CREATE,
            entity_type="PayrollRecord",
            entity_id=record.id,
            description=(
                f"Payroll record generated for "
                f"staff {record.staff_id}"
            ),
            new_value={
                "clinic_id": clinic_id,
                "staff_id": record.staff_id,
                "pay_period_start": (
                    pay_period_start.isoformat()
                ),
                "pay_period_end": (
                    pay_period_end.isoformat()
                ),
                "net_pay": str(record.net_pay),
            },
        )

    return created


@transactional
def mark_payroll_paid(
    record_id: int,
    clinic_id: int,
) -> PayrollRecord:
    _ensure_active_clinic(
        clinic_id
    )

    record = get_payroll_record(
        record_id=record_id,
        clinic_id=clinic_id,
    )

    if record.paid_at is not None:
        raise ConflictError(
            f"Payroll record {record_id} "
            "is already marked paid"
        )

    record.paid_at = _utcnow()

    create_audit_log(
        action=AuditAction.PAYMENT,
        entity_type="PayrollRecord",
        entity_id=record.id,
        description=(
            f"Payroll marked paid for "
            f"staff {record.staff_id}"
        ),
        new_value={
            "net_pay": str(record.net_pay),
            "paid_at": record.paid_at.isoformat(),
        },
    )

    return record
