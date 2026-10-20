from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
)

from flask_jwt_extended import (
    decode_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)

from pydantic import ValidationError as PydanticValidationError

from app import db

from app.core.auth.user.models.user_model import User
from app.core.auth.user.schema.user_schema import (
    GoogleAuthCallbackSchema,
    GoogleMobileAuthSchema,
    UserLoginSchema,
    UserRegisterSchema,
)
from app.core.auth.user.schema.clinic_context_schema import (
    ClinicContextSelectSchema,
)
from app.core.auth.user.services.google_auth_service import (
    authenticate_google_code,
    authenticate_google_id_token,
    get_google_authorization_url,
    validate_google_oauth_state,
)
from app.core.auth.user.services.token_service import (
    revoke_current_token,
    revoke_token,
)
from app.core.auth.user.services.clinic_context_service import (
    clear_clinic_context,
    get_current_clinic_context,
    resolve_effective_clinic_id,
    select_clinic_context,
)
from app.core.auth.user.services.user_service import (
    authenticate_user,
    issue_auth_tokens,
    register_user,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import DomainError
from app.extensions import limiter


auth_bp = Blueprint(
    "auth",
    __name__,
    url_prefix="/auth",
)


def _safe_validation_errors(exc):
    return [
        {
            "type": error.get("type"),
            "loc": list(error.get("loc", ())),
            "msg": error.get("msg"),
        }
        for error in exc.errors()
    ]


@auth_bp.post("/register")
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_REGISTER_RATE_LIMIT",
        "10 per minute",
    )
)
def register():
    payload = request.get_json(
        silent=True
    ) or {}

    try:
        data = UserRegisterSchema.model_validate(
            payload
        )
    except PydanticValidationError as exc:
        return jsonify(
            {
                "success": False,
                "error": "Validation failed",
                "details": _safe_validation_errors(
                    exc
                ),
            }
        ), 400

    try:
        user = register_user(
            email=str(data.email),
            password=data.password,
            role=Role.PATIENT,
            clinic_id=data.clinic_id,
        )
    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "data": {
                "id": user.id,
                "email": user.email,
                "role": user.role.value,
                "clinic_id": user.clinic_id,
                "is_active": user.is_active,
                "created_at": (
                    user.created_at.isoformat()
                    if user.created_at
                    else None
                ),
                "last_login_at": None,
            },
        }
    ), 201


@auth_bp.post("/login")
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_LOGIN_RATE_LIMIT",
        "5 per minute",
    )
)
def login():
    payload = request.get_json(
        silent=True
    ) or {}

    try:
        data = UserLoginSchema.model_validate(
            payload
        )
    except PydanticValidationError as exc:
        return jsonify(
            {
                "success": False,
                "error": "Validation failed",
                "details": _safe_validation_errors(
                    exc
                ),
            }
        ), 400

    try:
        result = authenticate_user(
            email=str(data.email),
            password=data.password,
        )
    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "data": result,
        }
    ), 200


@auth_bp.post("/google/mobile")
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_GOOGLE_RATE_LIMIT",
        "5 per minute",
    )
)
def google_mobile_login():
    payload = request.get_json(silent=True) or {}

    try:
        data = GoogleMobileAuthSchema.model_validate(payload)
    except PydanticValidationError as exc:
        return jsonify(
            {
                "success": False,
                "error": "Validation failed",
                "details": _safe_validation_errors(exc),
            }
        ), 400

    try:
        result = authenticate_google_id_token(
            id_token=data.id_token,
        )
    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "data": result,
        }
    ), 200


@auth_bp.get("/google")
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_GOOGLE_RATE_LIMIT",
        "10 per minute",
    )
)
def google_login():
    try:
        authorization_url, state = (
            get_google_authorization_url()
        )
    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "data": {
                "authorization_url": authorization_url,
                "state": state,
            },
        }
    ), 200


@auth_bp.get("/google/callback")
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_GOOGLE_RATE_LIMIT",
        "10 per minute",
    )
)
def google_callback():
    google_error = request.args.get(
        "error"
    )

    if google_error:
        return jsonify(
            {
                "success": False,
                "error": google_error,
                "error_description": request.args.get(
                    "error_description"
                ),
            }
        ), 400

    payload = {
        "code": request.args.get(
            "code"
        ),
        "state": request.args.get(
            "state"
        ),
    }

    try:
        data = GoogleAuthCallbackSchema.model_validate(
            payload
        )
    except PydanticValidationError as exc:
        return jsonify(
            {
                "success": False,
                "error": "Validation failed",
                "details": _safe_validation_errors(
                    exc
                ),
            }
        ), 400

    try:
        validate_google_oauth_state(
            data.state
        )

        result = authenticate_google_code(
            code=data.code
        )
    except DomainError as exc:
        db.session.rollback()

        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "data": result,
        }
    ), 200


@auth_bp.get("/clinic-context")
@jwt_required()
def get_clinic_context():
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return jsonify(
            {
                "success": False,
                "error": "Invalid authentication identity",
            }
        ), 401

    try:
        payload = get_jwt()
        result = get_current_clinic_context(
            user_id=user_id,
            jwt_payload=payload,
        )
    except DomainError as exc:
        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "data": result,
        }
    ), 200


@auth_bp.post("/clinic-context")
@jwt_required()
def select_clinic_context_route():
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return jsonify(
            {
                "success": False,
                "error": "Invalid authentication identity",
            }
        ), 401

    user = db.session.get(User, user_id)

    if user is None:
        return jsonify(
            {
                "success": False,
                "error": "User not found",
            }
        ), 401

    if user.role is not Role.SUPER_ADMIN:
        return jsonify(
            {
                "success": False,
                "error": (
                    "Only a super administrator can select "
                    "a clinic context"
                ),
            }
        ), 403

    payload = request.get_json(
        silent=True
    ) or {}

    try:
        data = ClinicContextSelectSchema.model_validate(
            payload
        )
    except PydanticValidationError as exc:
        return jsonify(
            {
                "success": False,
                "error": "Validation failed",
                "details": _safe_validation_errors(
                    exc
                ),
            }
        ), 400

    try:
        select_clinic_context(
            user_id=user.id,
            clinic_id=data.clinic_id,
        )

        tokens = issue_auth_tokens(
            user,
            clinic_context_id=data.clinic_id,
        )
    except DomainError as exc:
        db.session.rollback()

        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "data": tokens,
        }
    ), 200


@auth_bp.delete("/clinic-context")
@jwt_required()
def clear_clinic_context_route():
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return jsonify(
            {
                "success": False,
                "error": "Invalid authentication identity",
            }
        ), 401

    user = db.session.get(User, user_id)

    if user is None:
        return jsonify(
            {
                "success": False,
                "error": "User not found",
            }
        ), 401

    if user.role is not Role.SUPER_ADMIN:
        return jsonify(
            {
                "success": False,
                "error": (
                    "Only a super administrator can clear "
                    "a clinic context"
                ),
            }
        ), 403

    try:
        jwt_payload = get_jwt()
        clinic_context_id = jwt_payload.get(
            "clinic_context_id"
        )

        clear_clinic_context(
            user_id=user.id,
            clinic_id=clinic_context_id,
        )

        tokens = issue_auth_tokens(
            user
        )
    except DomainError as exc:
        db.session.rollback()

        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "data": tokens,
        }
    ), 200


@auth_bp.post("/refresh")
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_REFRESH_RATE_LIMIT",
        "20 per minute",
    )
)
@jwt_required(refresh=True)
def refresh():
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        return jsonify(
            {
                "success": False,
                "error": "Invalid authentication identity",
            }
        ), 401

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        return jsonify(
            {
                "success": False,
                "error": "User not found",
            }
        ), 401

    if not user.is_active:
        return jsonify(
            {
                "success": False,
                "error": (
                    "This account has been deactivated"
                ),
            }
        ), 401

    try:
        jwt_payload = get_jwt()

        clinic_context_id = None

        if user.role is Role.SUPER_ADMIN:
            clinic_context_id = resolve_effective_clinic_id(
                user_id=user.id,
                jwt_payload=jwt_payload,
            )

        revoke_current_token()

        tokens = issue_auth_tokens(
            user,
            clinic_context_id=clinic_context_id,
        )

    except DomainError as exc:
        db.session.rollback()

        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "data": tokens,
        }
    ), 200


@auth_bp.post("/logout")
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_LOGOUT_RATE_LIMIT",
        "20 per minute",
    )
)
@jwt_required()
def logout():
    payload = request.get_json(
        silent=True
    ) or {}

    refresh_token = payload.get(
        "refresh_token"
    )

    if not refresh_token:
        return jsonify(
            {
                "success": False,
                "error": "Refresh token is required",
            }
        ), 400

    try:
        refresh_payload = decode_token(
            refresh_token,
            allow_expired=False,
        )
    except Exception:
        return jsonify(
            {
                "success": False,
                "error": "Invalid refresh token",
            }
        ), 401

    if refresh_payload.get(
        "type"
    ) != "refresh":
        return jsonify(
            {
                "success": False,
                "error": "Invalid refresh token",
            }
        ), 401

    current_user_id = get_jwt_identity()

    refresh_user_id = refresh_payload.get(
        "sub"
    )

    if (
        str(current_user_id)
        != str(refresh_user_id)
    ):
        return jsonify(
            {
                "success": False,
                "error": (
                    "Refresh token does not belong "
                    "to the current user"
                ),
            }
        ), 401

    try:
        revoke_token(
            refresh_payload
        )

        revoke_current_token()

    except DomainError as exc:
        db.session.rollback()

        return jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ), exc.status_code

    return jsonify(
        {
            "success": True,
            "message": "Successfully logged out",
        }
    ), 200