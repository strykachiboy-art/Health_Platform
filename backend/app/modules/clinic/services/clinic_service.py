from __future__ import annotations

import secrets
from datetime import time
from flask import current_app
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinic_enums import (
    ClinicStatus,
    ClinicType,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.extensions import celery, db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.services.clinic_operating_modes import normalize_explicit_operating_mode
from app.modules.clinic.models.organization_model import HospitalOrganization
from uuid import UUID


# =====================================================================
# HELPERS
# =====================================================================


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _audit_value(value):
    value = _enum_value(value)

    if isinstance(value, time):
        return value.isoformat()

    return value


def _validate_positive_id(
    value,
    field_name: str,
) -> int:
    if isinstance(value, bool) or not isinstance(
        value,
        int,
    ):
        raise ValidationError(
            f"{field_name} must be an integer"
        )

    if value <= 0:
        raise ValidationError(
            f"Invalid {field_name}"
        )

    return value


def _validate_optional_positive_id(
    value,
    field_name: str,
):
    if value is None:
        return None

    return _validate_positive_id(
        value,
        field_name,
    )


def _validate_bool(
    value,
    field_name: str,
) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(
            f"{field_name} must be a boolean"
        )

    return value


def _validate_enum(
    value,
    enum_class,
    field_name: str,
):
    if isinstance(value, enum_class):
        return value

    try:
        return enum_class(value)
    except (
        ValueError,
        TypeError,
    ) as exc:
        raise ValidationError(
            f"Invalid {field_name}"
        ) from exc


def _validate_time(
    value,
    field_name: str,
):
    if value is not None and not isinstance(
        value,
        time,
    ):
        raise ValidationError(
            f"{field_name} must be a valid time"
        )

    return value


def _validate_name(
    name: str,
) -> str:
    if not isinstance(name, str):
        raise ValidationError(
            "Clinic name must be a string"
        )

    name = name.strip()

    if not name:
        raise ValidationError(
            "Clinic name is required"
        )

    return name


def _validate_operating_hours(
    opening_time: time | None,
    closing_time: time | None,
):
    opening_time = _validate_time(
        opening_time,
        "Opening time",
    )

    closing_time = _validate_time(
        closing_time,
        "Closing time",
    )

    if (
        opening_time is not None
        and closing_time is not None
        and opening_time >= closing_time
    ):
        raise ValidationError(
            "Opening time must be earlier than closing time"
        )


def _resolve_operating_hours(opening_time, closing_time, operating_mode):
    if operating_mode is None:
        # Preserve the exact legacy behavior, including optional one-sided times.
        _validate_operating_hours(opening_time, closing_time)
        return opening_time, closing_time
    if current_app.config.get("CLINIC_EXTENDED_OPERATING_HOURS_ENABLED", False) is not True:
        raise ValidationError("Extended clinic operating hours are not enabled")
    try:
        return normalize_explicit_operating_mode(opening_time, closing_time, operating_mode)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _validate_timezone(
    timezone: str,
) -> str:
    if not isinstance(timezone, str):
        raise ValidationError(
            "Timezone must be a string"
        )

    timezone = timezone.strip()

    if not timezone:
        raise ValidationError(
            "Timezone is required"
        )

    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(
            f"Invalid timezone '{timezone}'"
        ) from exc

    return timezone


def _get_parent_clinic(
    parent_clinic_id: int,
    *,
    for_update: bool = False,
) -> Clinic:
    parent_clinic_id = _validate_positive_id(
        parent_clinic_id,
        "parent clinic ID",
    )

    parent = db.session.get(
        Clinic,
        parent_clinic_id,
        with_for_update=for_update,
    )

    if parent is None:
        raise NotFoundError(
            f"Parent clinic {parent_clinic_id} not found"
        )

    return parent


def _check_name_conflict(
    name: str,
    parent_clinic_id: int | None,
    exclude_clinic_id: int | None = None,
):
    conditions = [
        Clinic.name == name,
        Clinic.parent_clinic_id == parent_clinic_id,
    ]

    if exclude_clinic_id is not None:
        exclude_clinic_id = _validate_positive_id(
            exclude_clinic_id,
            "clinic ID",
        )

        conditions.append(
            Clinic.id != exclude_clinic_id
        )

    statement = (
        select(Clinic)
        .where(*conditions)
        .limit(1)
    )

    existing = db.session.execute(
        statement
    ).scalar_one_or_none()

    if existing is not None:
        raise ConflictError(
            f"A clinic named '{name}' already exists under "
            "this parent clinic"
        )


def _ensure_active_clinic(
    clinic: Clinic,
):
    if clinic.status != ClinicStatus.ACTIVE:
        raise ValidationError(
            f"Clinic {clinic.id} is not active"
        )


def _ensure_no_hierarchy_cycle(
    clinic: Clinic,
    proposed_parent: Clinic,
):
    current = proposed_parent
    visited = set()

    while current is not None:
        if current.id in visited:
            raise ConflictError(
                "Existing clinic hierarchy contains a cycle"
            )

        visited.add(current.id)

        if current.id == clinic.id:
            raise ConflictError(
                "This parent assignment would create "
                "a circular clinic hierarchy"
            )

        current = current.parent_clinic


# =====================================================================
# GET CLINIC
# =====================================================================


def get_clinic(
    clinic_id: int,
    *,
    for_update: bool = False,
) -> Clinic:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic ID",
    )

    clinic = db.session.get(
        Clinic,
        clinic_id,
        with_for_update=for_update,
    )

    if clinic is None:
        raise NotFoundError(
            f"Clinic {clinic_id} not found"
        )

    return clinic


# =====================================================================
# LIST CLINICS
# =====================================================================


def list_clinics(
    status: ClinicStatus | None = None,
) -> list[Clinic]:
    if status is not None:
        status = _validate_enum(
            status,
            ClinicStatus,
            "clinic status",
        )

    statement = select(Clinic)

    if status is not None:
        statement = statement.where(
            Clinic.status == status
        )

    statement = statement.order_by(
        Clinic.name.asc(),
        Clinic.id.asc(),
    )

    return list(
        db.session.execute(
            statement
        ).scalars()
    )


# =====================================================================
# LIST BRANCHES
# =====================================================================


def list_branches(
    clinic_id: int,
) -> list[Clinic]:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic ID",
    )

    get_clinic(clinic_id)

    statement = (
        select(Clinic)
        .where(
            Clinic.parent_clinic_id == clinic_id
        )
        .order_by(
            Clinic.name.asc(),
            Clinic.id.asc(),
        )
    )

    return list(
        db.session.execute(
            statement
        ).scalars()
    )


# =====================================================================
# CREATE CLINIC
# =====================================================================


@transactional
def create_clinic(
    name: str,
    clinic_type: ClinicType = ClinicType.GENERAL,
    parent_clinic_id: int | None = None,
    is_headquarters: bool = False,
    address: str | None = None,
    city: str | None = None,
    country: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    timezone: str = "UTC",
    opening_time=None,
    closing_time=None,
    operating_mode=None,
    organization_id=None,
    platform_provisioner_id=None,
) -> Clinic:
    name = _validate_name(name)

    clinic_type = _validate_enum(
        clinic_type,
        ClinicType,
        "clinic type",
    )

    parent_clinic_id = (
        _validate_optional_positive_id(
            parent_clinic_id,
            "parent clinic ID",
        )
    )

    is_headquarters = _validate_bool(
        is_headquarters,
        "is_headquarters",
    )

    timezone = _validate_timezone(
        timezone
    )

    opening_time, closing_time = _resolve_operating_hours(
        opening_time, closing_time, operating_mode,
    )

    if (
        is_headquarters
        and parent_clinic_id is not None
    ):
        raise ValidationError(
            "A headquarters clinic cannot have a parent clinic"
        )

    parent = None

    if parent_clinic_id is not None:
        parent = _get_parent_clinic(
            parent_clinic_id,
            for_update=True,
        )

        _ensure_active_clinic(parent)

    # Ownership must come from an existing parent or an explicitly validated
    # server-side provisioning contract, never an untrusted client claim.
    if parent is not None:
        owner_id = parent.organization_id
    else:
        if organization_id is None and platform_provisioner_id is not None:
            from app.core.auth.user.models.user_model import User
            from app.core.enums.role_enums import Role
            actor = db.session.get(User, platform_provisioner_id)
            if actor is None or not actor.is_active or actor.role is not Role.SUPER_ADMIN:
                raise ValidationError('GAPA_ROOT_PLATFORM_PROVISIONER_REQUIRED')
            owner = HospitalOrganization(name=name)
            db.session.add(owner)
            db.session.flush()
            owner_id = owner.id
        else:
            if not isinstance(organization_id, UUID):
                raise ValidationError('GAPA_ROOT_ORGANIZATION_REQUIRED')
            owner_id = organization_id
            if db.session.get(HospitalOrganization, owner_id) is None:
                raise ValidationError('GAPA_UNKNOWN_ORGANIZATION')
    if owner_id is None:
        raise ValidationError('GAPA_OWNER_NOT_RESOLVED')

    _check_name_conflict(
        name=name,
        parent_clinic_id=parent_clinic_id,
    )

    clinic = Clinic(
        organization_id=owner_id,
        name=name,
        clinic_type=clinic_type,
        status=ClinicStatus.ACTIVE,
        parent_clinic_id=(
            parent.id
            if parent is not None
            else None
        ),
        is_headquarters=is_headquarters,
        address=address,
        city=city,
        country=country,
        phone=phone,
        email=email,
        timezone=timezone,
        opening_time=opening_time,
        closing_time=closing_time,
        ai_credits=0,
        ai_requests_this_month=0,
    )

    db.session.add(clinic)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Clinic",
        entity_id=clinic.id,
        description=(
            f"Clinic '{clinic.name}' created"
        ),
        new_value={
            "name": clinic.name,
            "clinic_type": _enum_value(
                clinic.clinic_type
            ),
            "status": _enum_value(
                clinic.status
            ),
            "parent_clinic_id": (
                clinic.parent_clinic_id
            ),
            "is_headquarters": (
                clinic.is_headquarters
            ),
            "organization_id": str(clinic.organization_id),
            "opening_time": _audit_value(clinic.opening_time),
            "closing_time": _audit_value(clinic.closing_time),
            "timezone": clinic.timezone,
        },
    )

    return clinic


# =====================================================================
# CREATE BRANCH
# =====================================================================


@transactional
def create_branch(
    parent_clinic_id: int,
    name: str,
    clinic_type: ClinicType = ClinicType.GENERAL,
    address: str | None = None,
    city: str | None = None,
    country: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    timezone: str = "UTC",
    opening_time=None,
    closing_time=None,
    operating_mode=None,
) -> Clinic:
    parent_clinic_id = _validate_positive_id(
        parent_clinic_id,
        "parent clinic ID",
    )

    parent = _get_parent_clinic(
        parent_clinic_id,
        for_update=True,
    )

    _ensure_active_clinic(parent)

    name = _validate_name(name)

    clinic_type = _validate_enum(
        clinic_type,
        ClinicType,
        "clinic type",
    )

    timezone = _validate_timezone(
        timezone
    )

    opening_time, closing_time = _resolve_operating_hours(
        opening_time, closing_time, operating_mode,
    )

    _check_name_conflict(
        name=name,
        parent_clinic_id=parent.id,
    )

    if parent.organization_id is None:
        raise ValidationError('GAPA_PARENT_OWNER_REQUIRED')

    branch = Clinic(
        organization_id=parent.organization_id,
        name=name,
        clinic_type=clinic_type,
        status=ClinicStatus.ACTIVE,
        parent_clinic_id=parent.id,
        is_headquarters=False,
        address=address,
        city=city,
        country=country,
        phone=phone,
        email=email,
        timezone=timezone,
        opening_time=opening_time,
        closing_time=closing_time,
        ai_credits=0,
        ai_requests_this_month=0,
    )

    db.session.add(branch)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Clinic",
        entity_id=branch.id,
        description=(
            f"Branch '{branch.name}' created under "
            f"clinic {parent.id}"
        ),
        new_value={
            "name": branch.name,
            "clinic_type": _enum_value(
                branch.clinic_type
            ),
            "parent_clinic_id": (
                branch.parent_clinic_id
            ),
            "is_headquarters": False,
            "opening_time": _audit_value(branch.opening_time),
            "closing_time": _audit_value(branch.closing_time),
            "timezone": branch.timezone,
        },
    )

    return branch


# =====================================================================
# UPDATE CLINIC
# =====================================================================


@transactional
def update_clinic(
    clinic_id: int,
    **fields,
) -> Clinic:
    clinic = get_clinic(
        clinic_id,
        for_update=True,
    )

    _ensure_active_clinic(clinic)

    allowed_fields = {
        "name",
        "clinic_type",
        "address",
        "city",
        "country",
        "phone",
        "email",
        "timezone",
        "opening_time",
        "closing_time",
        "operating_mode",
    }

    unknown_fields = set(fields) - allowed_fields

    if unknown_fields:
        raise ValidationError(
            "Unsupported clinic fields: "
            + ", ".join(
                sorted(unknown_fields)
            )
        )

    if not fields:
        return clinic

    # Mode changes are normalized to the same existing two time columns.
    operating_mode = fields.pop("operating_mode", None)
    if operating_mode is not None:
        opening, closing = _resolve_operating_hours(
            fields.get("opening_time"),
            fields.get("closing_time"),
            operating_mode,
        )
        fields["opening_time"] = opening
        fields["closing_time"] = closing

    if "name" in fields:
        fields["name"] = _validate_name(
            fields["name"]
        )

    if "clinic_type" in fields:
        fields["clinic_type"] = _validate_enum(
            fields["clinic_type"],
            ClinicType,
            "clinic type",
        )

    if "timezone" in fields:
        fields["timezone"] = _validate_timezone(
            fields["timezone"]
        )

    if "opening_time" in fields:
        fields["opening_time"] = _validate_time(
            fields["opening_time"],
            "Opening time",
        )

    if "closing_time" in fields:
        fields["closing_time"] = _validate_time(
            fields["closing_time"],
            "Closing time",
        )

    opening_time = fields.get(
        "opening_time",
        clinic.opening_time,
    )

    closing_time = fields.get(
        "closing_time",
        clinic.closing_time,
    )

    if operating_mode is None and (
        "opening_time" in fields or "closing_time" in fields
    ):
        _validate_operating_hours(opening_time, closing_time)

    if (
        "name" in fields
        and fields["name"] != clinic.name
    ):
        _check_name_conflict(
            name=fields["name"],
            parent_clinic_id=(
                clinic.parent_clinic_id
            ),
            exclude_clinic_id=clinic.id,
        )

    old_value = {}
    new_value = {}

    for key, new_value_raw in fields.items():
        current_value = getattr(
            clinic,
            key,
        )

        if current_value == new_value_raw:
            continue

        old_value[key] = _audit_value(
            current_value
        )

        new_value[key] = _audit_value(
            new_value_raw
        )

        setattr(
            clinic,
            key,
            new_value_raw,
        )

    if new_value:
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="Clinic",
            entity_id=clinic.id,
            description=(
                f"Clinic '{clinic.name}' profile updated"
            ),
            old_value=old_value,
            new_value=new_value,
        )

    return clinic


# =====================================================================
# UPDATE BRANCH CONFIGURATION
# =====================================================================


@transactional
def update_branch_configuration(
    clinic_id: int,
    **fields,
) -> Clinic:
    clinic = get_clinic(
        clinic_id,
        for_update=True,
    )

    _ensure_active_clinic(clinic)

    allowed_fields = {
        "parent_clinic_id",
        "is_headquarters",
    }

    unknown_fields = set(fields) - allowed_fields

    if unknown_fields:
        raise ValidationError(
            "Unsupported branch configuration fields: "
            + ", ".join(
                sorted(unknown_fields)
            )
        )

    if not fields:
        return clinic

    if "is_headquarters" in fields:
        fields["is_headquarters"] = _validate_bool(
            fields["is_headquarters"],
            "is_headquarters",
        )

    if "parent_clinic_id" in fields:
        fields["parent_clinic_id"] = (
            _validate_optional_positive_id(
                fields["parent_clinic_id"],
                "parent clinic ID",
            )
        )

    old_parent_id = clinic.parent_clinic_id
    old_is_headquarters = clinic.is_headquarters

    new_parent_id = fields.get(
        "parent_clinic_id",
        clinic.parent_clinic_id,
    )

    new_is_headquarters = fields.get(
        "is_headquarters",
        clinic.is_headquarters,
    )

    if new_parent_id == clinic.id:
        raise ValidationError(
            "A clinic cannot be its own parent"
        )

    new_parent = None

    if new_parent_id is not None:
        new_parent = _get_parent_clinic(
            new_parent_id,
            for_update=True,
        )

        _ensure_active_clinic(new_parent)

        _ensure_no_hierarchy_cycle(
            clinic,
            new_parent,
        )
        if clinic.organization_id is None or new_parent.organization_id != clinic.organization_id:
            raise ValidationError('GAPA_CROSS_ORGANIZATION_REPARENT_DENIED')

        _check_name_conflict(
            name=clinic.name,
            parent_clinic_id=new_parent.id,
            exclude_clinic_id=clinic.id,
        )

    if (
        new_is_headquarters
        and new_parent_id is not None
    ):
        raise ValidationError(
            "A headquarters clinic cannot have a parent clinic"
        )

    old_value = {
        "parent_clinic_id": old_parent_id,
        "is_headquarters": old_is_headquarters,
    }

    clinic.parent_clinic_id = new_parent_id
    clinic.is_headquarters = new_is_headquarters

    new_value = {
        "parent_clinic_id": (
            clinic.parent_clinic_id
        ),
        "is_headquarters": (
            clinic.is_headquarters
        ),
    }

    if old_value != new_value:
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="Clinic",
            entity_id=clinic.id,
            description=(
                f"Clinic '{clinic.name}' branch "
                "configuration updated"
            ),
            old_value=old_value,
            new_value=new_value,
        )

    return clinic


# =====================================================================
# CHANGE STATUS
# =====================================================================


@transactional
def change_status(
    clinic_id: int,
    new_status: ClinicStatus,
) -> Clinic:
    clinic = get_clinic(
        clinic_id,
        for_update=True,
    )

    new_status = _validate_enum(
        new_status,
        ClinicStatus,
        "clinic status",
    )

    if clinic.status == new_status:
        return clinic

    old_status = clinic.status

    clinic.status = new_status

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Clinic",
        entity_id=clinic.id,
        description=(
            f"Clinic '{clinic.name}' status changed "
            f"to '{new_status.value}'"
        ),
        old_value={
            "status": old_status.value,
        },
        new_value={
            "status": new_status.value,
        },
    )

    return clinic


# =====================================================================
# ADD AI CREDITS
# =====================================================================


@transactional
def add_ai_credits(
    clinic_id: int,
    amount: int,
) -> Clinic:
    if isinstance(amount, bool) or not isinstance(
        amount,
        int,
    ):
        raise ValidationError(
            "AI credit amount must be an integer"
        )

    if amount <= 0:
        raise ValidationError(
            "AI credit amount must be greater than zero"
        )

    clinic = get_clinic(
        clinic_id,
        for_update=True,
    )

    old_credits = clinic.ai_credits

    clinic.ai_credits += amount

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="Clinic",
        entity_id=clinic.id,
        description=(
            f"{amount} AI credits added to clinic "
            f"'{clinic.name}'"
        ),
        old_value={
            "ai_credits": old_credits,
        },
        new_value={
            "ai_credits": clinic.ai_credits,
        },
    )

    return clinic


# =====================================================================
# REGENERATE API TOKEN
# =====================================================================


@transactional
def regenerate_api_token(
    clinic_id: int,
) -> str:
    clinic = get_clinic(
        clinic_id,
        for_update=True,
    )

    old_token_exists = (
        clinic.api_token is not None
    )

    new_token = secrets.token_urlsafe(48)

    clinic.api_token = new_token

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="Clinic",
        entity_id=clinic.id,
        description=(
            f"API token regenerated for clinic "
            f"'{clinic.name}'"
        ),
        old_value={
            "api_token": (
                "present"
                if old_token_exists
                else None
            ),
        },
        new_value={
            "api_token": "present",
        },
    )

    return new_token


# =====================================================================
# CONSUME AI CREDIT
# =====================================================================


def _consume_ai_credit(
    clinic_id: int,
) -> Clinic:
    clinic = get_clinic(
        clinic_id,
        for_update=True,
    )

    _ensure_active_clinic(clinic)

    if clinic.ai_credits <= 0:
        raise ValidationError(
            "Insufficient AI credits"
        )

    old_credits = clinic.ai_credits
    old_requests = (
        clinic.ai_requests_this_month
    )

    clinic.ai_credits -= 1
    clinic.ai_requests_this_month += 1

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="Clinic",
        entity_id=clinic.id,
        description=(
            f"AI credit consumed by clinic "
            f"'{clinic.name}'"
        ),
        old_value={
            "ai_credits": old_credits,
            "ai_requests_this_month": (
                old_requests
            ),
        },
        new_value={
            "ai_credits": clinic.ai_credits,
            "ai_requests_this_month": (
                clinic.ai_requests_this_month
            ),
        },
    )

    return clinic


@transactional
def consume_ai_credit(
    clinic_id: int,
) -> Clinic:
    return _consume_ai_credit(
        clinic_id
    )
    


# =====================================================================
# MONTHLY AI USAGE RESET
# =====================================================================


@celery.task(
    name="reset_monthly_ai_usage"
)
def reset_monthly_ai_usage() -> int:
    try:
        result = db.session.execute(
            update(Clinic)
            .where(
                Clinic.ai_requests_this_month != 0
            )
            .values(
                ai_requests_this_month=0
            )
        )

        updated_count = int(
            result.rowcount or 0
        )

        db.session.commit()

        return updated_count

    except Exception:
        db.session.rollback()
        raise


# =====================================================================
# ENSURE CLINIC ACTIVE
# =====================================================================


def ensure_clinic_active(
    clinic_id: int,
) -> Clinic:
    clinic = get_clinic(
        clinic_id
    )

    _ensure_active_clinic(
        clinic
    )

    return clinic