from app.core.exceptions import ValidationError
from functools import wraps

from flask import g, jsonify, request
from sqlalchemy import select

from flask_jwt_extended import (
    get_jwt,
    get_jwt_identity,
    verify_jwt_in_request,
)

from app.core.enums.role_enums import Role
from app.extensions import db


def transactional(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            db.session.commit()
            return result
        except Exception:
            db.session.rollback()
            raise

    return wrapper


def _load_auth_context():
    """
    Verify the JWT and load the authenticated user's identity and role
    into Flask's request-local `g` object.

    This function is shared by the authentication decorators so that
    `login_required` and `role_required` use the same authentication flow.
    """

    if getattr(g, "_auth_context_request", None) is request._get_current_object():
        return

    verify_jwt_in_request()

    identity = get_jwt_identity()
    claims = get_jwt()

    try:
        g.current_user_id = int(identity)
    except (TypeError, ValueError):
        # Invalid JWT identity.
        g.current_user_id = None

    g.current_user_role = claims.get("role")
    g.current_clinic_id = None

    if g.current_user_id is not None:
        from app.core.auth.user.services.clinic_context_service import (
            resolve_effective_clinic_id,
        )

        g.current_clinic_id = resolve_effective_clinic_id(
            user_id=g.current_user_id,
            jwt_payload=claims,
        )

    g._auth_context_request = request._get_current_object()
    g._auth_context_loaded = True


def get_current_clinic_id(*, required: bool = False) -> int | None:
    _load_auth_context()

    clinic_id = getattr(g, "current_clinic_id", None)

    if required and clinic_id is None:
        raise ValidationError(
            "Clinic context is required"
        )

    return clinic_id


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _load_auth_context()

        if g.current_user_id is None:
            return jsonify({"error": "Invalid authentication identity"}), 401

        return fn(*args, **kwargs)

    return wrapper


def role_required(*required_roles):
    allowed_roles = {
        role.value if isinstance(role, Role) else str(role)
        for role in required_roles
    }

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            _load_auth_context()

            if g.current_user_id is None:
                return jsonify({"error": "Invalid authentication identity"}), 401

            if g.current_user_role not in allowed_roles:
                return jsonify({"error": "Insufficient permissions"}), 403

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def emergency_aware_role_required(*required_roles):
    """
    Preserve ordinary route-role enforcement while allowing eligible
    emergency requesters past this role gate when an emergency ID is
    supplied.

    This decorator is not resource authorization. The handler must
    validate the exact grant with assert_emergency_access before
    returning clinical data.
    """
    allowed_roles = {
        role.value if isinstance(role, Role) else str(role)
        for role in required_roles
    }

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            _load_auth_context()

            if g.current_user_id is None:
                return jsonify({
                    "error": "Invalid authentication identity",
                }), 401

            if g.current_user_role in allowed_roles:
                return fn(*args, **kwargs)

            if request.headers.get("X-Emergency-Access-ID") is None:
                return jsonify({
                    "error": "Insufficient permissions",
                }), 403

            # Import locally: emergency_access_service imports this module
            # for transactional, so a module-level import would cycle.
            from app.core.emergency_access.services.emergency_access_service import (
                EMERGENCY_REQUESTER_ROLES,
            )

            emergency_roles = {
                role.value if isinstance(role, Role) else str(role)
                for role in EMERGENCY_REQUESTER_ROLES
            }

            if g.current_user_role not in emergency_roles:
                return jsonify({
                    "error": "Insufficient permissions",
                }), 403

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def active_staff_required(fn):
    """
    Deny clinical-route access to linked staff who are suspended or terminated.

    Users without a linked Staff record retain the route's existing
    role-based authorization behavior. ON_LEAVE is not treated as suspension.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        _load_auth_context()

        if g.current_user_id is None:
            return jsonify({
                "error": "Invalid authentication identity",
            }), 401

        # Keep the import local to avoid model import cycles during app startup.
        from app.core.enums.staff_enums import StaffStatus
        from app.modules.staff.models.staff_model import Staff

        staff = db.session.execute(
            select(Staff).where(
                Staff.user_id == g.current_user_id,
            )
        ).scalar_one_or_none()

        # Some role-bearing accounts intentionally have no Staff row.
        if staff is None:
            return fn(*args, **kwargs)

        clinic_id = getattr(
            g,
            "current_clinic_id",
            None,
        )

        if clinic_id is None or staff.clinic_id != clinic_id:
            return jsonify({
                "error": "Staff access is not available for this clinic",
            }), 403

        if staff.status in (
            StaffStatus.SUSPENDED,
            StaffStatus.TERMINATED,
        ):
            return jsonify({
                "error": "Staff account is suspended or terminated",
            }), 403

        return fn(*args, **kwargs)

    return wrapper


def require_roles(*allowed_roles):

    return role_required(*allowed_roles)