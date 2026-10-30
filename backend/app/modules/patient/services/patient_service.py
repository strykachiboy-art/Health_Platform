from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import func, or_, select, update

from app.extensions import db
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.core.utils.qrcode_util import generate_tracking_code

from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.patient.models.patient_model import (
    Patient,
    PatientAllergy,
    PatientFamilyMember,
    PatientInsurance,
    PatientVitals,
)
from app.modules.staff.models.staff_model import Staff


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


_EDITABLE_PATIENT_FIELDS = {
    "first_name",
    "last_name",
    "date_of_birth",
    "gender",
    "blood_type",
    "phone",
    "email",
    "address",
    "allergies",
    "chronic_conditions",
    "emirates_id",
    "umrn",
}

_EDITABLE_FAMILY_FIELDS = {
    "full_name",
    "relation",
    "phone",
    "is_emergency_contact",
}

_EDITABLE_INSURANCE_FIELDS = {
    "provider_name",
    "policy_number",
    "plan_type",
    "coverage_start",
    "coverage_end",
    "is_primary",
    "is_active",
}

_EDITABLE_VITAL_FIELDS = {
    "temperature",
    "blood_pressure_systolic",
    "blood_pressure_diastolic",
    "heart_rate",
    "respiratory_rate",
    "oxygen_saturation",
    "weight",
    "height",
    "recorded_at",
}

_EDITABLE_ALLERGY_FIELDS = {
    "allergen_name",
    "allergen_code",
    "allergen_type",
    "reaction",
    "severity",
    "onset_at",
    "notes",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_positive_id(
    value: int,
    field_name: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _validate_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
    ):
        raise ValidationError(
            "Page must be a positive integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or per_page < 1
    ):
        raise ValidationError(
            "Per page must be a positive integer"
        )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"Per page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _serialize_audit_value(value):
    if isinstance(value, Enum):
        return value.value

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            key: _serialize_audit_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _serialize_audit_value(item)
            for item in value
        ]

    return value


def _serialize_audit_changes(
    changes: dict,
) -> dict:
    return {
        field: _serialize_audit_value(value)
        for field, value in changes.items()
    }


def _audit(
    *,
    actor_id: Optional[int],
    clinic_id: int,
    action: AuditAction,
    entity_type: str,
    entity_id: int,
    description: str,
    changes: Optional[dict] = None,
) -> None:
    create_audit_log(
        user_id=actor_id,
        clinic_id=clinic_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        new_value=_serialize_audit_changes(
            changes or {}
        ),
    )


def _validate_patient_names(
    first_name: str,
    last_name: str,
) -> None:
    if not first_name or not first_name.strip():
        raise ValidationError("First name is required")

    if not last_name or not last_name.strip():
        raise ValidationError("Last name is required")


def _validate_date_of_birth(
    date_of_birth: Optional[date],
) -> None:
    if (
        date_of_birth is not None
        and date_of_birth > date.today()
    ):
        raise ValidationError(
            "Date of birth cannot be in the future"
        )


def _validate_timestamp(
    timestamp: Optional[datetime],
) -> None:
    if timestamp is None:
        return

    if not isinstance(timestamp, datetime):
        raise ValidationError(
            "Invalid timestamp"
        )


def _validate_fields(
    data: dict,
    allowed_fields: set[str],
) -> None:
    unknown_fields = set(data) - allowed_fields

    if unknown_fields:
        raise ValidationError(
            f"Unknown fields: {', '.join(sorted(unknown_fields))}"
        )


def _generate_patient_number(
    clinic_id: int,
) -> str:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    for _ in range(5):
        patient_number = generate_tracking_code(
            f"PT{clinic_id}"
        )

        exists = db.session.execute(
            select(Patient.id)
            .where(
                Patient.patient_number == patient_number
            )
            .limit(1)
        ).scalar_one_or_none()

        if exists is None:
            return patient_number

    raise ConflictError(
        "Unable to generate a unique patient number. "
        "Please try again."
    )


def _get_patient_or_404(
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


def _get_family_member_or_404(
    patient_id: int,
    family_member_id: int,
) -> PatientFamilyMember:
    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    family_member_id = _validate_positive_id(
        family_member_id,
        "Family member ID",
    )

    member = db.session.execute(
        select(PatientFamilyMember)
        .where(
            PatientFamilyMember.id == family_member_id,
            PatientFamilyMember.patient_id == patient_id,
        )
        .limit(1)
    ).scalar_one_or_none()

    if member is None:
        raise NotFoundError(
            f"Family member {family_member_id} "
            f"not found for patient {patient_id}"
        )

    return member


def _get_insurance_or_404(
    patient_id: int,
    insurance_id: int,
) -> PatientInsurance:
    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    insurance_id = _validate_positive_id(
        insurance_id,
        "Insurance ID",
    )

    insurance = db.session.execute(
        select(PatientInsurance)
        .where(
            PatientInsurance.id == insurance_id,
            PatientInsurance.patient_id == patient_id,
        )
        .limit(1)
    ).scalar_one_or_none()

    if insurance is None:
        raise NotFoundError(
            f"Insurance {insurance_id} "
            f"not found for patient {patient_id}"
        )

    return insurance


def _get_vitals_or_404(
    patient_id: int,
    vitals_id: int,
) -> PatientVitals:
    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    vitals_id = _validate_positive_id(
        vitals_id,
        "Vitals ID",
    )

    vitals = db.session.execute(
        select(PatientVitals)
        .where(
            PatientVitals.id == vitals_id,
            PatientVitals.patient_id == patient_id,
        )
        .limit(1)
    ).scalar_one_or_none()

    if vitals is None:
        raise NotFoundError(
            f"Vitals record {vitals_id} "
            f"not found for patient {patient_id}"
        )

    return vitals


def _validate_related_patient(
    patient: Patient,
    related_patient_id: Optional[int],
) -> Optional[Patient]:
    if related_patient_id is None:
        return None

    related_patient_id = _validate_positive_id(
        related_patient_id,
        "Related patient ID",
    )

    if related_patient_id == patient.id:
        raise ValidationError(
            "A patient cannot be related to themselves"
        )

    related_patient = db.session.get(
        Patient,
        related_patient_id,
    )

    if related_patient is None:
        raise NotFoundError(
            f"Related patient {related_patient_id} not found"
        )

    if related_patient.clinic_id != patient.clinic_id:
        raise ValidationError(
            "Related patient must belong to the same clinic"
        )

    return related_patient


def _validate_staff_for_patient(
    clinic_id: int,
    staff_id: Optional[int],
) -> Optional[Staff]:
    if staff_id is None:
        return None

    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    staff_id = _validate_positive_id(
        staff_id,
        "Staff ID",
    )

    staff = db.session.get(
        Staff,
        staff_id,
    )

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    if staff.clinic_id != clinic_id:
        raise ValidationError(
            "Staff member must belong to the same clinic as the patient"
        )

    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError(
            f"Staff member {staff_id} is not active"
        )

    if (
        staff.user is not None
        and not staff.user.is_active
    ):
        raise ValidationError(
            f"User account for staff member {staff_id} is not active"
        )

    return staff


def _validate_consultation_for_vitals(
    patient: Patient,
    consultation_id: Optional[int],
):
    if consultation_id is None:
        return None

    consultation_id = _validate_positive_id(
        consultation_id,
        "Consultation ID",
    )

    from app.modules.consultation.models.consultation_model import (
        Consultation,
    )

    consultation = db.session.get(
        Consultation,
        consultation_id,
    )

    if consultation is None:
        raise NotFoundError(
            f"Consultation {consultation_id} not found"
        )

    if consultation.clinic_id != patient.clinic_id:
        raise ValidationError(
            "Consultation must belong to the same clinic as the patient"
        )

    if consultation.patient_id != patient.id:
        raise ValidationError(
            "Consultation must belong to the same patient"
        )

    return consultation


def get_patient(
    patient_id: int,
) -> Patient:
    return _get_patient_or_404(patient_id)


def list_patients(
    clinic_id: Optional[int] = None,
    active_only: bool = False,
    search: Optional[str] = None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

    statement = select(Patient)

    if clinic_id is not None:
        statement = statement.where(
            Patient.clinic_id == clinic_id
        )

    if active_only:
        statement = statement.where(
            Patient.is_active.is_(True)
        )

    if search is not None:
        search_term = search.strip()

        if search_term:
            pattern = f"%{search_term}%"

            statement = statement.where(
                or_(
                    Patient.first_name.ilike(pattern),
                    Patient.last_name.ilike(pattern),
                    Patient.patient_number.ilike(pattern),
                    Patient.phone.ilike(pattern),
                    Patient.email.ilike(pattern),
                )
            )

    statement = statement.order_by(
        Patient.last_name.asc(),
        Patient.first_name.asc(),
        Patient.id.asc(),
    )

    return db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )


@transactional
def create_patient(
    clinic_id: int,
    data: dict,
    actor_id: Optional[int] = None,
) -> Patient:
    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    ensure_clinic_active(clinic_id)

    _validate_fields(
        data,
        _EDITABLE_PATIENT_FIELDS,
    )

    first_name = data.get("first_name")
    last_name = data.get("last_name")

    _validate_patient_names(
        first_name,
        last_name,
    )

    _validate_date_of_birth(
        data.get("date_of_birth")
    )

    patient_number = _generate_patient_number(
        clinic_id
    )

    patient = Patient(
        clinic_id=clinic_id,
        patient_number=patient_number,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        date_of_birth=data.get("date_of_birth"),
        gender=data.get("gender"),
        blood_type=data.get("blood_type"),
        phone=data.get("phone"),
        email=data.get("email"),
        address=data.get("address"),
        allergies=data.get("allergies"),
        chronic_conditions=data.get(
            "chronic_conditions"
        ),
        emirates_id=data.get("emirates_id"),
        umrn=data.get("umrn"),
        is_active=True,
    )

    db.session.add(patient)
    db.session.flush()

    _audit(
        actor_id=actor_id,
        clinic_id=clinic_id,
        action=AuditAction.CREATE,
        entity_type="patient",
        entity_id=patient.id,
        description=(
            f"Created patient {patient.id} "
            f"in clinic {clinic_id}"
        ),
        changes={
            "clinic_id": clinic_id,
            "patient_number": patient.patient_number,
        },
    )

    return patient


@transactional
def update_patient(
    patient_id: int,
    data: dict,
    actor_id: Optional[int] = None,
) -> Patient:
    patient = _get_patient_or_404(
        patient_id
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    _validate_fields(
        data,
        _EDITABLE_PATIENT_FIELDS,
    )

    if "date_of_birth" in data:
        _validate_date_of_birth(
            data["date_of_birth"]
        )

    if "first_name" in data:
        if (
            data["first_name"] is None
            or not data["first_name"].strip()
        ):
            raise ValidationError(
                "First name cannot be empty"
            )

    if "last_name" in data:
        if (
            data["last_name"] is None
            or not data["last_name"].strip()
        ):
            raise ValidationError(
                "Last name cannot be empty"
            )

    changed_fields = {}

    for field, value in data.items():
        if field in {
            "first_name",
            "last_name",
        } and value is not None:
            value = value.strip()

        old_value = getattr(
            patient,
            field,
        )

        if old_value != value:
            changed_fields[field] = {
                "old": old_value,
                "new": value,
            }

            setattr(
                patient,
                field,
                value,
            )

    if changed_fields:
        patient.updated_at = _utcnow()

        _audit(
            actor_id=actor_id,
            clinic_id=patient.clinic_id,
            action=AuditAction.UPDATE,
            entity_type="patient",
            entity_id=patient.id,
            description=(
                f"Updated patient {patient.id} "
                f"in clinic {patient.clinic_id}"
            ),
            changes={
                "clinic_id": patient.clinic_id,
                "changed_fields": changed_fields,
            },
        )

    return patient


@transactional
def set_active_status(
    patient_id: int,
    is_active: bool,
    actor_id: Optional[int] = None,
) -> Patient:
    patient = _get_patient_or_404(
        patient_id
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    if not isinstance(is_active, bool):
        raise ValidationError(
            "is_active must be a boolean"
        )

    if patient.is_active == is_active:
        return patient

    old_status = patient.is_active

    patient.is_active = is_active
    patient.updated_at = _utcnow()

    _audit(
        actor_id=actor_id,
        clinic_id=patient.clinic_id,
        action=AuditAction.UPDATE,
        entity_type="patient",
        entity_id=patient.id,
        description=(
            f"Changed patient {patient.id} active status "
            f"in clinic {patient.clinic_id}"
        ),
        changes={
            "clinic_id": patient.clinic_id,
            "is_active": {
                "old": old_status,
                "new": is_active,
            },
        },
    )

    return patient


def list_family_members(
    patient_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    patient = _get_patient_or_404(
        patient_id
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = (
        select(PatientFamilyMember)
        .where(
            PatientFamilyMember.patient_id == patient.id
        )
        .order_by(
            PatientFamilyMember.is_emergency_contact.desc(),
            PatientFamilyMember.full_name.asc(),
            PatientFamilyMember.id.asc(),
        )
    )

    return db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )


@transactional
def add_family_member(
    patient_id: int,
    data: dict,
    actor_id: Optional[int] = None,
) -> PatientFamilyMember:
    patient = _get_patient_or_404(
        patient_id
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    allowed_fields = (
        _EDITABLE_FAMILY_FIELDS
        | {"related_patient_id"}
    )

    _validate_fields(
        data,
        allowed_fields,
    )

    full_name = data.get(
        "full_name"
    )

    if (
        not full_name
        or not full_name.strip()
    ):
        raise ValidationError(
            "Family member name is required"
        )

    related_patient_id = data.get(
        "related_patient_id"
    )

    _validate_related_patient(
        patient,
        related_patient_id,
    )

    member = PatientFamilyMember(
        patient_id=patient.id,
        related_patient_id=related_patient_id,
        full_name=full_name.strip(),
        relation=data.get("relation"),
        phone=data.get("phone"),
        is_emergency_contact=data.get(
            "is_emergency_contact",
            False,
        ),
    )

    db.session.add(member)
    db.session.flush()

    _audit(
        actor_id=actor_id,
        clinic_id=patient.clinic_id,
        action=AuditAction.CREATE,
        entity_type="patient_family_member",
        entity_id=member.id,
        description=(
            f"Added family member {member.id} "
            f"to patient {patient.id}"
        ),
        changes={
            "clinic_id": patient.clinic_id,
            "patient_id": patient.id,
            "related_patient_id": related_patient_id,
        },
    )

    return member


@transactional
def update_family_member(
    patient_id: int,
    family_member_id: int,
    data: dict,
    actor_id: Optional[int] = None,
) -> PatientFamilyMember:
    patient = _get_patient_or_404(
        patient_id
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    _validate_fields(
        data,
        _EDITABLE_FAMILY_FIELDS
        | {"related_patient_id"},
    )

    member = _get_family_member_or_404(
        patient.id,
        family_member_id,
    )

    if "related_patient_id" in data:
        _validate_related_patient(
            patient,
            data["related_patient_id"],
        )

    if "full_name" in data:
        if (
            data["full_name"] is None
            or not data["full_name"].strip()
        ):
            raise ValidationError(
                "Family member name cannot be empty"
            )

    changed_fields = {}

    for field, value in data.items():
        if (
            field == "full_name"
            and value is not None
        ):
            value = value.strip()

        old_value = getattr(
            member,
            field,
        )

        if old_value != value:
            changed_fields[field] = {
                "old": old_value,
                "new": value,
            }

            setattr(
                member,
                field,
                value,
            )

    if changed_fields:
        member.updated_at = _utcnow()

        _audit(
            actor_id=actor_id,
            clinic_id=patient.clinic_id,
            action=AuditAction.UPDATE,
            entity_type="patient_family_member",
            entity_id=member.id,
            description=(
                f"Updated family member {member.id} "
                f"for patient {patient.id}"
            ),
            changes={
                "clinic_id": patient.clinic_id,
                "patient_id": patient.id,
                "changed_fields": changed_fields,
            },
        )

    return member


@transactional
def remove_family_member(
    patient_id: int,
    family_member_id: int,
    actor_id: Optional[int] = None,
) -> None:
    patient = _get_patient_or_404(
        patient_id
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    member = _get_family_member_or_404(
        patient.id,
        family_member_id,
    )

    _audit(
        actor_id=actor_id,
        clinic_id=patient.clinic_id,
        action=AuditAction.DELETE,
        entity_type="patient_family_member",
        entity_id=member.id,
        description=(
            f"Removed family member {member.id} "
            f"from patient {patient.id}"
        ),
        changes={
            "clinic_id": patient.clinic_id,
            "patient_id": patient.id,
        },
    )

    db.session.delete(member)


def list_insurances(
    patient_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    patient = _get_patient_or_404(
        patient_id
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = (
        select(PatientInsurance)
        .where(
            PatientInsurance.patient_id == patient.id
        )
        .order_by(
            PatientInsurance.is_primary.desc(),
            PatientInsurance.is_active.desc(),
            PatientInsurance.created_at.desc(),
            PatientInsurance.id.desc(),
        )
    )

    return db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )


@transactional
def add_insurance(
    patient_id: int,
    data: dict,
    actor_id: Optional[int] = None,
) -> PatientInsurance:
    patient = _get_patient_or_404(
        patient_id
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    _validate_fields(
        data,
        _EDITABLE_INSURANCE_FIELDS,
    )

    provider_name = data.get(
        "provider_name"
    )

    policy_number = data.get(
        "policy_number"
    )

    if (
        not provider_name
        or not provider_name.strip()
    ):
        raise ValidationError(
            "Insurance provider name is required"
        )

    if (
        not policy_number
        or not policy_number.strip()
    ):
        raise ValidationError(
            "Insurance policy number is required"
        )

    coverage_start = data.get(
        "coverage_start"
    )

    coverage_end = data.get(
        "coverage_end"
    )

    if (
        coverage_start is not None
        and coverage_end is not None
        and coverage_start > coverage_end
    ):
        raise ValidationError(
            "Insurance coverage start date cannot be after coverage end date"
        )

    is_primary = data.get(
        "is_primary",
        False,
    )

    if not isinstance(is_primary, bool):
        raise ValidationError(
            "is_primary must be a boolean"
        )

    if is_primary:
        db.session.execute(
            update(PatientInsurance)
            .where(
                PatientInsurance.patient_id == patient.id,
                PatientInsurance.is_primary.is_(True),
            )
            .values(
                is_primary=False,
                updated_at=_utcnow(),
            )
        )

    insurance = PatientInsurance(
        patient_id=patient.id,
        provider_name=provider_name.strip(),
        policy_number=policy_number.strip(),
        plan_type=data.get("plan_type"),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        is_primary=is_primary,
        is_active=data.get(
            "is_active",
            True,
        ),
    )

    db.session.add(insurance)
    db.session.flush()

    _audit(
        actor_id=actor_id,
        clinic_id=patient.clinic_id,
        action=AuditAction.CREATE,
        entity_type="patient_insurance",
        entity_id=insurance.id,
        description=(
            f"Added insurance {insurance.id} "
            f"to patient {patient.id}"
        ),
        changes={
            "clinic_id": patient.clinic_id,
            "patient_id": patient.id,
            "provider_name": insurance.provider_name,
            "is_primary": insurance.is_primary,
        },
    )

    return insurance


@transactional
def update_insurance(
    patient_id: int,
    insurance_id: int,
    data: dict,
    actor_id: Optional[int] = None,
) -> PatientInsurance:
    patient = _get_patient_or_404(
        patient_id
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    _validate_fields(
        data,
        _EDITABLE_INSURANCE_FIELDS,
    )

    insurance = _get_insurance_or_404(
        patient.id,
        insurance_id,
    )

    if (
        "coverage_start" in data
        or "coverage_end" in data
    ):
        coverage_start = data.get(
            "coverage_start",
            insurance.coverage_start,
        )

        coverage_end = data.get(
            "coverage_end",
            insurance.coverage_end,
        )

        if (
            coverage_start is not None
            and coverage_end is not None
            and coverage_start > coverage_end
        ):
            raise ValidationError(
                "Insurance coverage start date cannot be after coverage end date"
            )

    if "provider_name" in data:
        if (
            data["provider_name"] is None
            or not data["provider_name"].strip()
        ):
            raise ValidationError(
                "Insurance provider name cannot be empty"
            )

    if "policy_number" in data:
        if (
            data["policy_number"] is None
            or not data["policy_number"].strip()
        ):
            raise ValidationError(
                "Insurance policy number cannot be empty"
            )

    if "is_primary" in data:
        if not isinstance(data["is_primary"], bool):
            raise ValidationError(
                "is_primary must be a boolean"
            )

    if data.get("is_primary") is True:
        db.session.execute(
            update(PatientInsurance)
            .where(
                PatientInsurance.patient_id == patient.id,
                PatientInsurance.id != insurance.id,
                PatientInsurance.is_primary.is_(True),
            )
            .values(
                is_primary=False,
                updated_at=_utcnow(),
            )
        )

    changed_fields = {}

    for field, value in data.items():
        if field in {
            "provider_name",
            "policy_number",
        } and value is not None:
            value = value.strip()

        old_value = getattr(
            insurance,
            field,
        )

        if old_value != value:
            changed_fields[field] = {
                "old": old_value,
                "new": value,
            }

            setattr(
                insurance,
                field,
                value,
            )

    if changed_fields:
        insurance.updated_at = _utcnow()

        _audit(
            actor_id=actor_id,
            clinic_id=patient.clinic_id,
            action=AuditAction.UPDATE,
            entity_type="patient_insurance",
            entity_id=insurance.id,
            description=(
                f"Updated insurance {insurance.id} "
                f"for patient {patient.id}"
            ),
            changes={
                "clinic_id": patient.clinic_id,
                "patient_id": patient.id,
                "changed_fields": changed_fields,
            },
        )

    return insurance


def get_vitals_history(
    patient_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    patient = _get_patient_or_404(
        patient_id
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = (
        select(PatientVitals)
        .where(
            PatientVitals.patient_id == patient.id
        )
        .order_by(
            PatientVitals.recorded_at.desc(),
            PatientVitals.id.desc(),
        )
    )

    return db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )


def get_latest_vitals(
    patient_id: int,
) -> Optional[PatientVitals]:
    patient = _get_patient_or_404(
        patient_id
    )

    statement = (
        select(PatientVitals)
        .where(
            PatientVitals.patient_id == patient.id
        )
        .order_by(
            PatientVitals.recorded_at.desc(),
            PatientVitals.id.desc(),
        )
        .limit(1)
    )

    return db.session.execute(
        statement
    ).scalar_one_or_none()


@transactional
def record_vitals(
    patient_id: int,
    data: dict,
    consultation_id: Optional[int] = None,
    recorded_by_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> PatientVitals:
    patient = _get_patient_or_404(
        patient_id
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    _validate_fields(
        data,
        _EDITABLE_VITAL_FIELDS,
    )

    if not data:
        raise ValidationError(
            "At least one vital measurement is required"
        )

    measurements = {
        field: value
        for field, value in data.items()
        if field in _EDITABLE_VITAL_FIELDS
        and value is not None
    }

    if not measurements:
        raise ValidationError(
            "At least one vital measurement is required"
        )

    recorded_at = data.get("recorded_at")

    _validate_timestamp(
        recorded_at
    )

    _validate_consultation_for_vitals(
        patient,
        consultation_id,
    )

    _validate_staff_for_patient(
        patient.clinic_id,
        recorded_by_id,
    )

    vitals = PatientVitals(
        patient_id=patient.id,
        consultation_id=consultation_id,
        recorded_by_id=recorded_by_id,
        temperature_c=data.get(
            "temperature"
        ),
        blood_pressure_systolic=data.get(
            "blood_pressure_systolic"
        ),
        blood_pressure_diastolic=data.get(
            "blood_pressure_diastolic"
        ),
        heart_rate_bpm=data.get(
            "heart_rate"
        ),
        respiratory_rate=data.get(
            "respiratory_rate"
        ),
        oxygen_saturation=data.get(
            "oxygen_saturation"
        ),
        weight_kg=data.get(
            "weight"
        ),
        height_cm=data.get(
            "height"
        ),
    )

    if recorded_at is not None:
        vitals.recorded_at = recorded_at

    db.session.add(vitals)
    db.session.flush()

    _audit(
        actor_id=actor_id,
        clinic_id=patient.clinic_id,
        action=AuditAction.CREATE,
        entity_type="patient_vitals",
        entity_id=vitals.id,
        description=(
            f"Recorded vitals {vitals.id} "
            f"for patient {patient.id}"
        ),
        changes={
            "clinic_id": patient.clinic_id,
            "patient_id": patient.id,
            "consultation_id": consultation_id,
            "recorded_by_id": recorded_by_id,
        },
    )

    return vitals


def _normalize_allergy_text(
    value,
    *,
    field_name: str,
    required: bool = False,
):
    if value is None:
        if required:
            raise ValidationError(
                f"{field_name} is required"
            )
        return None

    if not isinstance(
        value,
        str,
    ):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    normalized = value.strip()

    if required and not normalized:
        raise ValidationError(
            f"{field_name} is required"
        )

    return (
        normalized
        if normalized
        else None
    )


def _coerce_allergy_choice(
    value,
    *,
    field_name: str,
    allowed: set[str],
    default: Optional[str] = None,
) -> str:
    if value is None:
        if default is not None:
            return default

        raise ValidationError(
            f"{field_name} is required"
        )

    if isinstance(
        value,
        Enum,
    ):
        value = value.value

    if not isinstance(
        value,
        str,
    ):
        raise ValidationError(
            f"{field_name} is invalid"
        )

    normalized = value.strip().lower()

    if normalized not in allowed:
        raise ValidationError(
            f"{field_name} is invalid"
        )

    return normalized


def _normalize_allergy_datetime(
    value,
    *,
    field_name: str,
):
    if value is None:
        return None

    if not isinstance(
        value,
        datetime,
    ):
        raise ValidationError(
            f"{field_name} must be a datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(
            f"{field_name} must include timezone information"
        )

    return value.astimezone(
        timezone.utc
    )


def _get_patient_for_clinic_or_404(
    clinic_id: int,
    patient_id: int,
) -> Patient:
    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    patient = _get_patient_or_404(
        patient_id
    )

    if patient.clinic_id != clinic_id:
        raise ValidationError(
            "Patient does not belong to clinic"
        )

    return patient


def _get_allergy_or_404(
    *,
    clinic_id: int,
    patient_id: int,
    allergy_id: int,
) -> PatientAllergy:
    clinic_id = _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    allergy_id = _validate_positive_id(
        allergy_id,
        "Allergy ID",
    )

    record = db.session.execute(
        select(
            PatientAllergy
        )
        .where(
            PatientAllergy.id == allergy_id,
            PatientAllergy.clinic_id == clinic_id,
            PatientAllergy.patient_id == patient_id,
        )
        .limit(
            1
        )
    ).scalar_one_or_none()

    if record is None:
        raise NotFoundError(
            f"Allergy {allergy_id} not found "
            f"for patient {patient_id}"
        )

    return record


def _normalized_allergy_identity(
    *,
    allergen_name: str,
    allergen_code: Optional[str],
    allergen_type: str,
) -> tuple[str, Optional[str], str]:
    normalized_name = (
        _normalize_allergy_text(
            allergen_name,
            field_name="allergen_name",
            required=True,
        )
        or ""
    )

    normalized_code = (
        _normalize_allergy_text(
            allergen_code,
            field_name="allergen_code",
        )
    )

    normalized_type = _coerce_allergy_choice(
        allergen_type,
        field_name="allergen_type",
        allowed={
            "drug",
            "food",
            "environmental",
            "latex",
            "biologic",
            "other",
        },
        default="other",
    )

    return (
        normalized_name,
        normalized_code,
        normalized_type,
    )


def _find_duplicate_active_allergy(
    *,
    clinic_id: int,
    patient_id: int,
    allergen_name: str,
    allergen_code: Optional[str],
    allergen_type: str,
    exclude_allergy_id: Optional[int] = None,
) -> Optional[PatientAllergy]:
    statement = select(
        PatientAllergy
    ).where(
        PatientAllergy.clinic_id == clinic_id,
        PatientAllergy.patient_id == patient_id,
        PatientAllergy.clinical_status == "active",
        PatientAllergy.verification_status != "refuted",
        PatientAllergy.allergen_type == allergen_type,
    )

    if allergen_code:
        statement = statement.where(
            func.lower(
                PatientAllergy.allergen_code
            )
            == allergen_code.lower()
        )
    else:
        statement = statement.where(
            PatientAllergy.allergen_code.is_(None),
            func.lower(
                func.trim(
                    PatientAllergy.allergen_name
                )
            )
            == allergen_name.lower(),
        )

    if exclude_allergy_id is not None:
        statement = statement.where(
            PatientAllergy.id
            != exclude_allergy_id
        )

    return db.session.execute(
        statement.limit(
            1
        )
    ).scalar_one_or_none()


def get_patient_allergy(
    *,
    clinic_id: int,
    patient_id: int,
    allergy_id: int,
) -> PatientAllergy:
    _get_patient_for_clinic_or_404(
        clinic_id,
        patient_id,
    )

    return _get_allergy_or_404(
        clinic_id=clinic_id,
        patient_id=patient_id,
        allergy_id=allergy_id,
    )


def list_patient_allergies(
    *,
    clinic_id: int,
    patient_id: int,
    active_only: bool = False,
    include_refuted: bool = True,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    patient = _get_patient_for_clinic_or_404(
        clinic_id,
        patient_id,
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    if not isinstance(
        active_only,
        bool,
    ):
        raise ValidationError(
            "active_only must be a boolean"
        )

    if not isinstance(
        include_refuted,
        bool,
    ):
        raise ValidationError(
            "include_refuted must be a boolean"
        )

    statement = select(
        PatientAllergy
    ).where(
        PatientAllergy.clinic_id == patient.clinic_id,
        PatientAllergy.patient_id == patient.id,
    )

    if active_only:
        statement = statement.where(
            PatientAllergy.clinical_status == "active"
        )

    if not include_refuted:
        statement = statement.where(
            PatientAllergy.verification_status != "refuted"
        )

    statement = statement.order_by(
        PatientAllergy.recorded_at.desc(),
        PatientAllergy.id.desc(),
    )

    return db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )


@transactional
def create_patient_allergy(
    *,
    clinic_id: int,
    patient_id: int,
    data: dict,
    recorded_by_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> PatientAllergy:
    patient = _get_patient_for_clinic_or_404(
        clinic_id,
        patient_id,
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    if not isinstance(
        data,
        dict,
    ):
        raise ValidationError(
            "Allergy data must be an object"
        )

    _validate_fields(
        data,
        _EDITABLE_ALLERGY_FIELDS,
    )

    recorded_by = _validate_staff_for_patient(
        patient.clinic_id,
        recorded_by_id,
    )

    allergen_name, allergen_code, allergen_type = (
        _normalized_allergy_identity(
            allergen_name=data.get(
                "allergen_name"
            ),
            allergen_code=data.get(
                "allergen_code"
            ),
            allergen_type=data.get(
                "allergen_type",
                "other",
            ),
        )
    )

    reaction = _normalize_allergy_text(
        data.get(
            "reaction"
        ),
        field_name="reaction",
    )

    notes = _normalize_allergy_text(
        data.get(
            "notes"
        ),
        field_name="notes",
    )

    severity = _coerce_allergy_choice(
        data.get(
            "severity",
            "unknown",
        ),
        field_name="severity",
        allowed={
            "unknown",
            "mild",
            "moderate",
            "severe",
            "life_threatening",
        },
        default="unknown",
    )

    onset_at = _normalize_allergy_datetime(
        data.get(
            "onset_at"
        ),
        field_name="onset_at",
    )

    duplicate = _find_duplicate_active_allergy(
        clinic_id=patient.clinic_id,
        patient_id=patient.id,
        allergen_name=allergen_name,
        allergen_code=allergen_code,
        allergen_type=allergen_type,
    )

    if duplicate is not None:
        raise ConflictError(
            "An active non-refuted allergy with the same "
            "allergen identity already exists"
        )

    now = _utcnow()

    record = PatientAllergy(
        clinic_id=patient.clinic_id,
        patient_id=patient.id,
        allergen_name=allergen_name,
        allergen_code=allergen_code,
        allergen_type=allergen_type,
        reaction=reaction,
        severity=severity,
        clinical_status="active",
        verification_status="unconfirmed",
        onset_at=onset_at,
        recorded_at=now,
        recorded_by_id=(
            recorded_by.id
            if recorded_by is not None
            else None
        ),
        notes=notes,
    )

    db.session.add(
        record
    )

    db.session.flush()

    _audit(
        actor_id=actor_id,
        clinic_id=patient.clinic_id,
        action=AuditAction.CREATE,
        entity_type="patient_allergy",
        entity_id=record.id,
        description=(
            f"Created allergy {record.id} "
            f"for patient {patient.id}"
        ),
        changes={
            "clinic_id": patient.clinic_id,
            "patient_id": patient.id,
            "allergen_name": record.allergen_name,
            "allergen_code": record.allergen_code,
            "allergen_type": record.allergen_type,
            "severity": record.severity,
            "clinical_status": record.clinical_status,
            "verification_status": (
                record.verification_status
            ),
            "recorded_by_id": record.recorded_by_id,
        },
    )

    return record


@transactional
def update_patient_allergy(
    *,
    clinic_id: int,
    patient_id: int,
    allergy_id: int,
    data: dict,
    actor_id: Optional[int] = None,
) -> PatientAllergy:
    patient = _get_patient_for_clinic_or_404(
        clinic_id,
        patient_id,
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    record = _get_allergy_or_404(
        clinic_id=patient.clinic_id,
        patient_id=patient.id,
        allergy_id=allergy_id,
    )

    if not isinstance(
        data,
        dict,
    ):
        raise ValidationError(
            "Allergy data must be an object"
        )

    _validate_fields(
        data,
        _EDITABLE_ALLERGY_FIELDS,
    )

    if not data:
        raise ValidationError(
            "At least one allergy field must be supplied"
        )

    proposed_name = record.allergen_name
    proposed_code = record.allergen_code
    proposed_type = record.allergen_type

    if "allergen_name" in data:
        proposed_name = (
            _normalize_allergy_text(
                data["allergen_name"],
                field_name="allergen_name",
                required=True,
            )
            or ""
        )

    if "allergen_code" in data:
        proposed_code = _normalize_allergy_text(
            data["allergen_code"],
            field_name="allergen_code",
        )

    if "allergen_type" in data:
        proposed_type = _coerce_allergy_choice(
            data["allergen_type"],
            field_name="allergen_type",
            allowed={
                "drug",
                "food",
                "environmental",
                "latex",
                "biologic",
                "other",
            },
        )

    duplicate = _find_duplicate_active_allergy(
        clinic_id=patient.clinic_id,
        patient_id=patient.id,
        allergen_name=proposed_name,
        allergen_code=proposed_code,
        allergen_type=proposed_type,
        exclude_allergy_id=record.id,
    )

    if (
        record.clinical_status == "active"
        and record.verification_status != "refuted"
        and duplicate is not None
    ):
        raise ConflictError(
            "An active non-refuted allergy with the same "
            "allergen identity already exists"
        )

    normalized = {}

    for field, value in data.items():
        if field == "allergen_name":
            normalized[field] = proposed_name
        elif field == "allergen_code":
            normalized[field] = proposed_code
        elif field == "allergen_type":
            normalized[field] = proposed_type
        elif field in {
            "reaction",
            "notes",
        }:
            normalized[field] = _normalize_allergy_text(
                value,
                field_name=field,
            )
        elif field == "severity":
            normalized[field] = _coerce_allergy_choice(
                value,
                field_name="severity",
                allowed={
                    "unknown",
                    "mild",
                    "moderate",
                    "severe",
                    "life_threatening",
                },
            )
        elif field == "onset_at":
            normalized[field] = _normalize_allergy_datetime(
                value,
                field_name="onset_at",
            )

    changed_fields = {}

    for field, value in normalized.items():
        old_value = getattr(
            record,
            field,
        )

        if old_value != value:
            changed_fields[field] = {
                "old": old_value,
                "new": value,
            }

            setattr(
                record,
                field,
                value,
            )

    if changed_fields:
        record.updated_at = _utcnow()

        _audit(
            actor_id=actor_id,
            clinic_id=patient.clinic_id,
            action=AuditAction.UPDATE,
            entity_type="patient_allergy",
            entity_id=record.id,
            description=(
                f"Updated allergy {record.id} "
                f"for patient {patient.id}"
            ),
            changes={
                "clinic_id": patient.clinic_id,
                "patient_id": patient.id,
                "changed_fields": changed_fields,
            },
        )

    return record


@transactional
def transition_patient_allergy_status(
    *,
    clinic_id: int,
    patient_id: int,
    allergy_id: int,
    clinical_status: Optional[str] = None,
    verification_status: Optional[str] = None,
    reason: str,
    actor_id: Optional[int] = None,
) -> PatientAllergy:
    patient = _get_patient_for_clinic_or_404(
        clinic_id,
        patient_id,
    )

    ensure_clinic_active(
        patient.clinic_id
    )

    record = _get_allergy_or_404(
        clinic_id=patient.clinic_id,
        patient_id=patient.id,
        allergy_id=allergy_id,
    )

    normalized_reason = (
        _normalize_allergy_text(
            reason,
            field_name="reason",
            required=True,
        )
        or ""
    )

    next_clinical_status = record.clinical_status
    next_verification_status = record.verification_status

    if clinical_status is not None:
        next_clinical_status = _coerce_allergy_choice(
            clinical_status,
            field_name="clinical_status",
            allowed={
                "active",
                "inactive",
                "resolved",
            },
        )

    if verification_status is not None:
        next_verification_status = _coerce_allergy_choice(
            verification_status,
            field_name="verification_status",
            allowed={
                "unconfirmed",
                "confirmed",
                "refuted",
            },
        )

    if (
        clinical_status is None
        and verification_status is None
    ):
        raise ValidationError(
            "At least one allergy status must change"
        )

    if next_verification_status == "refuted":
        next_clinical_status = "inactive"

    if (
        next_clinical_status == record.clinical_status
        and next_verification_status
        == record.verification_status
    ):
        raise ConflictError(
            "Allergy status transition does not change state"
        )

    if (
        next_clinical_status == "active"
        and next_verification_status != "refuted"
    ):
        duplicate = _find_duplicate_active_allergy(
            clinic_id=patient.clinic_id,
            patient_id=patient.id,
            allergen_name=record.allergen_name,
            allergen_code=record.allergen_code,
            allergen_type=record.allergen_type,
            exclude_allergy_id=record.id,
        )

        if duplicate is not None:
            raise ConflictError(
                "Cannot activate allergy because an active "
                "non-refuted duplicate already exists"
            )

    old_clinical_status = record.clinical_status
    old_verification_status = record.verification_status

    record.clinical_status = next_clinical_status
    record.verification_status = (
        next_verification_status
    )
    record.updated_at = _utcnow()

    _audit(
        actor_id=actor_id,
        clinic_id=patient.clinic_id,
        action=AuditAction.UPDATE,
        entity_type="patient_allergy",
        entity_id=record.id,
        description=(
            f"Transitioned allergy {record.id} "
            f"for patient {patient.id}"
        ),
        changes={
            "clinic_id": patient.clinic_id,
            "patient_id": patient.id,
            "clinical_status": {
                "old": old_clinical_status,
                "new": record.clinical_status,
            },
            "verification_status": {
                "old": old_verification_status,
                "new": record.verification_status,
            },
            "reason": normalized_reason,
        },
    )

    return record
