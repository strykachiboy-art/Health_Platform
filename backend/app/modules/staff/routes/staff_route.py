from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError as PydanticValidationError

from app.extensions import db

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import (
    get_administrative_clinic_id,
    role_required,
)

from app.modules.staff.schemas.staff_schema import (
    LeaveListQuerySchema,
    LeaveRejectSchema,
    LeaveRequestCreateSchema,
    LeaveReviewSchema,
    PayrollCreateSchema,
    PayrollGenerateSchema,
    PayrollListQuerySchema,
    StaffCreateSchema,
    StaffListQuerySchema,
    StaffStatusUpdateSchema,
    StaffUpdateSchema,
)

from app.modules.staff.schemas.staff_department_schema import (
    StaffDepartmentUpdateSchema,
)

from app.modules.staff.schemas.excuse_schema import (
    ExcuseCreateSchema,
    ExcuseListQuerySchema,
    ExcuseListResponseSchema,
    ExcuseRejectSchema,
)

from app.modules.staff.services.staff_service import (
    approve_leave_request,
    change_staff_status,
    create_payroll_record,
    create_staff,
    generate_payroll_for_period,
    get_leave_request,
    get_payroll_record,
    get_staff,
    list_leave_requests,
    list_payroll,
    list_payroll_for_staff,
    list_staff,
    mark_payroll_paid,
    reject_leave_request,
    request_leave,
    update_staff,
)

from app.modules.staff.services.staff_department_service import (
    assign_staff_to_department,
    get_staff_departments,
    remove_staff_from_department,
    set_primary_department,
)

from app.modules.staff.services.excuse_service import (
    approve_excuse,
    create_excuse,
    get_excuse,
    get_my_excuses,
    list_excuses,
    reject_excuse,
)


staff_bp = Blueprint(
    "staff",
    __name__,
    url_prefix="/staff",
)


MANAGEMENT_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
)

STAFF_VIEW_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.RECEPTIONIST,
    Role.PARAMEDIC,
    Role.EMT,
    Role.DRIVER,
    Role.AMBULANCE_DISPATCHER,
    Role.AMBULANCE_COORDINATOR,
    Role.ACCOUNTANT,
    Role.OTHER,
)

LEAVE_MANAGEMENT_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
)

PAYROLL_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
    Role.ACCOUNTANT,
)

EXCUSE_REVIEW_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
)


def _current_user() -> User:
    """Return the authenticated active user."""
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
        raise ValidationError(
            "Authenticated user could not be resolved"
        )

    if not user.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    return user


def _current_clinic_id(
    user: User | None = None,
) -> int:
    return get_administrative_clinic_id(
        required=True
    )


def _current_staff_id(
    user: User | None = None,
) -> int:
    user = user or _current_user()

    staff = getattr(
        user,
        "staff",
        None,
    )

    if staff is None:
        raise ValidationError(
            "Authenticated user is not linked to a staff profile"
        )

    staff_id = getattr(
        staff,
        "id",
        None,
    )

    if isinstance(staff_id, bool):
        raise ValidationError(
            "Authenticated staff profile is invalid"
        )

    try:
        staff_id = int(staff_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Authenticated staff profile is invalid"
        ) from exc

    if staff_id <= 0:
        raise ValidationError(
            "Authenticated staff profile is invalid"
        )

    return staff_id


def _is_review_manager(user: User) -> bool:
    # The route's verified administrative context still constrains every row.
    role = getattr(user.role, "value", user.role)
    return role in (Role.ADMIN.value, Role.SUPER_ADMIN.value)


def _validate_json(schema):
    payload = request.get_json(
        silent=True
    )

    if payload is None:
        return None, (
            jsonify(
                {
                    "error": "Invalid or missing JSON body",
                }
            ),
            400,
        )

    if not isinstance(
        payload,
        dict,
    ):
        return None, (
            jsonify(
                {
                    "error": "Request body must be a JSON object",
                }
            ),
            422,
        )

    try:
        return (
            schema.model_validate(payload),
            None,
        )

    except PydanticValidationError as exc:
        return None, (
            jsonify(
                {
                    "error": "Validation failed",
                    "details": _normalize_validation_errors(
                        exc
                    ),
                }
            ),
            422,
        )


def _validate_query(schema):
    try:
        payload = schema.model_validate(
            request.args.to_dict()
        )

        return payload, None

    except PydanticValidationError as exc:
        return None, (
            jsonify(
                {
                    "error": "Validation failed",
                    "details": _normalize_validation_errors(
                        exc
                    ),
                }
            ),
            422,
        )


def _normalize_validation_errors(
    exc: PydanticValidationError,
) -> list[dict]:
    details = []

    for error in exc.errors():
        item = dict(error)
        ctx = item.get("ctx")

        if isinstance(
            ctx,
            dict,
        ):
            ctx = dict(ctx)

            if "error" in ctx:
                ctx["error"] = str(
                    ctx["error"]
                )

            item["ctx"] = ctx

        details.append(item)

    return details


def _domain_error_response(
    exc: DomainError,
):
    return (
        jsonify(
            {
                "error": str(exc),
            }
        ),
        exc.status_code,
    )


def _serialize_staff(
    staff,
) -> dict:
    primary_department_id = None

    for membership in getattr(
        staff,
        "department_memberships",
        (),
    ) or ():
        status = getattr(
            membership.status,
            "value",
            membership.status,
        )

        if (
            status == "active"
            and membership.is_primary
        ):
            primary_department_id = (
                membership.department_id
            )
            break

    return {
        "id": staff.id,
        "clinic_id": staff.clinic_id,
        "user_id": staff.user_id,
        "department_id": primary_department_id,
        "first_name": staff.first_name,
        "last_name": staff.last_name,
        "specialty": staff.specialty,
        "phone": staff.phone,
        "email": staff.email,
        "status": (
            staff.status.value
            if hasattr(
                staff.status,
                "value",
            )
            else staff.status
        ),
        "hired_at": (
            staff.hired_at.isoformat()
            if staff.hired_at
            else None
        ),
        "created_at": (
            staff.created_at.isoformat()
            if staff.created_at
            else None
        ),
        "updated_at": (
            staff.updated_at.isoformat()
            if staff.updated_at
            else None
        ),
    }


def _serialize_leave(
    leave,
    *,
    clinic_id: int | None = None,
) -> dict:
    return {
        **({"clinic_id": clinic_id} if clinic_id is not None else {}),
        "id": leave.id,
        "staff_id": leave.staff_id,
        "leave_type": (
            leave.leave_type.value
            if hasattr(
                leave.leave_type,
                "value",
            )
            else leave.leave_type
        ),
        "status": (
            leave.status.value
            if hasattr(
                leave.status,
                "value",
            )
            else leave.status
        ),
        "start_date": (
            leave.start_date.isoformat()
            if leave.start_date
            else None
        ),
        "end_date": (
            leave.end_date.isoformat()
            if leave.end_date
            else None
        ),
        "reason": leave.reason,
        "reviewed_by_user_id": (
            leave.reviewed_by_user_id
        ),
        "reviewed_at": (
            leave.reviewed_at.isoformat()
            if leave.reviewed_at
            else None
        ),
        "created_at": (
            leave.created_at.isoformat()
            if leave.created_at
            else None
        ),
        "updated_at": (
            leave.updated_at.isoformat()
            if leave.updated_at
            else None
        ),
    }


def _serialize_payroll(
    record,
    *,
    clinic_id: int | None = None,
) -> dict:
    paid_at = getattr(
        record,
        "paid_at",
        None,
    )

    return {
        **({"clinic_id": clinic_id} if clinic_id is not None else {}),
        "id": record.id,
        "staff_id": record.staff_id,
        "pay_period_start": (
            record.pay_period_start.isoformat()
            if record.pay_period_start
            else None
        ),
        "pay_period_end": (
            record.pay_period_end.isoformat()
            if record.pay_period_end
            else None
        ),
        "base_salary": str(
            record.base_salary
        ),
        "bonuses": str(
            record.bonuses
        ),
        "deductions": str(
            record.deductions
        ),
        "net_pay": str(
            record.net_pay
        ),
        "paid_at": (
            paid_at.isoformat()
            if paid_at
            else None
        ),
        "created_at": (
            record.created_at.isoformat()
            if record.created_at
            else None
        ),
        "updated_at": (
            record.updated_at.isoformat()
            if record.updated_at
            else None
        ),
    }


def _serialize_excuse(
    excuse,
) -> dict:
    return {
        "id": excuse.id,
        "staff_id": excuse.staff_id,
        "leave_request_id": (
            excuse.leave_request_id
        ),
        "excuse_type": (
            excuse.excuse_type.value
            if hasattr(
                excuse.excuse_type,
                "value",
            )
            else excuse.excuse_type
        ),
        "status": (
            excuse.status.value
            if hasattr(
                excuse.status,
                "value",
            )
            else excuse.status
        ),
        "description": excuse.description,
        "document_url": excuse.document_url,
        "rejection_reason": getattr(
            excuse,
            "rejection_reason",
            None,
        ),
        "reviewed_by_user_id": (
            excuse.reviewed_by_user_id
        ),
        "reviewed_at": (
            excuse.reviewed_at.isoformat()
            if excuse.reviewed_at
            else None
        ),
        "created_at": (
            excuse.created_at.isoformat()
            if excuse.created_at
            else None
        ),
        "updated_at": (
            excuse.updated_at.isoformat()
            if excuse.updated_at
            else None
        ),
    }


def _serialize_paginated(
    result: dict,
    serializer,
) -> dict:
    return {
        "items": [
            serializer(item)
            for item in result["items"]
        ],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
    }


def _serialize_excuse_list(
    result: dict,
) -> dict:
    payload = {
        "items": [
            _serialize_excuse(excuse)
            for excuse in result["items"]
        ],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
    }

    return ExcuseListResponseSchema.model_validate(
        payload
    ).model_dump(
        mode="json"
    )


@staff_bp.post("")
@role_required(*MANAGEMENT_ROLES)
def create_staff_route():
    payload, error = _validate_json(
        StaffCreateSchema
    )

    if error:
        return error

    try:
        user = _current_user()
        clinic_id = _current_clinic_id(user)

        staff = create_staff(
            clinic_id=clinic_id,
            first_name=payload.first_name,
            last_name=payload.last_name,
            user_id=payload.user_id,
            specialty=payload.specialty,
            phone=payload.phone,
            email=payload.email,
            hired_at=payload.hired_at,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Staff created successfully"
                    ),
                    "data": _serialize_staff(
                        staff
                    ),
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.get("")
@role_required(*STAFF_VIEW_ROLES)
def list_staff_route():
    try:
        user = _current_user()
        clinic_id = _current_clinic_id(user)

        payload, error = _validate_query(
            StaffListQuerySchema
        )

        if error:
            return error

        result = list_staff(
            clinic_id=clinic_id,
            status=payload.status,
            search=payload.search,
            page=payload.page,
            per_page=payload.per_page,
        )

        return (
            jsonify(
                {
                    "data": _serialize_paginated(
                        result,
                        _serialize_staff,
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.get("/<int:staff_id>")
@role_required(*STAFF_VIEW_ROLES)
def get_staff_route(
    staff_id: int,
):
    try:
        clinic_id = _current_clinic_id()

        staff = get_staff(
            staff_id=staff_id,
            clinic_id=clinic_id,
        )

        return (
            jsonify(
                {
                    "data": _serialize_staff(
                        staff
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.patch("/<int:staff_id>")
@role_required(*MANAGEMENT_ROLES)
def update_staff_route(
    staff_id: int,
):
    payload, error = _validate_json(
        StaffUpdateSchema
    )

    if error:
        return error

    try:
        clinic_id = _current_clinic_id()

        fields = payload.model_dump(
            exclude_unset=True
        )

        staff = update_staff(
            staff_id=staff_id,
            clinic_id=clinic_id,
            **fields,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Staff updated successfully"
                    ),
                    "data": _serialize_staff(
                        staff
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.patch("/<int:staff_id>/department")
@role_required(*MANAGEMENT_ROLES)
def update_staff_department_route(
    staff_id: int,
):
    payload, error = _validate_json(
        StaffDepartmentUpdateSchema
    )

    if error:
        return error

    try:
        clinic_id = _current_clinic_id()

        memberships = get_staff_departments(
            staff_id=staff_id,
            clinic_id=clinic_id,
            include_ended=False,
        )

        if payload.department_id is None:
            primary_membership = next(
                (
                    membership
                    for membership in memberships
                    if membership.is_primary
                ),
                None,
            )

            if primary_membership is not None:
                remove_staff_from_department(
                    staff_id=staff_id,
                    clinic_id=clinic_id,
                    department_id=(
                        primary_membership.department_id
                    ),
                )

        else:
            existing = next(
                (
                    membership
                    for membership in memberships
                    if (
                        membership.department_id
                        == payload.department_id
                    )
                ),
                None,
            )

            if existing is None:
                assign_staff_to_department(
                    staff_id=staff_id,
                    clinic_id=clinic_id,
                    department_id=payload.department_id,
                    is_primary=True,
                )
            elif not existing.is_primary:
                set_primary_department(
                    staff_id=staff_id,
                    clinic_id=clinic_id,
                    department_id=payload.department_id,
                )

        staff = get_staff(
            staff_id=staff_id,
            clinic_id=clinic_id,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Staff department updated successfully"
                    ),
                    "data": _serialize_staff(
                        staff
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.patch("/<int:staff_id>/status")
@role_required(*MANAGEMENT_ROLES)
def change_staff_status_route(
    staff_id: int,
):
    payload, error = _validate_json(
        StaffStatusUpdateSchema
    )

    if error:
        return error

    try:
        clinic_id = _current_clinic_id()

        staff = change_staff_status(
            staff_id=staff_id,
            clinic_id=clinic_id,
            new_status=payload.status,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Staff status updated successfully"
                    ),
                    "data": _serialize_staff(
                        staff
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.post("/leave")
@role_required(*STAFF_VIEW_ROLES)
def request_leave_route():
    payload, error = _validate_json(
        LeaveRequestCreateSchema
    )

    if error:
        return error

    try:
        user = _current_user()
        clinic_id = _current_clinic_id(user)
        staff_id = _current_staff_id(user)

        leave = request_leave(
            clinic_id=clinic_id,
            staff_id=staff_id,
            actor_user_id=user.id,
            leave_type=payload.leave_type,
            start_date=payload.start_date,
            end_date=payload.end_date,
            reason=payload.reason,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Leave request submitted successfully"
                    ),
                    "data": _serialize_leave(
                        leave, clinic_id=clinic_id
                    ),
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.get("/leave")
@role_required(*STAFF_VIEW_ROLES)
def list_leave_route():
    try:
        user = _current_user()
        clinic_id = _current_clinic_id(user)

        payload, error = _validate_query(
            LeaveListQuerySchema
        )

        if error:
            return error

        staff_id = payload.staff_id

        if not _is_review_manager(user):
            staff_id = _current_staff_id(user)

        result = list_leave_requests(
            clinic_id=clinic_id,
            staff_id=staff_id,
            status=payload.status,
            page=payload.page,
            per_page=payload.per_page,
        )

        return (
            jsonify(
                {
                    "data": _serialize_paginated(
                        result,
                        lambda row: _serialize_leave(row, clinic_id=clinic_id),
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.get("/leave/<int:leave_id>")
@role_required(*STAFF_VIEW_ROLES)
def get_leave_route(
    leave_id: int,
):
    try:
        user = _current_user()
        clinic_id = _current_clinic_id(user)

        leave = get_leave_request(
            leave_id=leave_id,
            clinic_id=clinic_id,
        )

        if not _is_review_manager(user):
            staff_id = _current_staff_id(user)

            if leave.staff_id != staff_id:
                raise NotFoundError(
                    f"Leave request {leave_id} not found"
                )

        return (
            jsonify(
                {
                    "data": _serialize_leave(
                        leave, clinic_id=clinic_id
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.post("/leave/<int:leave_id>/approve")
@role_required(*LEAVE_MANAGEMENT_ROLES)
def approve_leave_route(
    leave_id: int,
):
    payload, error = _validate_json(
        LeaveReviewSchema
    )

    if error:
        return error

    try:
        user = _current_user()
        clinic_id = _current_clinic_id()

        leave = approve_leave_request(
            leave_id=leave_id,
            clinic_id=clinic_id,
            reviewer_user_id=user.id,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Leave request approved successfully"
                    ),
                    "data": _serialize_leave(
                        leave, clinic_id=clinic_id
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.post("/leave/<int:leave_id>/reject")
@role_required(*LEAVE_MANAGEMENT_ROLES)
def reject_leave_route(
    leave_id: int,
):
    payload, error = _validate_json(
        LeaveRejectSchema
    )

    if error:
        return error

    try:
        user = _current_user()
        clinic_id = _current_clinic_id()

        leave = reject_leave_request(
            leave_id=leave_id,
            clinic_id=clinic_id,
            reviewer_user_id=user.id,
            reason=payload.reason,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Leave request rejected successfully"
                    ),
                    "data": _serialize_leave(
                        leave, clinic_id=clinic_id
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.post("/excuses")
@role_required(*STAFF_VIEW_ROLES)
def create_excuse_route():
    payload, error = _validate_json(
        ExcuseCreateSchema
    )

    if error:
        return error

    try:
        user = _current_user()
        clinic_id = _current_clinic_id(user)
        staff_id = _current_staff_id(user)

        excuse = create_excuse(
            clinic_id=clinic_id,
            staff_id=staff_id,
            excuse_type=payload.excuse_type,
            description=payload.description,
            leave_request_id=payload.leave_request_id,
            document_url=payload.document_url,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Excuse submitted successfully"
                    ),
                    "data": _serialize_excuse(
                        excuse
                    ),
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.get("/excuses")
@role_required(*STAFF_VIEW_ROLES)
def list_excuses_route():
    try:
        user = _current_user()
        clinic_id = _current_clinic_id(user)

        payload, error = _validate_query(
            ExcuseListQuerySchema
        )

        if error:
            return error

        staff_id = payload.staff_id

        if not _is_review_manager(user):
            staff_id = _current_staff_id(user)

        result = list_excuses(
            clinic_id=clinic_id,
            staff_id=staff_id,
            leave_request_id=payload.leave_request_id,
            excuse_type=payload.excuse_type,
            status=payload.status,
            page=payload.page,
            per_page=payload.per_page,
        )

        return (
            jsonify(
                {
                    "data": _serialize_excuse_list(
                        result
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.get("/excuses/me")
@role_required(*STAFF_VIEW_ROLES)
def get_my_excuses_route():
    try:
        user = _current_user()
        clinic_id = _current_clinic_id(user)
        staff_id = _current_staff_id(user)

        payload, error = _validate_query(
            ExcuseListQuerySchema
        )

        if error:
            return error

        result = get_my_excuses(
            clinic_id=clinic_id,
            staff_id=staff_id,
            page=payload.page,
            per_page=payload.per_page,
        )

        return (
            jsonify(
                {
                    "data": _serialize_excuse_list(
                        result
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.get("/excuses/<int:excuse_id>")
@role_required(*STAFF_VIEW_ROLES)
def get_excuse_route(
    excuse_id: int,
):
    try:
        user = _current_user()
        clinic_id = _current_clinic_id(user)

        excuse = get_excuse(
            excuse_id=excuse_id,
            clinic_id=clinic_id,
        )

        if not _is_review_manager(user):
            staff_id = _current_staff_id(user)

            if excuse.staff_id != staff_id:
                raise NotFoundError(
                    f"Excuse {excuse_id} not found"
                )

        return (
            jsonify(
                {
                    "data": _serialize_excuse(
                        excuse
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.post("/excuses/<int:excuse_id>/approve")
@role_required(*EXCUSE_REVIEW_ROLES)
def approve_excuse_route(
    excuse_id: int,
):
    try:
        user = _current_user()
        clinic_id = _current_clinic_id()

        excuse = approve_excuse(
            excuse_id=excuse_id,
            clinic_id=clinic_id,
            reviewer_user_id=user.id,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Excuse approved successfully"
                    ),
                    "data": _serialize_excuse(
                        excuse
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.post("/excuses/<int:excuse_id>/reject")
@role_required(*EXCUSE_REVIEW_ROLES)
def reject_excuse_route(
    excuse_id: int,
):
    payload, error = _validate_json(
        ExcuseRejectSchema
    )

    if error:
        return error

    try:
        user = _current_user()
        clinic_id = _current_clinic_id()

        excuse = reject_excuse(
            excuse_id=excuse_id,
            clinic_id=clinic_id,
            reviewer_user_id=user.id,
            reason=payload.reason,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Excuse rejected successfully"
                    ),
                    "data": _serialize_excuse(
                        excuse
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.post("/payroll")
@role_required(*PAYROLL_ROLES)
def create_payroll_route():
    payload, error = _validate_json(
        PayrollCreateSchema
    )

    if error:
        return error

    try:
        clinic_id = _current_clinic_id()

        record = create_payroll_record(
            clinic_id=clinic_id,
            staff_id=payload.staff_id,
            pay_period_start=payload.pay_period_start,
            pay_period_end=payload.pay_period_end,
            base_salary=payload.base_salary,
            bonuses=payload.bonuses,
            deductions=payload.deductions,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Payroll record created successfully"
                    ),
                    "data": _serialize_payroll(
                        record, clinic_id=clinic_id
                    ),
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.post("/payroll/generate")
@role_required(*PAYROLL_ROLES)
def generate_payroll_route():
    payload, error = _validate_json(
        PayrollGenerateSchema
    )

    if error:
        return error

    try:
        clinic_id = _current_clinic_id()

        records = generate_payroll_for_period(
            clinic_id=clinic_id,
            pay_period_start=payload.pay_period_start,
            pay_period_end=payload.pay_period_end,
            salary_lookup=payload.salary_lookup,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Payroll generated successfully"
                    ),
                    "data": [
                        _serialize_payroll(record, clinic_id=clinic_id)
                        for record in records
                    ],
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.get("/payroll")
@role_required(*PAYROLL_ROLES)
def list_payroll_route():
    try:
        clinic_id = _current_clinic_id()

        payload, error = _validate_query(
            PayrollListQuerySchema
        )

        if error:
            return error

        if payload.staff_id is not None:
            result = list_payroll_for_staff(
                clinic_id=clinic_id,
                staff_id=payload.staff_id,
                page=payload.page,
                per_page=payload.per_page,
            )
        else:
            result = list_payroll(
                clinic_id=clinic_id,
                page=payload.page,
                per_page=payload.per_page,
            )

        return (
            jsonify(
                {
                    "data": _serialize_paginated(
                        result,
                        lambda row: _serialize_payroll(row, clinic_id=clinic_id),
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.get("/payroll/<int:record_id>")
@role_required(*PAYROLL_ROLES)
def get_payroll_route(
    record_id: int,
):
    try:
        clinic_id = _current_clinic_id()

        record = get_payroll_record(
            record_id=record_id,
            clinic_id=clinic_id,
        )

        return (
            jsonify(
                {
                    "data": _serialize_payroll(
                        record, clinic_id=clinic_id
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_bp.post("/payroll/<int:record_id>/pay")
@role_required(*PAYROLL_ROLES)
def mark_payroll_paid_route(
    record_id: int,
):
    try:
        clinic_id = _current_clinic_id()

        record = mark_payroll_paid(
            record_id=record_id,
            clinic_id=clinic_id,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Payroll marked as paid successfully"
                    ),
                    "data": _serialize_payroll(
                        record, clinic_id=clinic_id
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)
