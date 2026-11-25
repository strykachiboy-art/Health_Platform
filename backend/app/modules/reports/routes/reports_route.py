from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError as PydanticValidationError

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import DomainError, ValidationError
from app.core.utils.decorators import role_required
from app.extensions import db

from app.modules.reports.schemas.reports_schema import (
    GeneratedReportListResponseSchema,
    GeneratedReportResponseSchema,
    ReportGenerateSchema,
    ReportQuerySchema,
)
from app.modules.reports.services.reports_service import (
    generate_report,
    get_report,
    list_reports,
)


reports_bp = Blueprint(
    "reports",
    __name__,
    url_prefix="/reports",
)


REPORT_GENERATION_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
    Role.ACCOUNTANT,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
)

REPORT_VIEW_ROLES = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
    Role.ACCOUNTANT,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
)


def _parse_authenticated_user_id(
    identity,
) -> int:
    """
    Parse the JWT identity into a positive integer user ID.

    JWT identities are commonly serialized as strings, so numeric
    strings are accepted. Arbitrary coercion is rejected.
    """
    if isinstance(
        identity,
        bool,
    ):
        raise ValidationError(
            "Invalid authentication identity"
        )

    if isinstance(
        identity,
        int,
    ):
        user_id = identity

    elif isinstance(
        identity,
        str,
    ):
        identity = identity.strip()

        if not identity.isdecimal():
            raise ValidationError(
                "Invalid authentication identity"
            )

        try:
            user_id = int(identity)
        except ValueError as exc:
            raise ValidationError(
                "Invalid authentication identity"
            ) from exc

    else:
        raise ValidationError(
            "Invalid authentication identity"
        )

    if user_id <= 0:
        raise ValidationError(
            "Invalid authentication identity"
        )

    return user_id


def _get_current_user() -> User:
    """Return the authenticated active user."""
    identity = get_jwt_identity()

    user_id = _parse_authenticated_user_id(
        identity
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


def _get_current_user_id(
    user: User | None = None,
) -> int:
    """Return the authenticated user ID."""
    if user is not None:
        user_id = getattr(
            user,
            "id",
            None,
        )

        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id <= 0
        ):
            raise ValidationError(
                "Invalid authenticated user ID"
            )

        return user_id

    return _parse_authenticated_user_id(
        get_jwt_identity()
    )


def _get_current_clinic_id(user: User | None = None) -> int:
    # Authorization comes from the revalidated tenant/clinic JWT, never from
    # an account's default clinic field or its platform role.
    from app.core.utils.decorators import get_current_clinic_id
    return get_current_clinic_id(required=True)

def _is_super_admin(
    user: User,
) -> bool:
    """Return whether the authenticated user is a super admin."""
    return (
        getattr(
            user,
            "role",
            None,
        )
        == Role.SUPER_ADMIN
    )


def _handle_route_error(
    exc: Exception,
):
    """Convert domain errors to API responses."""
    if isinstance(
        exc,
        DomainError,
    ):
        return (
            jsonify(
                {
                    "success": False,
                    "error": str(exc),
                }
            ),
            exc.status_code,
        )

    return (
        jsonify(
            {
                "success": False,
                "error": "An unexpected error occurred",
            }
        ),
        500,
    )


def _validation_error_response(
    exc: PydanticValidationError,
):
    """Return a JSON-safe Pydantic validation response."""
    details = []

    for error in exc.errors():
        item = dict(error)
        ctx = item.get(
            "ctx"
        )

        if (
            isinstance(ctx, dict)
            and "error" in ctx
        ):
            ctx = dict(ctx)
            ctx["error"] = str(
                ctx["error"]
            )
            item["ctx"] = ctx

        details.append(
            item
        )

    return (
        jsonify(
            {
                "success": False,
                "error": "Validation failed",
                "details": details,
            }
        ),
        422,
    )


def _serialize_report(
    report,
) -> dict:
    """Serialize a report ORM instance."""
    return (
        GeneratedReportResponseSchema
        .model_validate(
            report
        )
        .model_dump(
            mode="json"
        )
    )


def _serialize_report_list(
    result: dict,
) -> dict:
    """Serialize a paginated report result."""
    return (
        GeneratedReportListResponseSchema
        .model_validate(
            result
        )
        .model_dump(
            mode="json"
        )
    )


@reports_bp.post("")
@role_required(
    *REPORT_GENERATION_ROLES
)
def create_report():
    """Generate a report for the authenticated user's clinic."""
    try:
        user = _get_current_user()

        clinic_id = _get_current_clinic_id(
            user
        )

        requester_user_id = _get_current_user_id(
            user
        )

        payload = ReportGenerateSchema.model_validate(
            request.get_json(
                silent=True
            ) or {}
        )

        report = generate_report(
            requester_user_id=requester_user_id,
            clinic_id=clinic_id,
            report_type=payload.report_type,
            report_format=payload.report_format,
            filters=(
                payload.filters.model_dump(
                    mode="json"
                )
                if payload.filters is not None
                else None
            ),
        )

        return (
            jsonify(
                {
                    "success": True,
                    "message": "Report generated successfully",
                    "data": _serialize_report(
                        report
                    ),
                }
            ),
            201,
        )

    except PydanticValidationError as exc:
        return _validation_error_response(
            exc
        )

    except Exception as exc:
        return _handle_route_error(
            exc
        )


@reports_bp.get("")
@role_required(
    *REPORT_VIEW_ROLES
)
def get_reports():
    """Return paginated reports visible to the authenticated user."""
    try:
        user = _get_current_user()

        requester_user_id = _get_current_user_id(
            user
        )

        query_payload = {
            key: request.args.get(
                key
            )
            for key in (
                "report_type",
                "report_format",
                "date_from",
                "date_to",
                "generated_by_id",
                "page",
                "per_page",
            )
        }

        query_payload = {
            key: value
            for key, value in query_payload.items()
            if value is not None
        }

        payload = ReportQuerySchema.model_validate(
            query_payload
        )

        clinic_id = _get_current_clinic_id(
            user
        )

        result = list_reports(
            requester_user_id=requester_user_id,
            clinic_id=clinic_id,
            generated_by_id=payload.generated_by_id,
            report_type=payload.report_type,
            report_format=payload.report_format,
            date_from=payload.date_from,
            date_to=payload.date_to,
            page=payload.page,
            per_page=payload.per_page,
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": _serialize_report_list(
                        result
                    ),
                }
            ),
            200,
        )

    except PydanticValidationError as exc:
        return _validation_error_response(
            exc
        )

    except Exception as exc:
        return _handle_route_error(
            exc
        )


@reports_bp.get(
    "/<int:report_id>"
)
@role_required(
    *REPORT_VIEW_ROLES
)
def get_single_report(
    report_id: int,
):
    """Return one report after service authorization checks."""
    try:
        user = _get_current_user()

        requester_user_id = _get_current_user_id(
            user
        )
        bound_clinic_id = _get_current_clinic_id(user)

        report = get_report(
            report_id=report_id,
            requester_user_id=requester_user_id,
        )

        if report.clinic_id != bound_clinic_id:
            from app.core.exceptions import NotFoundError
            raise NotFoundError("Report not found")

        return (
            jsonify(
                {
                    "success": True,
                    "data": _serialize_report(
                        report
                    ),
                }
            ),
            200,
        )

    except PydanticValidationError as exc:
        return _validation_error_response(
            exc
        )

    except Exception as exc:
        return _handle_route_error(
            exc
        )