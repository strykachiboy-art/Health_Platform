from __future__ import annotations

from flask import Blueprint, g, jsonify, request
from pydantic import ValidationError as PydanticValidationError

from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import role_required

from app.modules.department.schemas.department_schema import (
    DepartmentCreateSchema,
    DepartmentListQuerySchema,
    DepartmentResponseSchema,
    DepartmentStatusUpdateSchema,
    DepartmentUpdateSchema,
)
from app.modules.department.services.department_service import (
    change_department_status,
    create_department,
    get_department,
    list_departments,
    update_department,
)


department_bp = Blueprint(
    "department",
    __name__,
    url_prefix="/departments",
)


DEPARTMENT_MANAGEMENT_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
)

DEPARTMENT_VIEW_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.RECEPTIONIST,
    Role.ACCOUNTANT,
    Role.PARAMEDIC,
    Role.EMT,
    Role.DRIVER,
    Role.AMBULANCE_DISPATCHER,
    Role.AMBULANCE_COORDINATOR,
    Role.OTHER,
)


def _current_clinic_id() -> int:
    from app.core.utils.decorators import get_administrative_clinic_id
    clinic_id = get_administrative_clinic_id()

    if (
        isinstance(clinic_id, bool)
        or not isinstance(clinic_id, int)
        or clinic_id <= 0
    ):
        raise ValidationError(
            "Clinic context is required"
        )

    return clinic_id


def _sanitize_pydantic_errors(errors):
    sanitized = []

    for error in errors:
        item = dict(error)

        if (
            "ctx" in item
            and isinstance(item["ctx"], dict)
        ):
            item["ctx"] = {
                key: str(value)
                for key, value in item["ctx"].items()
            }

        sanitized.append(item)

    return sanitized


def _validate_json(schema):
    try:
        return schema.model_validate(
            request.get_json(
                silent=True
            ) or {}
        ), None

    except PydanticValidationError as exc:
        return (
            None,
            (
                jsonify(
                    {
                        "error": "Validation error",
                        "details": _sanitize_pydantic_errors(
                            exc.errors()
                        ),
                    }
                ),
                422,
            ),
        )


def _validate_query():
    try:
        return DepartmentListQuerySchema.model_validate(
            request.args.to_dict()
        ), None

    except PydanticValidationError as exc:
        return (
            None,
            (
                jsonify(
                    {
                        "error": "Validation error",
                        "details": _sanitize_pydantic_errors(
                            exc.errors()
                        ),
                    }
                ),
                422,
            ),
        )


def _serialize_department(
    department,
):
    return DepartmentResponseSchema.model_validate(
        department
    ).model_dump(
        mode="json"
    )


def _domain_error_response(
    exc: DomainError,
):
    if isinstance(exc, NotFoundError):
        status = 404
    elif isinstance(exc, ConflictError):
        status = 409
    elif isinstance(exc, ValidationError):
        status = 422
    else:
        raise exc

    return (
        jsonify(
            {
                "error": str(exc),
            }
        ),
        status,
    )


@department_bp.post("")
@role_required(*DEPARTMENT_MANAGEMENT_ROLES)
def create_department_route():
    payload, error = _validate_json(
        DepartmentCreateSchema
    )

    if error:
        return error

    try:
        department = create_department(
            clinic_id=_current_clinic_id(),
            code=payload.code,
            name=payload.name,
            description=payload.description,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Department created successfully"
                    ),
                    "data": _serialize_department(
                        department
                    ),
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@department_bp.get("")
@role_required(*DEPARTMENT_VIEW_ROLES)
def list_departments_route():
    query, error = _validate_query()

    if error:
        return error

    try:
        departments = list_departments(
            clinic_id=_current_clinic_id(),
            status=query.status,
            search=query.search,
        )

        return (
            jsonify(
                {
                    "data": [
                        _serialize_department(
                            department
                        )
                        for department in departments
                    ],
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@department_bp.get("/<int:department_id>")
@role_required(*DEPARTMENT_VIEW_ROLES)
def get_department_route(
    department_id: int,
):
    try:
        department = get_department(
            clinic_id=_current_clinic_id(),
            department_id=department_id,
        )

        return (
            jsonify(
                {
                    "data": _serialize_department(
                        department
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@department_bp.patch("/<int:department_id>")
@role_required(*DEPARTMENT_MANAGEMENT_ROLES)
def update_department_route(
    department_id: int,
):
    payload, error = _validate_json(
        DepartmentUpdateSchema
    )

    if error:
        return error

    try:
        department = update_department(
            clinic_id=_current_clinic_id(),
            department_id=department_id,
            **payload.model_dump(
                exclude_unset=True
            ),
        )

        return (
            jsonify(
                {
                    "message": (
                        "Department updated successfully"
                    ),
                    "data": _serialize_department(
                        department
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@department_bp.patch(
    "/<int:department_id>/status"
)
@role_required(*DEPARTMENT_MANAGEMENT_ROLES)
def update_department_status_route(
    department_id: int,
):
    payload, error = _validate_json(
        DepartmentStatusUpdateSchema
    )

    if error:
        return error

    try:
        department = change_department_status(
            clinic_id=_current_clinic_id(),
            department_id=department_id,
            new_status=payload.status,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Department status updated successfully"
                    ),
                    "data": _serialize_department(
                        department
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)