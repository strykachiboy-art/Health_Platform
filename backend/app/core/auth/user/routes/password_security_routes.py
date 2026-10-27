from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
)
from flask_jwt_extended import (
    get_jwt_identity,
    jwt_required,
)
from pydantic import ValidationError as PydanticValidationError

from app.core.auth.user.schema.password_security_schema import (
    ChangePasswordSchema,
    PasswordResetConfirmSchema,
    PasswordResetRequestSchema,
)
from app.core.auth.user.services.password_security_service import (
    change_password,
    confirm_password_reset,
    queue_password_security_notice,
    request_password_reset,
)
from app.core.exceptions import DomainError
from app.extensions import limiter


password_security_bp = Blueprint(
    "password_security",
    __name__,
    url_prefix="/auth",
)


def _safe_validation_errors(
    exc: PydanticValidationError,
) -> list[dict]:
    return [
        {
            "type": error.get(
                "type"
            ),
            "loc": list(
                error.get(
                    "loc",
                    (),
                )
            ),
            "msg": error.get(
                "msg"
            ),
        }
        for error in exc.errors()
    ]


def _validation_failure(
    exc: PydanticValidationError,
):
    return jsonify(
        {
            "success": False,
            "error": "Validation failed",
            "details": (
                _safe_validation_errors(
                    exc
                )
            ),
        }
    ), 400


def _domain_failure(
    exc: DomainError,
):
    body = {
        "success": False,
        "error": str(
            exc
        ),
    }

    if exc.code is not None:
        body["code"] = (
            exc.code
        )

    return jsonify(
        body
    ), exc.status_code


@password_security_bp.post(
    "/change-password"
)
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_CHANGE_PASSWORD_RATE_LIMIT",
        "5 per minute",
    )
)
@jwt_required()
def change_password_route():
    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:
        data = (
            ChangePasswordSchema.model_validate(
                payload
            )
        )
    except PydanticValidationError as exc:
        return _validation_failure(
            exc
        )

    identity = (
        get_jwt_identity()
    )

    try:
        user_id = int(
            identity
        )
    except (
        TypeError,
        ValueError,
    ):
        return jsonify(
            {
                "success": False,
                "error": (
                    "Invalid authentication identity"
                ),
            }
        ), 401

    try:
        change_password(
            user_id=user_id,
            current_password=(
                data.current_password.get_secret_value()
            ),
            new_password=(
                data.new_password.get_secret_value()
            ),
            confirm_password=(
                data.confirm_password.get_secret_value()
            ),
            ip_address=request.remote_addr,
        )
    except DomainError as exc:
        return _domain_failure(
            exc
        )

    queue_password_security_notice(
        user_id=user_id,
        event="changed",
    )

    return jsonify(
        {
            "success": True,
            "message": (
                "Password changed successfully. "
                "Sign in again to continue."
            ),
            "data": {
                "reauthentication_required": True,
            },
        }
    ), 200


@password_security_bp.post(
    "/password-reset/request"
)
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_PASSWORD_RESET_REQUEST_RATE_LIMIT",
        "5 per minute",
    )
)
def request_password_reset_route():
    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:
        data = (
            PasswordResetRequestSchema.model_validate(
                payload
            )
        )
    except PydanticValidationError as exc:
        return _validation_failure(
            exc
        )

    try:
        request_password_reset(
            email=str(
                data.email
            ),
        )
    except DomainError as exc:
        return _domain_failure(
            exc
        )

    return jsonify(
        {
            "success": True,
            "message": (
                "If an eligible account exists for that email, "
                "password reset instructions will be sent."
            ),
        }
    ), 202


@password_security_bp.post(
    "/password-reset/confirm"
)
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_PASSWORD_RESET_CONFIRM_RATE_LIMIT",
        "10 per minute",
    )
)
def confirm_password_reset_route():
    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:
        data = (
            PasswordResetConfirmSchema.model_validate(
                payload
            )
        )
    except PydanticValidationError as exc:
        return _validation_failure(
            exc
        )

    try:
        confirm_password_reset(
            token=(
                data.token.get_secret_value()
            ),
            new_password=(
                data.new_password.get_secret_value()
            ),
            confirm_password=(
                data.confirm_password.get_secret_value()
            ),
            recovery_factor=(
                data.recovery_factor.get_secret_value()
                if data.recovery_factor is not None
                else None
            ),
            ip_address=request.remote_addr,
        )
    except DomainError as exc:
        return _domain_failure(
            exc
        )

    return jsonify(
        {
            "success": True,
            "message": (
                "Password reset successfully. "
                "Sign in with your new password."
            ),
            "data": {
                "reauthentication_required": True,
            },
        }
    ), 200
