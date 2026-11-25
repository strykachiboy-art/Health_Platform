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


# G05 enrollment requires a normal authenticated Super Admin session.
# It cannot mint tokens or weaken the G03 shared issuer gate.
def _mfa_enrollment_actor():
    try:
        identity = int(get_jwt_identity())
    except (ValueError, TypeError):
        return None
    actor = db.session.get(User, identity)
    if actor is None or not actor.is_active or actor.role is not Role.SUPER_ADMIN:
        return None
    return actor


@auth_bp.get("/mfa/enrollment/status")
@jwt_required()
def super_admin_mfa_enrollment_status():
    actor = _mfa_enrollment_actor()
    if actor is None:
        return jsonify({"success": False, "error": "Super Admin access required"}), 403
    from app.core.auth.user.services.super_admin_mfa_enrollment_service import (
        get_enrollment_status,
    )
    try:
        result = get_enrollment_status(actor)
    except DomainError as exc:
        return jsonify({"success": False, "error": str(exc)}), exc.status_code
    return jsonify({"success": True, "data": result}), 200


@auth_bp.post("/mfa/enrollment/begin")
@jwt_required()
@limiter.limit("3 per minute")
def super_admin_mfa_enrollment_begin():
    actor = _mfa_enrollment_actor()
    if actor is None:
        return jsonify({"success": False, "error": "Super Admin access required"}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": "Validation failed"}), 400
    from app.core.auth.user.services.super_admin_mfa_enrollment_service import (
        begin_enrollment,
    )
    try:
        result = begin_enrollment(actor, payload.get("current_password"))
    except DomainError as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), exc.status_code
    return jsonify({"success": True, "data": result}), 200


@auth_bp.post("/mfa/enrollment/confirm")
@jwt_required()
@limiter.limit("5 per minute")
def super_admin_mfa_enrollment_confirm():
    actor = _mfa_enrollment_actor()
    if actor is None:
        return jsonify({"success": False, "error": "Super Admin access required"}), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": "Validation failed"}), 400
    from app.core.auth.user.services.super_admin_mfa_enrollment_service import (
        confirm_enrollment,
    )
    try:
        result = confirm_enrollment(actor, payload.get("code"))
    except DomainError as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), exc.status_code
    return jsonify({"success": True, "data": result}), 200


@auth_bp.post("/mfa/complete")
@limiter.limit(
    lambda: current_app.config.get(
        "AUTH_SUPER_ADMIN_LOGIN_MFA_COMPLETE_RATE_LIMIT", "5 per minute"
    )
)
def complete_super_admin_login_mfa():
    # A pre-MFA challenge is deliberately NOT a JWT. This endpoint cannot
    # grant clinic-context access until second-factor verification succeeds.
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"success": False, "error": "Validation failed"}), 400
    from app.core.auth.user.services.super_admin_login_mfa_service import (
        complete_login_challenge,
    )
    try:
        result = complete_login_challenge(
            token=payload.get("challenge_token"),
            factor=payload.get("factor"),
        )
    except DomainError as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), exc.status_code
    return jsonify({"success": True, "data": result}), 200


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
        # Invalidate BOTH old access and refresh tokens, not access JTI alone.
        tokens = issue_auth_tokens(
            user, clinic_context_id=data.clinic_id,
            auto_assign_clinic=False, rotate_session=True,
        )
        db.session.commit()
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
        clinic_context_id = jwt_payload.get("platform_clinic_id")

        clear_clinic_context(
            user_id=user.id,
            clinic_id=clinic_context_id,
        )
        tokens = issue_auth_tokens(
            user, tenant_id=jwt_payload.get('tenant_id'),
            auto_assign_clinic=False, rotate_session=True,
        )
        db.session.commit()
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

        from app.modules.clinic.services.session_authority import context_from_claims
        tenant_id, clinic_context_id = context_from_claims(user.id, jwt_payload)
        if user.role is Role.SUPER_ADMIN:
            clinic_context_id = jwt_payload.get('platform_clinic_id')
        revoke_current_token()
        tokens = issue_auth_tokens(
            user, clinic_context_id=clinic_context_id,
            tenant_id=tenant_id, auto_assign_clinic=False,
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


# ================================================================
# GAP-B: server-authorized organization and multi-clinic selection.
# Does not replace the legacy super-admin clinic endpoints.
# ================================================================

def _gapb_authenticated_user():
    from app.modules.clinic.services.session_authority import TenantContextDenied
    try:
        identity = int(get_jwt_identity())
    except (TypeError, ValueError):
        raise TenantContextDenied('Invalid authenticated identity')
    user = db.session.get(User, identity)
    if user is None or not user.is_active:
        raise TenantContextDenied('Inactive authenticated identity')
    return user


@auth_bp.get('/tenant-context')
@jwt_required()
def get_tenant_context_route():
    from app.modules.clinic.services.session_authority import context_from_claims
    try:
        user = _gapb_authenticated_user()
        tenant_id, clinic_id = context_from_claims(user.id, get_jwt())
    except DomainError as exc:
        return jsonify({'success': False, 'error': str(exc)}), exc.status_code
    return jsonify({'success': True, 'data': {
        'tenant_id': tenant_id, 'clinic_id': clinic_id,
    }}), 200


@auth_bp.post('/tenant-context')
@jwt_required()
def select_tenant_context_route():
    from app.core.auth.user.services.clinic_context_service import select_tenant_context
    try:
        user = _gapb_authenticated_user()
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'organization_id'}:
            return jsonify({'success': False, 'error': 'Organization ID is required'}), 400
        select_tenant_context(user.id, payload['organization_id'])
        tokens = issue_auth_tokens(
            user, tenant_id=payload['organization_id'], auto_assign_clinic=False, rotate_session=True)
        db.session.commit()
    except DomainError as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), exc.status_code
    return jsonify({'success': True, 'data': tokens}), 200


@auth_bp.post('/tenant-context/clinic')
@jwt_required()
def select_member_clinic_context_route():
    from app.core.auth.user.services.clinic_context_service import select_clinic_context
    from app.modules.clinic.services.session_authority import TenantContextDenied
    try:
        user = _gapb_authenticated_user()
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != {'clinic_id'}:
            return jsonify({'success': False, 'error': 'Clinic ID is required'}), 400
        clinic_id = payload['clinic_id']
        if type(clinic_id) is not int or clinic_id <= 0:
            raise TenantContextDenied('Invalid clinic ID')
        from app.modules.clinic.models.clinic_model import Clinic
        from app.modules.clinic.services.session_authority import context_from_claims
        selected_tenant_id, _ = context_from_claims(user.id, get_jwt())
        target_clinic = db.session.get(Clinic, clinic_id)
        if (selected_tenant_id is None or target_clinic is None or str(target_clinic.organization_id) != selected_tenant_id):
            raise TenantContextDenied('Select this organization before switching clinics')
        select_clinic_context(user.id, clinic_id)
        tokens = issue_auth_tokens(
            user, clinic_context_id=clinic_id, tenant_id=selected_tenant_id,
            auto_assign_clinic=False, rotate_session=True)
        db.session.commit()
    except DomainError as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), exc.status_code
    return jsonify({'success': True, 'data': tokens}), 200


@auth_bp.delete('/tenant-context')
@jwt_required()
def clear_tenant_context_route():
    from app.core.auth.user.services.clinic_context_service import clear_tenant_context
    from app.modules.clinic.services.session_authority import context_from_claims
    try:
        user = _gapb_authenticated_user()
        old_tenant_id, _ = context_from_claims(user.id, get_jwt())
        clear_tenant_context(user.id, previous_tenant_id=old_tenant_id)
        tokens = issue_auth_tokens(user, auto_assign_clinic=False, rotate_session=True)
        db.session.commit()
    except DomainError as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), exc.status_code
    return jsonify({'success': True, 'data': tokens}), 200
