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
) -> dict:
    if not isinstance(user, User):
        raise ValidationError(
            "Invalid user"
        )

    if not user.is_active:
        raise ValidationError(
            "This account has been deactivated"
        )

    if clinic_context_id is not None:
        if user.role is not Role.SUPER_ADMIN:
            raise ValidationError(
                "Only a super administrator can use a clinic context"
            )

        from app.core.auth.user.services.clinic_context_service import (
            _get_active_clinic,
        )

        clinic_context_id = _get_active_clinic(
            clinic_context_id
        ).id

    # Central enforcement: no password, Google, refresh, or clinic-switch
    # caller may mint a Super Admin token without valid MFA evidence.
    from app.core.auth.user.services.super_admin_login_mfa_service import (
        require_token_issuance_authority,
    )
    mfa_binding = require_token_issuance_authority(user)

    access_claims = {
        "role": user.role.value,
        "token_version": user.token_version,
    }

    refresh_claims = {
        "token_version": user.token_version,
    }
    if mfa_binding is not None:
        access_claims["sa_mfa_verified"] = True
        access_claims["sa_mfa_binding"] = mfa_binding
        refresh_claims["role"] = Role.SUPER_ADMIN.value
        refresh_claims["sa_mfa_verified"] = True
        refresh_claims["sa_mfa_binding"] = mfa_binding

    if clinic_context_id is not None:
        access_claims["clinic_context_id"] = clinic_context_id
        refresh_claims["clinic_context_id"] = clinic_context_id

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims=access_claims,
    )

    refresh_token = create_refresh_token(
        identity=str(user.id),
        additional_claims=refresh_claims,
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_id": user.id,
        "role": user.role.value,
        "clinic_context_id": clinic_context_id,
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
