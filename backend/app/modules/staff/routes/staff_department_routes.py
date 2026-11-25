from __future__ import annotations

from flask import Blueprint, jsonify, request
from pydantic import ValidationError as PydanticValidationError

from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import (
    get_administrative_clinic_id,
    role_required,
)

from app.modules.staff.schemas.staff_department_schema import (
    StaffDepartmentCreateSchema,
    StaffDepartmentListQuerySchema,
    StaffDepartmentResponseSchema,
)
from app.modules.staff.services.staff_department_service import (
    assign_staff_to_department,
    get_staff_departments,
    list_department_staff,
    remove_staff_from_department,
    set_primary_department,
)


staff_department_bp = Blueprint(
    "staff_department",
    __name__,
    url_prefix="/staff-departments",
)


MANAGEMENT_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
)


VIEW_ROLES = (
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
        return (
            schema.model_validate(
                request.get_json(
                    silent=True
                ) or {}
            ),
            None,
        )

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
        return (
            StaffDepartmentListQuerySchema.model_validate(
                request.args.to_dict()
            ),
            None,
        )

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


def _serialize_membership(
    membership,
):
    return (
        StaffDepartmentResponseSchema
        .model_validate(membership)
        .model_dump(mode="json")
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


@staff_department_bp.post(
    "/staff/<int:staff_id>/department/<int:department_id>"
)
@role_required(*MANAGEMENT_ROLES)
def assign_staff_department_route(
    staff_id: int,
    department_id: int,
):
    payload, error = _validate_json(
        StaffDepartmentCreateSchema
    )

    if error:
        return error

    try:
        if payload.department_id != department_id:
            return (
                jsonify(
                    {
                        "error": (
                            "Department ID in the request body "
                            "must match the URL"
                        ),
                    }
                ),
                422,
            )

        membership = assign_staff_to_department(
            staff_id=staff_id,
            clinic_id=get_administrative_clinic_id(
                required=True
            ),
            department_id=department_id,
            is_primary=payload.is_primary,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Staff department membership "
                        "created successfully"
                    ),
                    "data": _serialize_membership(
                        membership
                    ),
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_department_bp.get(
    "/staff/<int:staff_id>"
)
@role_required(*VIEW_ROLES)
def list_staff_departments_route(
    staff_id: int,
):
    query, error = _validate_query()

    if error:
        return error

    try:
        memberships = get_staff_departments(
            staff_id=staff_id,
            clinic_id=get_administrative_clinic_id(
                required=True
            ),
            include_ended=query.include_ended,
        )

        return (
            jsonify(
                {
                    "data": [
                        _serialize_membership(
                            membership
                        )
                        for membership in memberships
                    ],
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_department_bp.get(
    "/department/<int:department_id>"
)
@role_required(*VIEW_ROLES)
def list_department_staff_route(
    department_id: int,
):
    query, error = _validate_query()

    if error:
        return error

    try:
        memberships = list_department_staff(
            department_id=department_id,
            clinic_id=get_administrative_clinic_id(
                required=True
            ),
            include_ended=query.include_ended,
        )

        return (
            jsonify(
                {
                    "data": [
                        _serialize_membership(
                            membership
                        )
                        for membership in memberships
                    ],
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_department_bp.post(
    "/staff/<int:staff_id>/department/<int:department_id>/primary"
)
@role_required(*MANAGEMENT_ROLES)
def set_primary_staff_department_route(
    staff_id: int,
    department_id: int,
):
    try:
        membership = set_primary_department(
            staff_id=staff_id,
            clinic_id=get_administrative_clinic_id(
                required=True
            ),
            department_id=department_id,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Primary department updated successfully"
                    ),
                    "data": _serialize_membership(
                        membership
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


@staff_department_bp.delete(
    "/staff/<int:staff_id>/department/<int:department_id>"
)
@role_required(*MANAGEMENT_ROLES)
def remove_staff_department_route(
    staff_id: int,
    department_id: int,
):
    try:
        membership = remove_staff_from_department(
            staff_id=staff_id,
            clinic_id=get_administrative_clinic_id(
                required=True
            ),
            department_id=department_id,
        )

        return (
            jsonify(
                {
                    "message": (
                        "Staff department membership ended successfully"
                    ),
                    "data": _serialize_membership(
                        membership
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)
