from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.core.auth.user.models.user_model import User
from app.core.audit.services.audit_service import create_audit_log
from app.core.emergency_access.models.emergency_access_model import (
    EmergencyAccessGrant,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.emergency_access_enums import (
    EmergencyAccessStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.lab.models.lab_model import LabOrder
from app.modules.pharmacy.models.pharmacy_model import DispenseRecord
from app.modules.patient.models.patient_model import Patient
from app.modules.prescription.models.prescription_model import Prescription
from app.modules.staff.models.staff_model import Staff


MAX_REASON_LENGTH = 500
MAX_PURPOSE_LENGTH = 255
MAX_SCOPE_ITEMS = 50
MAX_SCOPE_ITEM_LENGTH = 150
MAX_DURATION_MINUTES = 60

EMERGENCY_REQUESTER_ROLES = frozenset(
    {
        Role.DOCTOR,
        Role.NURSE,
        Role.PHARMACIST,
        Role.LAB_TECHNICIAN,
        Role.PARAMEDIC,
        Role.EMT,
    }
)

EMERGENCY_REVIEWER_ROLES = frozenset(
    {
        *EMERGENCY_REQUESTER_ROLES,
        Role.ADMIN,
        Role.SUPER_ADMIN,
    }
)

_SCOPE_TOKEN_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9_-]{0,79}$"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc_aware(
    value: datetime | None,
) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc,
        )

    return value.astimezone(
        timezone.utc,
    )


def _validate_positive_id(
    value,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _normalize_required_string(
    value,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        raise ValidationError(
            f"{field_name} is required"
        )

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _normalize_optional_string(
    value,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        return None

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _normalize_scope(
    scope,
) -> list[str]:
    if not isinstance(scope, list):
        raise ValidationError(
            "Scope must be a list"
        )

    if not scope:
        raise ValidationError(
            "At least one emergency access scope is required"
        )

    if len(scope) > MAX_SCOPE_ITEMS:
        raise ValidationError(
            f"Scope cannot contain more than "
            f"{MAX_SCOPE_ITEMS} items"
        )

    normalized: list[str] = []

    for item in scope:
        if not isinstance(item, str):
            raise ValidationError(
                "Every scope item must be a string"
            )

        token = item.strip().lower()

        if not token:
            raise ValidationError(
                "Scope items cannot be empty"
            )

        if len(token) > MAX_SCOPE_ITEM_LENGTH:
            raise ValidationError(
                f"Scope items cannot exceed "
                f"{MAX_SCOPE_ITEM_LENGTH} characters"
            )

        parts = token.split(":")

        if len(parts) not in (2, 3):
            raise ValidationError(
                "Each scope item must use "
                "'resource:action' or "
                "'resource:resource_id:action'"
            )

        resource_type = parts[0]

        if resource_type == "*":
            raise ValidationError(
                "Wildcard resource types are not allowed"
            )

        if not _SCOPE_TOKEN_PATTERN.fullmatch(
            resource_type
        ):
            raise ValidationError(
                f"Invalid resource type in scope '{token}'"
            )

        if len(parts) == 2:
            action = parts[1]

            if not action:
                raise ValidationError(
                    f"Invalid action in scope '{token}'"
                )

            if action != "*" and not _SCOPE_TOKEN_PATTERN.fullmatch(
                action
            ):
                raise ValidationError(
                    f"Invalid action in scope '{token}'"
                )

        else:
            resource_id = parts[1]
            action = parts[2]

            try:
                parsed_resource_id = int(
                    resource_id
                )
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    f"Invalid resource ID in scope '{token}'"
                ) from exc

            _validate_positive_id(
                parsed_resource_id,
                "Scope resource ID",
            )

            if action != "*" and not _SCOPE_TOKEN_PATTERN.fullmatch(
                action
            ):
                raise ValidationError(
                    f"Invalid action in scope '{token}'"
                )

        if token in {"*", "*:*"}:
            raise ValidationError(
                "Unrestricted emergency access scope is not allowed"
            )

        if token not in normalized:
            normalized.append(token)

    return normalized


def _normalize_role(
    role,
) -> Role:
    if isinstance(role, Role):
        return role

    try:
        return Role(role)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Invalid user role"
        ) from exc


def _get_user(
    user_id: int,
) -> User:
    user_id = _validate_positive_id(
        user_id,
        "User ID",
    )

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        raise NotFoundError(
            f"User {user_id} not found"
        )

    if not user.is_active:
        raise ValidationError(
            f"User {user.id} is inactive"
        )

    return user


def _get_patient(
    patient_id: int,
) -> Patient:
    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    patient = db.session.get(
        Patient,
        patient_id,
    )

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    return patient


def _get_staff_for_user(
    user: User,
) -> Staff:
    staff = db.session.execute(
        db.select(Staff)
        .where(
            Staff.user_id == user.id,
        )
        .limit(1)
    ).scalar_one_or_none()

    if staff is None:
        raise ValidationError(
            "Authenticated user is not linked "
            "to a staff record"
        )

    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError(
            "Staff record is not active"
        )

    return staff


def _get_user_clinic_id(
    user: User,
) -> int | None:
    role = _normalize_role(
        user.role
    )

    if role == Role.SUPER_ADMIN:
        return user.clinic_id

    staff = None

    if user.staff is not None:
        staff = user.staff
    else:
        staff = db.session.execute(
            db.select(Staff)
            .where(
                Staff.user_id == user.id,
            )
            .limit(1)
        ).scalar_one_or_none()

    if staff is not None:
        if (
            user.clinic_id is not None
            and user.clinic_id != staff.clinic_id
        ):
            raise ConflictError(
                "User clinic does not match staff clinic"
            )

        return staff.clinic_id

    return user.clinic_id


def _ensure_active_clinic(
    clinic_id: int,
):
    from app.modules.clinic.models.clinic_model import (
        Clinic,
    )

    clinic = db.session.get(
        Clinic,
        clinic_id,
    )

    if clinic is None:
        raise NotFoundError(
            f"Clinic {clinic_id} not found"
        )

    if clinic.status != ClinicStatus.ACTIVE:
        raise ValidationError(
            f"Clinic {clinic.id} is not active"
        )

    return clinic


def _validate_requester(
    actor_id: int,
):
    actor = _get_user(
        actor_id,
    )

    role = _normalize_role(
        actor.role
    )

    if role not in EMERGENCY_REQUESTER_ROLES:
        raise ValidationError(
            "User role is not eligible "
            "for emergency clinical access"
        )

    staff = _get_staff_for_user(
        actor,
    )

    clinic_id = _get_user_clinic_id(
        actor,
    )

    if clinic_id is None:
        raise ValidationError(
            "Emergency access requires a clinic-scoped user"
        )

    if staff.clinic_id != clinic_id:
        raise ConflictError(
            "Staff clinic does not match "
            "authenticated user clinic"
        )

    _ensure_active_clinic(
        clinic_id,
    )

    return actor, staff, clinic_id, role


def _validate_reviewer(
    reviewer_id: int,
    clinic_id: int,
    requester_id: int,
) -> User:
    reviewer = _get_user(
        reviewer_id,
    )

    if reviewer.id == requester_id:
        raise ValidationError(
            "Emergency access requester cannot "
            "approve or deny their own request"
        )

    role = _normalize_role(
        reviewer.role
    )

    if role not in EMERGENCY_REVIEWER_ROLES:
        raise ValidationError(
            "User role is not eligible "
            "to review emergency access"
        )

    if role == Role.SUPER_ADMIN:
        return reviewer

    reviewer_clinic_id = _get_user_clinic_id(
        reviewer,
    )

    if reviewer_clinic_id != clinic_id:
        raise NotFoundError(
            "Emergency access request not found"
        )

    if role not in {
        Role.ADMIN,
        Role.DOCTOR,
        Role.NURSE,
        Role.PHARMACIST,
        Role.LAB_TECHNICIAN,
        Role.PARAMEDIC,
        Role.EMT,
    }:
        raise ValidationError(
            "Reviewer is not authorized "
            "for emergency access decisions"
        )

    if role != Role.ADMIN:
        _get_staff_for_user(
            reviewer,
        )

    return reviewer


def _ensure_patient_in_clinic(
    patient: Patient,
    clinic_id: int,
):
    if patient.clinic_id != clinic_id:
        raise NotFoundError(
            f"Patient {patient.id} not found"
        )


def _get_grant(
    emergency_access_id: int,
    *,
    lock: bool = False,
) -> EmergencyAccessGrant:
    emergency_access_id = _validate_positive_id(
        emergency_access_id,
        "Emergency access ID",
    )

    statement = (
        db.select(EmergencyAccessGrant)
        .where(
            EmergencyAccessGrant.id
            == emergency_access_id
        )
    )

    if lock:
        statement = statement.with_for_update()

    grant = db.session.execute(
        statement
    ).scalar_one_or_none()

    if grant is None:
        raise NotFoundError(
            f"Emergency access request "
            f"{emergency_access_id} not found"
        )

    return grant


def _scope_matches(
    scope: list[str],
    resource_type: str,
    resource_id: int,
    action: str,
) -> bool:
    resource_type = resource_type.strip().lower()
    action = action.strip().lower()

    exact = (
        f"{resource_type}:{resource_id}:{action}"
    )

    exact_resource_wildcard_action = (
        f"{resource_type}:{resource_id}:*"
    )

    type_action = (
        f"{resource_type}:{action}"
    )

    type_wildcard_action = (
        f"{resource_type}:*"
    )

    return any(
        token in {
            exact,
            exact_resource_wildcard_action,
            type_action,
            type_wildcard_action,
        }
        for token in scope
    )


def _audit_status_change(
    grant: EmergencyAccessGrant,
    *,
    user_id: int | None,
    description: str,
    old_status: EmergencyAccessStatus,
    new_status: EmergencyAccessStatus,
    extra: dict | None = None,
):
    payload = {
        "old_status": old_status.value,
        "new_status": new_status.value,
        "patient_id": grant.patient_id,
        "clinic_id": grant.clinic_id,
        "requester_user_id": (
            grant.requester_user_id
        ),
    }

    if extra:
        payload.update(extra)

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="EmergencyAccessGrant",
        entity_id=grant.id,
        description=description,
        old_value={
            "status": old_status.value,
        },
        new_value=payload,
        user_id=user_id,
    )


@transactional
def request_emergency_access(
    *,
    actor_id: int,
    patient_id: int,
    reason: str,
    purpose: str,
    scope: list[str],
) -> EmergencyAccessGrant:
    actor, _, clinic_id, role = _validate_requester(
        actor_id,
    )

    patient = _get_patient(
        patient_id,
    )

    _ensure_patient_in_clinic(
        patient,
        clinic_id,
    )

    reason = _normalize_required_string(
        reason,
        "Emergency access reason",
        MAX_REASON_LENGTH,
    )

    purpose = _normalize_required_string(
        purpose,
        "Emergency access purpose",
        MAX_PURPOSE_LENGTH,
    )

    scope = _normalize_scope(
        scope,
    )

    existing = db.session.execute(
        db.select(EmergencyAccessGrant)
        .where(
            EmergencyAccessGrant.patient_id
            == patient.id,
            EmergencyAccessGrant.requester_user_id
            == actor.id,
            EmergencyAccessGrant.status.in_(
                [
                    EmergencyAccessStatus.REQUESTED,
                    EmergencyAccessStatus.ACTIVE,
                ]
            ),
        )
        .order_by(
            EmergencyAccessGrant.created_at.desc(),
            EmergencyAccessGrant.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()

    now = _utcnow()

    if existing is not None:
        existing_expires_at = _as_utc_aware(
            existing.expires_at,
        )

        if (
            existing.status
            == EmergencyAccessStatus.ACTIVE
            and existing_expires_at is not None
            and existing_expires_at <= now
        ):
            existing.status = (
                EmergencyAccessStatus.EXPIRED
            )

            _audit_status_change(
                existing,
                user_id=None,
                description=(
                    "Emergency access automatically "
                    "expired during request validation"
                ),
                old_status=(
                    EmergencyAccessStatus.ACTIVE
                ),
                new_status=(
                    EmergencyAccessStatus.EXPIRED
                ),
            )

        else:
            raise ConflictError(
                "An existing emergency access "
                "request is already active"
            )

    grant = EmergencyAccessGrant(
        clinic_id=clinic_id,
        patient_id=patient.id,
        requester_user_id=actor.id,
        requester_role=role.value,
        reason=reason,
        purpose=purpose,
        scope=scope,
        status=EmergencyAccessStatus.REQUESTED,
    )

    db.session.add(grant)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="EmergencyAccessGrant",
        entity_id=grant.id,
        description=(
            "Emergency access request created"
        ),
        new_value={
            "clinic_id": clinic_id,
            "patient_id": patient.id,
            "requester_user_id": actor.id,
            "requester_role": role.value,
            "reason": reason,
            "purpose": purpose,
            "scope": scope,
            "status": (
                EmergencyAccessStatus.REQUESTED.value
            ),
        },
        user_id=actor.id,
    )

    return grant


@transactional
def grant_emergency_access(
    *,
    emergency_access_id: int,
    reviewer_id: int,
    duration_minutes: int,
    review_notes: str | None = None,
) -> EmergencyAccessGrant:
    grant = _get_grant(
        emergency_access_id,
        lock=True,
    )

    reviewer = _validate_reviewer(
        reviewer_id,
        grant.clinic_id,
        grant.requester_user_id,
    )

    if (
        isinstance(duration_minutes, bool)
        or not isinstance(duration_minutes, int)
    ):
        raise ValidationError(
            "Emergency access duration "
            "must be an integer"
        )

    if duration_minutes <= 0:
        raise ValidationError(
            "Emergency access duration "
            "must be greater than zero"
        )

    if duration_minutes > MAX_DURATION_MINUTES:
        raise ValidationError(
            f"Emergency access duration "
            f"cannot exceed {MAX_DURATION_MINUTES} minutes"
        )

    review_notes = _normalize_optional_string(
        review_notes,
        "Review notes",
        1000,
    )

    if grant.status != EmergencyAccessStatus.REQUESTED:
        raise ConflictError(
            f"Emergency access request "
            f"{grant.id} cannot be granted from "
            f"status '{grant.status.value}'"
        )

    _ensure_active_clinic(
        grant.clinic_id,
    )

    now = _utcnow()
    expires_at = (
        now
        + timedelta(minutes=duration_minutes)
    )

    old_status = grant.status

    grant.status = EmergencyAccessStatus.ACTIVE
    grant.granted_at = now
    grant.expires_at = expires_at
    grant.reviewed_at = now
    grant.reviewed_by_user_id = reviewer.id
    grant.review_notes = review_notes

    _audit_status_change(
        grant,
        user_id=reviewer.id,
        description=(
            "Emergency access request granted"
        ),
        old_status=old_status,
        new_status=EmergencyAccessStatus.ACTIVE,
        extra={
            "reviewer_user_id": reviewer.id,
            "duration_minutes": duration_minutes,
            "expires_at": expires_at.isoformat(),
            "review_notes": review_notes,
        },
    )

    return grant


@transactional
def deny_emergency_access(
    *,
    emergency_access_id: int,
    reviewer_id: int,
    review_notes: str | None = None,
) -> EmergencyAccessGrant:
    grant = _get_grant(
        emergency_access_id,
        lock=True,
    )

    reviewer = _validate_reviewer(
        reviewer_id,
        grant.clinic_id,
        grant.requester_user_id,
    )

    review_notes = _normalize_required_string(
        review_notes,
        "Review notes",
        1000,
    )

    if grant.status != EmergencyAccessStatus.REQUESTED:
        raise ConflictError(
            f"Emergency access request "
            f"{grant.id} cannot be denied from "
            f"status '{grant.status.value}'"
        )

    now = _utcnow()
    old_status = grant.status

    grant.status = EmergencyAccessStatus.DENIED
    grant.reviewed_at = now
    grant.reviewed_by_user_id = reviewer.id
    grant.review_notes = review_notes

    _audit_status_change(
        grant,
        user_id=reviewer.id,
        description=(
            "Emergency access request denied"
        ),
        old_status=old_status,
        new_status=EmergencyAccessStatus.DENIED,
        extra={
            "reviewer_user_id": reviewer.id,
            "review_notes": review_notes,
        },
    )

    return grant


@transactional
def revoke_emergency_access(
    *,
    emergency_access_id: int,
    actor_id: int,
    reason: str,
) -> EmergencyAccessGrant:
    grant = _get_grant(
        emergency_access_id,
        lock=True,
    )

    actor = _get_user(
        actor_id,
    )

    reason = _normalize_required_string(
        reason,
        "Revoke reason",
        MAX_REASON_LENGTH,
    )

    if grant.status == EmergencyAccessStatus.EXPIRED:
        return grant

    if grant.status != EmergencyAccessStatus.ACTIVE:
        raise ConflictError(
            f"Emergency access request "
            f"{grant.id} cannot be revoked from "
            f"status '{grant.status.value}'"
        )

    now = _utcnow()

    grant_expires_at = _as_utc_aware(
        grant.expires_at,
    )

    if (
        grant_expires_at is not None
        and grant_expires_at <= now
    ):
        old_status = grant.status

        grant.status = EmergencyAccessStatus.EXPIRED

        _audit_status_change(
            grant,
            user_id=None,
            description=(
                "Emergency access automatically "
                "expired before revoke"
            ),
            old_status=old_status,
            new_status=EmergencyAccessStatus.EXPIRED,
        )

        return grant

    actor_role = _normalize_role(
        actor.role
    )

    authorized = (
        actor.id == grant.requester_user_id
        or actor_role == Role.SUPER_ADMIN
    )

    if not authorized:
        reviewer_clinic_id = _get_user_clinic_id(
            actor,
        )

        if (
            reviewer_clinic_id == grant.clinic_id
            and actor_role in EMERGENCY_REVIEWER_ROLES
        ):
            authorized = True

    if not authorized:
        raise NotFoundError(
            "Emergency access request not found"
        )

    old_status = grant.status

    grant.status = EmergencyAccessStatus.REVOKED
    grant.revoked_at = now

    _audit_status_change(
        grant,
        user_id=actor.id,
        description=(
            "Emergency access revoked"
        ),
        old_status=old_status,
        new_status=EmergencyAccessStatus.REVOKED,
        extra={
            "actor_user_id": actor.id,
            "reason": reason,
        },
    )

    return grant


@transactional
def expire_emergency_access(
    *,
    emergency_access_id: int,
) -> EmergencyAccessGrant:
    grant = _get_grant(
        emergency_access_id,
        lock=True,
    )

    if grant.status == EmergencyAccessStatus.EXPIRED:
        return grant

    if grant.status != EmergencyAccessStatus.ACTIVE:
        raise ConflictError(
            f"Emergency access request "
            f"{grant.id} cannot expire from "
            f"status '{grant.status.value}'"
        )

    if grant.expires_at is None:
        raise ValidationError(
            "Active emergency access grant "
            "is missing an expiry timestamp"
        )

    now = _utcnow()
    grant_expires_at = _as_utc_aware(
        grant.expires_at,
    )

    if grant_expires_at > now:
        raise ConflictError(
            "Emergency access grant has not expired yet"
        )

    old_status = grant.status

    grant.status = EmergencyAccessStatus.EXPIRED

    _audit_status_change(
        grant,
        user_id=None,
        description=(
            "Emergency access automatically expired"
        ),
        old_status=old_status,
        new_status=EmergencyAccessStatus.EXPIRED,
        extra={
            "expired_at": now.isoformat(),
        },
    )

    return grant


def assert_emergency_access(
    *,
    actor_id: int,
    patient_id: int,
    resource_type: str,
    resource_id: int,
    action: str,
    emergency_access_id: int | None = None,
) -> bool:
    actor_id = _validate_positive_id(
        actor_id,
        "Actor ID",
    )

    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    resource_id = _validate_positive_id(
        resource_id,
        "Resource ID",
    )
    if emergency_access_id is not None:
        emergency_access_id = _validate_positive_id(
            emergency_access_id,
            "Emergency access ID",
        )

    resource_type = _normalize_required_string(
        resource_type,
        "Resource type",
        80,
    ).lower()

    action = _normalize_required_string(
        action,
        "Action",
        80,
    ).lower()

    if not _SCOPE_TOKEN_PATTERN.fullmatch(
        resource_type
    ):
        raise ValidationError(
            "Invalid resource type"
        )

    if not _SCOPE_TOKEN_PATTERN.fullmatch(
        action
    ):
        raise ValidationError(
            "Invalid action"
        )

    actor = _get_user(
        actor_id,
    )

    role = _normalize_role(
        actor.role
    )

    if role not in EMERGENCY_REQUESTER_ROLES:
        return False

    # Revalidate the requester against current staff and clinic lifecycle.
    staff = _get_staff_for_user(
        actor,
    )

    clinic_id = _get_user_clinic_id(
        actor,
    )

    if clinic_id is None:
        return False

    if staff.clinic_id != clinic_id:
        raise ConflictError(
            "Staff clinic does not match "
            "authenticated user clinic"
        )

    _ensure_active_clinic(
        clinic_id,
    )

    patient = _get_patient(
        patient_id,
    )

    if patient.clinic_id != clinic_id:
        return False

    now = _utcnow()

    grant_statement = db.select(
        EmergencyAccessGrant
    ).where(
        EmergencyAccessGrant.patient_id == patient_id,
        EmergencyAccessGrant.requester_user_id == actor_id,
        EmergencyAccessGrant.clinic_id == clinic_id,
        EmergencyAccessGrant.status == EmergencyAccessStatus.ACTIVE,
        EmergencyAccessGrant.expires_at > now,
    )

    if emergency_access_id is not None:
        grant_statement = grant_statement.where(
            EmergencyAccessGrant.id == emergency_access_id,
        )

    grant = db.session.execute(
        grant_statement.order_by(
            EmergencyAccessGrant.granted_at.desc(),
            EmergencyAccessGrant.id.desc(),
        ).limit(1)
    ).scalar_one_or_none()

    if grant is None:
        return False

    if not _scope_matches(
        grant.scope,
        resource_type,
        resource_id,
        action,
    ):
        return False

    if resource_type == "patient":
        if resource_id != patient_id:
            return False

    elif resource_type == "consultation":
        resource = db.session.get(
            Consultation,
            resource_id,
        )

        if (
            resource is None
            or resource.clinic_id != clinic_id
            or resource.patient_id != patient_id
        ):
            return False

    elif resource_type == "lab_order":
        resource = db.session.get(
            LabOrder,
            resource_id,
        )

        if (
            resource is None
            or resource.clinic_id != clinic_id
            or resource.patient_id != patient_id
        ):
            return False

    elif resource_type == "prescription":
        resource = db.session.get(
            Prescription,
            resource_id,
        )

        if (
            resource is None
            or resource.clinic_id != clinic_id
            or resource.patient_id != patient_id
        ):
            return False

    elif resource_type == "dispense_record":
        resource = db.session.get(
            DispenseRecord,
            resource_id,
        )
        prescription = (
            resource.prescription
            if resource is not None
            else None
        )

        if (
            resource is None
            or prescription is None
            or prescription.clinic_id != clinic_id
            or prescription.patient_id != patient_id
        ):
            return False

    elif resource_type in {
        "lab_order_list",
        "prescription_list",
    }:
        # Collection scopes refer to the patient ID, not an individual
        # lab order or prescription. Patient and clinic were validated above.
        if resource_id != patient_id:
            return False

    else:
        # Fail closed for resource types without explicit
        # ownership validation in this authorization helper.
        return False

    create_audit_log(
        action=AuditAction.VIEW,
        entity_type="EmergencyAccessGrant",
        entity_id=grant.id,
        description=(
            "Emergency access used for clinical resource"
        ),
        new_value={
            "clinic_id": grant.clinic_id,
            "patient_id": patient_id,
            "actor_user_id": actor_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "action": action,
            "grant_expires_at": (
                grant.expires_at.isoformat()
                if grant.expires_at
                else None
            ),
        },
        user_id=actor_id,
    )

    return True