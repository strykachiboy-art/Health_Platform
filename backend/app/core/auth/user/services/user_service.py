from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
)

from sqlalchemy import select

from app.extensions import db
from app.core.utils.decorators import transactional
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.auth.user.models.user_model import User


def _validate_positive_id(
    value,
    field_name: str,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )


def get_user(user_id: int) -> User:
    _validate_positive_id(
        user_id,
        "User ID",
    )

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        raise ValidationError(
            f"User {user_id} not found"
        )

    return user


def get_user_by_email(
    email: str,
) -> User | None:
    if not email:
        return None

    statement = select(User).where(
        User.email == email.lower().strip()
    )

    return db.session.execute(
        statement
    ).scalar_one_or_none()


@transactional
def register_user(
    email: str,
    password: str,
    role: Role,
    clinic_id: int | None = None,
) -> User:
    if not email or "@" not in email:
        raise ValidationError(
            "A valid email is required"
        )

    if not password or len(password) < 8:
        raise ValidationError(
            "Password must be at least 8 characters"
        )

    if not isinstance(role, Role):
        raise ValidationError(
            "Invalid user role"
        )

    email = email.lower().strip()

    if get_user_by_email(email):
        raise ConflictError(
            f"Email '{email}' is already registered"
        )

    user = User(
        email=email,
        role=role,
        clinic_id=clinic_id,
    )

    user.set_password(password)

    db.session.add(user)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="User",
        entity_id=user.id,
        description=(
            f"User registered: "
            f"{email} ({role.value})"
        ),
    )

    return user


def issue_auth_tokens(
    user: User,
    clinic_context_id: int | None = None,
    *,
    tenant_id=None,
    auto_assign_clinic: bool = True,
    rotate_session: bool = False,
) -> dict:
    """Issue tokens only with server-validated tenant and clinic memberships.

    Unscoped login remains valid for newly onboarded patients; it grants no
    organization or clinic authority. Organization admins receive no clinical
    permission simply because of their organization role.
    """
    from app.modules.clinic.services.session_authority import (
        bound_context_for_issuance, validate_platform_clinic,
    )
    if not isinstance(user, User) or not user.is_active:
        raise ValidationError("Invalid or inactive user")
    if rotate_session:
        # Serialize competing context rotations and re-check the source JWT
        # after acquiring the row lock, before auditing/minting a new session.
        from flask_jwt_extended import get_jwt
        from sqlalchemy import select
        from app.extensions import db
        from app.modules.clinic.services.session_authority import TenantContextDenied
        user = db.session.execute(select(User).where(User.id == user.id)
            .with_for_update().execution_options(populate_existing=True)).scalar_one()
        claims = get_jwt()
        if not user.is_active or claims.get('sub') != str(user.id) or claims.get('token_version') != user.token_version:
            raise TenantContextDenied('Session changed before context rotation')
    platform_clinic_id = None
    if user.role is Role.SUPER_ADMIN and clinic_context_id is not None:
        if tenant_id is not None:
            from app.modules.clinic.services.session_authority import TenantContextDenied
            raise TenantContextDenied('Platform session cannot bind clinical membership')
        platform_clinic_id = validate_platform_clinic(user, clinic_context_id)
        clinic_context_id = None
    organization_id, selected_clinic_id = bound_context_for_issuance(
        user, tenant_id=tenant_id, clinic_id=clinic_context_id,
        auto_assign_clinic=auto_assign_clinic,
    )
    # Central enforcement: no password, Google, refresh, or clinic-switch
    # caller may mint a Super Admin token without valid MFA evidence.
    from app.core.auth.user.services.super_admin_login_mfa_service import (
        require_token_issuance_authority,
    )
    mfa_binding = require_token_issuance_authority(user)

    # A verified MFA JWT carries the pre-rotation token_version. Validate
    # both authorities before rotating; only the new pair uses the new version.
    # Callers of context-switch routes commit this change with the new session.
    if rotate_session:
        from app.core.auth.user.services.token_service import revoke_user_tokens
        revoke_user_tokens(user.id)

    access_claims = {
        'role': user.role.value,
        'token_version': user.token_version,
    }
    refresh_claims = {'token_version': user.token_version}
    if organization_id is not None:
        access_claims['tenant_id'] = organization_id
        refresh_claims['tenant_id'] = organization_id
    if selected_clinic_id is not None:
        access_claims['clinic_context_id'] = selected_clinic_id
        refresh_claims['clinic_context_id'] = selected_clinic_id
    if platform_clinic_id is not None:
        access_claims['platform_clinic_id'] = platform_clinic_id
        refresh_claims['platform_clinic_id'] = platform_clinic_id
        refresh_claims['role'] = Role.SUPER_ADMIN.value
    if mfa_binding is not None:
        access_claims["sa_mfa_verified"] = True
        access_claims["sa_mfa_binding"] = mfa_binding
        refresh_claims["role"] = Role.SUPER_ADMIN.value
        refresh_claims["sa_mfa_verified"] = True
        refresh_claims["sa_mfa_binding"] = mfa_binding

    access_token = create_access_token(
        identity=str(user.id), additional_claims=access_claims)
    refresh_token = create_refresh_token(
        identity=str(user.id), additional_claims=refresh_claims)
    return {
        'access_token': access_token, 'refresh_token': refresh_token,
        'user_id': user.id, 'role': user.role.value,
        'tenant_id': organization_id, 'clinic_context_id': platform_clinic_id if platform_clinic_id is not None else selected_clinic_id,
        'context_kind': 'platform' if user.role is Role.SUPER_ADMIN else 'clinical',
    }


def authenticate_user(
    email: str,
    password: str,
) -> dict:
    email = (email or "").lower().strip()

    if not email or not password:
        raise ValidationError(
            "Email and password are required"
        )

    user = get_user_by_email(email)

    if user is None or not user.check_password(password):
        raise ValidationError(
            "Invalid email or password"
        )

    if not user.is_active:
        raise ValidationError(
            "This account has been deactivated"
        )

    from app.core.auth.user.services.super_admin_login_mfa_service import (
        maybe_start_login_challenge,
    )
    challenge = maybe_start_login_challenge(user)
    if challenge is not None:
        return challenge

    tokens = issue_auth_tokens(
        user
    )

    user.last_login_at = db.func.now()

    create_audit_log(
        action=AuditAction.LOGIN,
        entity_type="User",
        entity_id=user.id,
        description=f"User '{email}' logged in",
        user_id=user.id,
    )

    db.session.commit()

    return tokens
