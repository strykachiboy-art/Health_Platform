from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func, select

from app.extensions import db

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import (
    AdmissionStatus,
    BedStatus,
    ReservationStatus,
    WardType,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional

from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff

from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    BedReservation,
    Ward,
    WardTransfer,
)


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500

_EDITABLE_WARD_FIELDS = {
    "name",
    "ward_type",
    "capacity",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _db_now() -> datetime:
    return _utcnow().replace(tzinfo=None)


def _normalize_db_datetime(
    value: datetime | None,
) -> datetime | None:
    if value is None:
        return None

    if not isinstance(value, datetime):
        raise ValidationError(
            "Invalid datetime value"
        )

    if value.tzinfo is not None:
        return (
            value
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )

    return value


def _normalize_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    value = value.strip()

    return value or None


def _validate_positive_id(
    value: int,
    field_name: str,
) -> int:
    if value is None:
        raise ValidationError(
            f"{field_name} is required"
        )

    if isinstance(value, bool) or not isinstance(
        value,
        int,
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    if value <= 0:
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _validate_pagination(
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> tuple[int, int]:
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
    ):
        raise ValidationError(
            "page must be a positive integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or per_page < 1
    ):
        raise ValidationError(
            "per_page must be a positive integer"
        )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"per_page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _paginate(
    statement,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
    count_statement=None,
) -> dict:
    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    if count_statement is None:
        count_statement = statement.with_only_columns(
            func.count(),
            maintain_column_froms=True,
        ).order_by(None)

    total = (
        db.session.execute(
            count_statement
        ).scalar_one()
    )

    items = (
        db.session.execute(
            statement.limit(per_page).offset(
                (page - 1) * per_page
            )
        )
        .scalars()
        .all()
    )

    return {
        "items": items,
        "total": int(total or 0),
        "page": page,
        "per_page": per_page,
    }


def _validate_ward_type(
    ward_type,
) -> WardType:
    if ward_type is None:
        return WardType.GENERAL

    if isinstance(
        ward_type,
        WardType,
    ):
        return ward_type

    try:
        return WardType(ward_type)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid ward type: {ward_type}"
        ) from exc


def _validate_bed_status(
    status,
) -> BedStatus:
    if isinstance(
        status,
        BedStatus,
    ):
        return status

    try:
        return BedStatus(status)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid bed status: {status}"
        ) from exc


def _validate_reservation_status(
    status,
) -> ReservationStatus:
    if isinstance(
        status,
        ReservationStatus,
    ):
        return status

    try:
        return ReservationStatus(status)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid reservation status: {status}"
        ) from exc


def _validate_ward_capacity_value(
    capacity: int,
) -> int:
    if capacity is None:
        raise ValidationError(
            "capacity is required"
        )

    if isinstance(capacity, bool) or not isinstance(
        capacity,
        int,
    ):
        raise ValidationError(
            "capacity must be an integer"
        )

    if capacity < 0:
        raise ValidationError(
            "capacity cannot be negative"
        )

    return capacity


def _validate_bed_number(
    bed_number: str,
) -> str:
    if not isinstance(
        bed_number,
        str,
    ):
        raise ValidationError(
            "bed_number must be a string"
        )

    bed_number = _normalize_text(
        bed_number
    )

    if not bed_number:
        raise ValidationError(
            "bed_number is required"
        )

    if len(bed_number) > 30:
        raise ValidationError(
            "bed_number cannot exceed 30 characters"
        )

    return bed_number


def _validate_reason(
    reason: str | None,
) -> str | None:
    if reason is not None and not isinstance(
        reason,
        str,
    ):
        raise ValidationError(
            "reason must be a string"
        )

    reason = _normalize_text(
        reason
    )

    if reason and len(reason) > 255:
        raise ValidationError(
            "reason cannot exceed 255 characters"
        )

    return reason


def _validate_clinic_id(
    clinic_id: int,
) -> int:
    return _validate_positive_id(
        clinic_id,
        "clinic_id",
    )


def _validate_actor_id(
    actor_user_id: int,
) -> int:
    return _validate_positive_id(
        actor_user_id,
        "actor_user_id",
    )


def _get_ward(
    ward_id: int,
    clinic_id: int | None = None,
    lock: bool = False,
) -> Ward:
    ward_id = _validate_positive_id(
        ward_id,
        "ward_id",
    )

    statement = select(Ward).where(
        Ward.id == ward_id
    )

    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

        statement = statement.where(
            Ward.clinic_id == clinic_id
        )

    if lock:
        statement = statement.with_for_update()

    ward = db.session.execute(
        statement
    ).scalar_one_or_none()

    if ward is None:
        raise NotFoundError(
            f"Ward {ward_id} not found"
        )

    return ward


def _get_bed(
    bed_id: int,
    clinic_id: int | None = None,
    lock: bool = False,
) -> Bed:
    bed_id = _validate_positive_id(
        bed_id,
        "bed_id",
    )

    statement = (
        select(Bed)
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            Bed.id == bed_id
        )
    )

    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

        statement = statement.where(
            Ward.clinic_id == clinic_id
        )

    if lock:
        statement = statement.with_for_update()

    bed = db.session.execute(
        statement
    ).scalar_one_or_none()

    if bed is None:
        raise NotFoundError(
            f"Bed {bed_id} not found"
        )

    return bed


def _get_patient(
    patient_id: int,
    clinic_id: int | None = None,
    lock: bool = False,
) -> Patient:
    patient_id = _validate_positive_id(
        patient_id,
        "patient_id",
    )

    statement = select(Patient).where(
        Patient.id == patient_id
    )

    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

        statement = statement.where(
            Patient.clinic_id == clinic_id
        )

    if lock:
        statement = statement.with_for_update()

    patient = db.session.execute(
        statement
    ).scalar_one_or_none()

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    return patient


def _get_staff(
    staff_id: int,
    clinic_id: int | None = None,
) -> Staff:
    staff_id = _validate_positive_id(
        staff_id,
        "staff_id",
    )

    statement = select(Staff).where(
        Staff.id == staff_id
    )

    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

        statement = statement.where(
            Staff.clinic_id == clinic_id
        )

    staff = db.session.execute(
        statement
    ).scalar_one_or_none()

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    return staff


def _get_reservation(
    reservation_id: int,
    clinic_id: int | None = None,
    lock: bool = False,
) -> BedReservation:
    reservation_id = _validate_positive_id(
        reservation_id,
        "reservation_id",
    )

    statement = (
        select(BedReservation)
        .join(
            Bed,
            BedReservation.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            BedReservation.id == reservation_id
        )
    )

    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

        statement = statement.where(
            Ward.clinic_id == clinic_id
        )

    if lock:
        statement = statement.with_for_update()

    reservation = db.session.execute(
        statement
    ).scalar_one_or_none()

    if reservation is None:
        raise NotFoundError(
            f"Bed reservation {reservation_id} not found"
        )

    return reservation


def _get_admission(
    admission_id: int,
    clinic_id: int | None = None,
    lock: bool = False,
) -> Admission:
    admission_id = _validate_positive_id(
        admission_id,
        "admission_id",
    )

    statement = (
        select(Admission)
        .join(
            Bed,
            Admission.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            Admission.id == admission_id
        )
    )

    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

        statement = statement.where(
            Ward.clinic_id == clinic_id
        )

    if lock:
        statement = statement.with_for_update()

    admission = db.session.execute(
        statement
    ).scalar_one_or_none()

    if admission is None:
        raise NotFoundError(
            f"Admission {admission_id} not found"
        )

    return admission


def _validate_staff_for_clinic(
    staff_id: int,
    clinic_id: int,
) -> Staff:
    staff = _get_staff(
        staff_id,
        clinic_id=clinic_id,
    )

    if staff.clinic_id != clinic_id:
        raise ConflictError(
            f"Staff {staff_id} does not belong "
            f"to clinic {clinic_id}"
        )

    if staff.status != StaffStatus.ACTIVE:
        raise ConflictError(
            f"Staff {staff_id} is not active"
        )

    return staff


def _validate_patient_for_clinic(
    patient: Patient,
    clinic_id: int,
) -> Patient:
    if patient.clinic_id != clinic_id:
        raise ConflictError(
            f"Patient {patient.id} does not belong "
            f"to clinic {clinic_id}"
        )

    return patient


def _validate_bed_for_clinic(
    bed: Bed,
    clinic_id: int,
) -> Bed:
    if bed.ward is None:
        raise ConflictError(
            f"Bed {bed.id} is not assigned to a ward"
        )

    if bed.ward.clinic_id != clinic_id:
        raise ConflictError(
            f"Bed {bed.id} does not belong "
            f"to clinic {clinic_id}"
        )

    return bed


def _get_configured_bed_count(
    ward_id: int,
) -> int:
    ward_id = _validate_positive_id(
        ward_id,
        "ward_id",
    )

    statement = select(
        func.count(Bed.id)
    ).where(
        Bed.ward_id == ward_id
    )

    return int(
        db.session.execute(
            statement
        ).scalar_one()
        or 0
    )


def _validate_new_capacity(
    ward: Ward,
    new_capacity: int,
) -> None:
    bed_count = _get_configured_bed_count(
        ward.id
    )

    if new_capacity < bed_count:
        raise ConflictError(
            f"Capacity cannot be reduced below "
            f"the current configured bed count of "
            f"{bed_count}"
        )


def _ensure_ward_active(
    clinic_id: int,
) -> None:
    ensure_clinic_active(
        clinic_id
    )


def _ensure_admission_active(
    admission: Admission,
) -> None:
    if admission.status != AdmissionStatus.ADMITTED:
        raise ConflictError(
            f"Admission {admission.id} is currently "
            f"'{admission.status.value}'"
        )


def _ensure_reservation_pending(
    reservation: BedReservation,
) -> None:
    if reservation.status != ReservationStatus.PENDING:
        raise ConflictError(
            f"Reservation {reservation.id} is currently "
            f"'{reservation.status.value}'"
        )


def _ensure_reservation_not_expired(
    reservation: BedReservation,
) -> None:
    if (
        reservation.expires_at is not None
        and reservation.expires_at <= _db_now()
    ):
        raise ConflictError(
            f"Reservation {reservation.id} has expired"
        )


def _ensure_bed_available(
    bed: Bed,
) -> None:
    if bed.status != BedStatus.AVAILABLE:
        raise ConflictError(
            f"Bed {bed.id} is currently "
            f"'{bed.status.value}' and is not available"
        )


def _ensure_bed_occupied(
    bed: Bed,
) -> None:
    if bed.status != BedStatus.OCCUPIED:
        raise ConflictError(
            f"Bed {bed.id} is currently "
            f"'{bed.status.value}' and is not occupied"
        )


def _get_active_admission_for_patient(
    patient_id: int,
    clinic_id: int | None = None,
    lock: bool = False,
) -> Admission | None:
    patient_id = _validate_positive_id(
        patient_id,
        "patient_id",
    )

    statement = (
        select(Admission)
        .join(
            Bed,
            Admission.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            Admission.patient_id == patient_id,
            Admission.status == AdmissionStatus.ADMITTED,
        )
        .order_by(
            Admission.admitted_at.desc(),
            Admission.id.desc(),
        )
    )

    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

        statement = statement.where(
            Ward.clinic_id == clinic_id
        )

    if lock:
        statement = statement.with_for_update()

    return (
        db.session.execute(
            statement
        )
        .scalars()
        .first()
    )


def _get_active_reservation_for_patient(
    patient_id: int,
    clinic_id: int | None = None,
    lock: bool = False,
) -> BedReservation | None:
    patient_id = _validate_positive_id(
        patient_id,
        "patient_id",
    )

    statement = (
        select(BedReservation)
        .join(
            Bed,
            BedReservation.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            BedReservation.patient_id == patient_id,
            BedReservation.status == ReservationStatus.PENDING,
        )
        .order_by(
            BedReservation.reserved_at.desc(),
            BedReservation.id.desc(),
        )
    )

    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

        statement = statement.where(
            Ward.clinic_id == clinic_id
        )

    if lock:
        statement = statement.with_for_update()

    return (
        db.session.execute(
            statement
        )
        .scalars()
        .first()
    )


def _get_active_reservation_for_bed(
    bed_id: int,
    clinic_id: int | None = None,
    lock: bool = False,
) -> BedReservation | None:
    bed_id = _validate_positive_id(
        bed_id,
        "bed_id",
    )

    statement = (
        select(BedReservation)
        .join(
            Bed,
            BedReservation.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            BedReservation.bed_id == bed_id,
            BedReservation.status == ReservationStatus.PENDING,
        )
        .order_by(
            BedReservation.reserved_at.desc(),
            BedReservation.id.desc(),
        )
    )

    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

        statement = statement.where(
            Ward.clinic_id == clinic_id
        )

    if lock:
        statement = statement.with_for_update()

    return (
        db.session.execute(
            statement
        )
        .scalars()
        .first()
    )


def get_ward(
    ward_id: int,
    clinic_id: int | None = None,
) -> Ward:
    return _get_ward(
        ward_id,
        clinic_id=clinic_id,
    )


def list_wards(
    clinic_id: int,
    ward_type: WardType | str | None = None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> dict:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = select(Ward).where(
        Ward.clinic_id == clinic_id
    )

    count_statement = select(
        func.count(Ward.id)
    ).where(
        Ward.clinic_id == clinic_id
    )

    if ward_type is not None:
        ward_type = _validate_ward_type(
            ward_type
        )

        statement = statement.where(
            Ward.ward_type == ward_type
        )

        count_statement = count_statement.where(
            Ward.ward_type == ward_type
        )

    statement = statement.order_by(
        Ward.name.asc(),
        Ward.id.asc(),
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
        count_statement=count_statement,
    )


@transactional
def create_ward(
    clinic_id: int,
    name: str,
    ward_type: WardType | str | None,
    capacity: int,
    actor_user_id: int | None = None,
) -> Ward:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    if actor_user_id is not None:
        actor_user_id = _validate_actor_id(
            actor_user_id
        )

    _ensure_ward_active(
        clinic_id
    )

    if not isinstance(
        name,
        str,
    ):
        raise ValidationError(
            "Ward name must be a string"
        )

    name = _normalize_text(
        name
    )

    if not name:
        raise ValidationError(
            "Ward name is required"
        )

    if len(name) > 150:
        raise ValidationError(
            "Ward name cannot exceed 150 characters"
        )

    ward_type = _validate_ward_type(
        ward_type
    )

    capacity = _validate_ward_capacity_value(
        capacity
    )

    existing_statement = select(
        Ward.id
    ).where(
        Ward.clinic_id == clinic_id,
        Ward.name == name,
    )

    existing = db.session.execute(
        existing_statement
    ).scalar_one_or_none()

    if existing is not None:
        raise ConflictError(
            f"Ward '{name}' already exists "
            f"in clinic {clinic_id}"
        )

    ward = Ward(
        clinic_id=clinic_id,
        name=name,
        ward_type=ward_type,
        capacity=capacity,
    )

    db.session.add(
        ward
    )
    db.session.flush()

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.CREATE,
        entity_type="ward",
        entity_id=ward.id,
        clinic_id=clinic_id,
        description=(
            f"Created ward '{ward.name}' "
            f"in clinic {clinic_id}"
        ),
    )

    return ward


@transactional
def update_ward(
    ward_id: int,
    clinic_id: int,
    actor_user_id: int | None = None,
    **fields,
) -> Ward:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    if actor_user_id is not None:
        actor_user_id = _validate_actor_id(
            actor_user_id
        )

    _ensure_ward_active(
        clinic_id
    )

    ward = _get_ward(
        ward_id,
        clinic_id=clinic_id,
        lock=True,
    )

    unknown_fields = (
        set(fields)
        - _EDITABLE_WARD_FIELDS
    )

    if unknown_fields:
        raise ValidationError(
            "Unsupported ward fields: "
            + ", ".join(
                sorted(unknown_fields)
            )
        )

    if not fields:
        raise ValidationError(
            "At least one ward field must be provided"
        )

    old_value = {
        "name": ward.name,
        "ward_type": ward.ward_type.value,
        "capacity": ward.capacity,
    }

    if "name" in fields:
        name = fields["name"]

        if not isinstance(
            name,
            str,
        ):
            raise ValidationError(
                "Ward name must be a string"
            )

        name = _normalize_text(
            name
        )

        if not name:
            raise ValidationError(
                "Ward name cannot be empty"
            )

        if len(name) > 150:
            raise ValidationError(
                "Ward name cannot exceed 150 characters"
            )

        duplicate_statement = select(
            Ward.id
        ).where(
            Ward.clinic_id == clinic_id,
            Ward.name == name,
            Ward.id != ward.id,
        )

        duplicate = db.session.execute(
            duplicate_statement
        ).scalar_one_or_none()

        if duplicate is not None:
            raise ConflictError(
                f"Ward '{name}' already exists "
                f"in clinic {clinic_id}"
            )

        ward.name = name

    if "ward_type" in fields:
        ward.ward_type = _validate_ward_type(
            fields["ward_type"]
        )

    if "capacity" in fields:
        new_capacity = _validate_ward_capacity_value(
            fields["capacity"]
        )

        _validate_new_capacity(
            ward,
            new_capacity,
        )

        ward.capacity = new_capacity

    db.session.flush()

    new_value = {
        "name": ward.name,
        "ward_type": ward.ward_type.value,
        "capacity": ward.capacity,
    }

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.UPDATE,
        entity_type="ward",
        entity_id=ward.id,
        clinic_id=clinic_id,
        description=f"Updated ward {ward.id}",
        old_value=old_value,
        new_value=new_value,
    )

    return ward


def get_ward_occupancy(
    ward_id: int,
    clinic_id: int,
) -> dict:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    ward = _get_ward(
        ward_id,
        clinic_id=clinic_id,
    )

    occupied_case = case(
        (
            Bed.status == BedStatus.OCCUPIED,
            1,
        ),
        else_=0,
    )

    available_case = case(
        (
            Bed.status == BedStatus.AVAILABLE,
            1,
        ),
        else_=0,
    )

    reserved_case = case(
        (
            Bed.status == BedStatus.RESERVED,
            1,
        ),
        else_=0,
    )

    maintenance_case = case(
        (
            Bed.status == BedStatus.MAINTENANCE,
            1,
        ),
        else_=0,
    )

    statement = select(
        func.count(Bed.id),
        func.coalesce(
            func.sum(occupied_case),
            0,
        ),
        func.coalesce(
            func.sum(available_case),
            0,
        ),
        func.coalesce(
            func.sum(reserved_case),
            0,
        ),
        func.coalesce(
            func.sum(maintenance_case),
            0,
        ),
    ).where(
        Bed.ward_id == ward.id
    )

    (
        total_beds,
        occupied,
        available,
        reserved,
        maintenance,
    ) = db.session.execute(
        statement
    ).one()

    return {
        "ward_id": ward.id,
        "clinic_id": ward.clinic_id,
        "ward_name": ward.name,
        "capacity": ward.capacity,
        "total_beds": int(
            total_beds or 0
        ),
        "occupied": int(
            occupied or 0
        ),
        "available": int(
            available or 0
        ),
        "reserved": int(
            reserved or 0
        ),
        "maintenance": int(
            maintenance or 0
        ),
    }


def get_bed(
    bed_id: int,
    clinic_id: int | None = None,
) -> Bed:
    return _get_bed(
        bed_id,
        clinic_id=clinic_id,
    )


def list_beds(
    ward_id: int,
    clinic_id: int,
    status: BedStatus | str | None = None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> dict:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    ward = _get_ward(
        ward_id,
        clinic_id=clinic_id,
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = select(Bed).where(
        Bed.ward_id == ward.id
    )

    count_statement = select(
        func.count(Bed.id)
    ).where(
        Bed.ward_id == ward.id
    )

    if status is not None:
        status = _validate_bed_status(
            status
        )

        statement = statement.where(
            Bed.status == status
        )

        count_statement = count_statement.where(
            Bed.status == status
        )

    statement = statement.order_by(
        Bed.bed_number.asc(),
        Bed.id.asc(),
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
        count_statement=count_statement,
    )


@transactional
def add_bed(
    ward_id: int,
    bed_number: str,
    clinic_id: int,
    actor_user_id: int | None = None,
) -> Bed:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    if actor_user_id is not None:
        actor_user_id = _validate_actor_id(
            actor_user_id
        )

    ward = _get_ward(
        ward_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _ensure_ward_active(
        ward.clinic_id
    )

    bed_number = _validate_bed_number(
        bed_number
    )

    current_count = _get_configured_bed_count(
        ward.id
    )

    if current_count >= ward.capacity:
        raise ConflictError(
            f"Ward {ward.id} has reached "
            f"its configured capacity of "
            f"{ward.capacity}"
        )

    existing_statement = select(
        Bed.id
    ).where(
        Bed.ward_id == ward.id,
        Bed.bed_number == bed_number,
    )

    existing = db.session.execute(
        existing_statement
    ).scalar_one_or_none()

    if existing is not None:
        raise ConflictError(
            f"Bed '{bed_number}' already exists "
            f"in ward {ward.id}"
        )

    bed = Bed(
        ward_id=ward.id,
        bed_number=bed_number,
        status=BedStatus.AVAILABLE,
    )

    db.session.add(
        bed
    )
    db.session.flush()

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.CREATE,
        entity_type="bed",
        entity_id=bed.id,
        clinic_id=clinic_id,
        description=(
            f"Added bed '{bed.bed_number}' "
            f"to ward {ward.id}"
        ),
    )

    return bed


@transactional
def set_bed_maintenance(
    bed_id: int,
    under_maintenance: bool,
    clinic_id: int,
    actor_user_id: int | None = None,
) -> Bed:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    if actor_user_id is not None:
        actor_user_id = _validate_actor_id(
            actor_user_id
        )

    if not isinstance(
        under_maintenance,
        bool,
    ):
        raise ValidationError(
            "under_maintenance must be boolean"
        )

    bed = _get_bed(
        bed_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _validate_bed_for_clinic(
        bed,
        clinic_id,
    )

    _ensure_ward_active(
        clinic_id
    )

    old_status = bed.status

    if under_maintenance:
        if bed.status in (
            BedStatus.OCCUPIED,
            BedStatus.RESERVED,
        ):
            raise ConflictError(
                f"Bed {bed.id} cannot be placed "
                f"under maintenance while "
                f"'{bed.status.value}'"
            )

        bed.status = BedStatus.MAINTENANCE

    else:
        if bed.status != BedStatus.MAINTENANCE:
            raise ConflictError(
                f"Bed {bed.id} is not under maintenance"
            )

        bed.status = BedStatus.AVAILABLE

    db.session.flush()

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.UPDATE,
        entity_type="bed",
        entity_id=bed.id,
        clinic_id=clinic_id,
        description=(
            f"Changed bed {bed.id} maintenance status"
        ),
        old_value={
            "status": old_status.value,
        },
        new_value={
            "status": bed.status.value,
        },
    )

    return bed


def get_bed_reservation(
    reservation_id: int,
    clinic_id: int | None = None,
) -> BedReservation:
    return _get_reservation(
        reservation_id,
        clinic_id=clinic_id,
    )


def list_bed_reservations(
    clinic_id: int,
    status: ReservationStatus | str | None = None,
    patient_id: int | None = None,
    bed_id: int | None = None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> dict:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = (
        select(BedReservation)
        .join(
            Bed,
            BedReservation.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            Ward.clinic_id == clinic_id
        )
    )

    count_statement = (
        select(
            func.count(BedReservation.id)
        )
        .select_from(BedReservation)
        .join(
            Bed,
            BedReservation.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            Ward.clinic_id == clinic_id
        )
    )

    if status is not None:
        status = _validate_reservation_status(
            status
        )

        statement = statement.where(
            BedReservation.status == status
        )

        count_statement = count_statement.where(
            BedReservation.status == status
        )

    if patient_id is not None:
        patient_id = _validate_positive_id(
            patient_id,
            "patient_id",
        )

        statement = statement.where(
            BedReservation.patient_id == patient_id
        )

        count_statement = count_statement.where(
            BedReservation.patient_id == patient_id
        )

    if bed_id is not None:
        bed_id = _validate_positive_id(
            bed_id,
            "bed_id",
        )

        statement = statement.where(
            BedReservation.bed_id == bed_id
        )

        count_statement = count_statement.where(
            BedReservation.bed_id == bed_id
        )

    statement = statement.order_by(
        BedReservation.reserved_at.desc(),
        BedReservation.id.desc(),
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
        count_statement=count_statement,
    )


def get_active_bed_reservation_for_patient(
    patient_id: int,
    clinic_id: int,
) -> BedReservation | None:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    _get_patient(
        patient_id,
        clinic_id=clinic_id,
    )

    return _get_active_reservation_for_patient(
        patient_id,
        clinic_id=clinic_id,
    )


def get_active_bed_reservation_for_bed(
    bed_id: int,
    clinic_id: int,
) -> BedReservation | None:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    _get_bed(
        bed_id,
        clinic_id=clinic_id,
    )

    return _get_active_reservation_for_bed(
        bed_id,
        clinic_id=clinic_id,
    )


@transactional
def reserve_bed(
    patient_id: int,
    bed_id: int,
    reserved_by_id: int,
    clinic_id: int,
    reason: str | None = None,
    expires_at: datetime | None = None,
    actor_user_id: int | None = None,
) -> BedReservation:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    reserved_by_id = _validate_positive_id(
        reserved_by_id,
        "reserved_by_id",
    )

    if actor_user_id is None:
        actor_user_id = reserved_by_id

    actor_user_id = _validate_actor_id(
        actor_user_id
    )

    _ensure_ward_active(
        clinic_id
    )

    patient = _get_patient(
        patient_id,
        clinic_id=clinic_id,
        lock=True,
    )

    bed = _get_bed(
        bed_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _validate_patient_for_clinic(
        patient,
        clinic_id,
    )

    _validate_bed_for_clinic(
        bed,
        clinic_id,
    )

    staff = _validate_staff_for_clinic(
        reserved_by_id,
        clinic_id,
    )

    if staff.user_id != actor_user_id:
        raise ConflictError(
            "Reservation actor does not match "
            "the authenticated user"
        )

    expires_at = _normalize_db_datetime(
        expires_at
    )

    if (
        expires_at is not None
        and expires_at <= _db_now()
    ):
        raise ValidationError(
            "expires_at must be in the future"
        )

    reason = _validate_reason(
        reason
    )

    active_admission = (
        _get_active_admission_for_patient(
            patient.id,
            clinic_id=clinic_id,
            lock=True,
        )
    )

    if active_admission is not None:
        raise ConflictError(
            f"Patient {patient.id} already has "
            f"an active admission"
        )

    active_reservation = (
        _get_active_reservation_for_patient(
            patient.id,
            clinic_id=clinic_id,
            lock=True,
        )
    )

    if active_reservation is not None:
        raise ConflictError(
            f"Patient {patient.id} already has "
            f"an active bed reservation"
        )

    active_bed_reservation = (
        _get_active_reservation_for_bed(
            bed.id,
            clinic_id=clinic_id,
            lock=True,
        )
    )

    if active_bed_reservation is not None:
        raise ConflictError(
            f"Bed {bed.id} already has "
            f"an active reservation"
        )

    _ensure_bed_available(
        bed
    )

    reservation = BedReservation(
        patient_id=patient.id,
        bed_id=bed.id,
        reserved_by_id=staff.id,
        status=ReservationStatus.PENDING,
        reason=reason,
        reserved_at=_db_now(),
        expires_at=expires_at,
    )

    db.session.add(
        reservation
    )

    bed.status = BedStatus.RESERVED

    db.session.flush()

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.CREATE,
        entity_type="bed_reservation",
        entity_id=reservation.id,
        clinic_id=clinic_id,
        description=(
            f"Reserved bed {bed.id} "
            f"for patient {patient.id}"
        ),
    )

    return reservation


@transactional
def cancel_bed_reservation(
    reservation_id: int,
    clinic_id: int,
    reason: str | None = None,
    actor_user_id: int | None = None,
) -> BedReservation:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    if actor_user_id is not None:
        actor_user_id = _validate_actor_id(
            actor_user_id
        )

    _ensure_ward_active(
        clinic_id
    )

    reservation = _get_reservation(
        reservation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _ensure_reservation_pending(
        reservation
    )

    bed = _get_bed(
        reservation.bed_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _validate_bed_for_clinic(
        bed,
        clinic_id,
    )

    reason = _validate_reason(
        reason
    )

    old_status = reservation.status

    reservation.status = (
        ReservationStatus.CANCELLED
    )
    reservation.cancelled_at = _db_now()

    if reason:
        reservation.reason = reason

    if bed.status == BedStatus.RESERVED:
        bed.status = BedStatus.AVAILABLE

    db.session.flush()

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.UPDATE,
        entity_type="bed_reservation",
        entity_id=reservation.id,
        clinic_id=clinic_id,
        description=(
            f"Cancelled bed reservation "
            f"{reservation.id}"
        ),
        old_value={
            "status": old_status.value,
        },
        new_value={
            "status": reservation.status.value,
            "cancelled_at": (
                reservation.cancelled_at.isoformat()
                if reservation.cancelled_at
                else None
            ),
        },
    )

    return reservation


@transactional
def expire_bed_reservation(
    reservation_id: int,
) -> BedReservation:
    reservation = _get_reservation(
        reservation_id,
        lock=True,
    )

    if reservation.status != ReservationStatus.PENDING:
        return reservation

    now = _db_now()

    if (
        reservation.expires_at is None
        or reservation.expires_at > now
    ):
        return reservation

    bed = _get_bed(
        reservation.bed_id,
        lock=True,
    )

    reservation.status = (
        ReservationStatus.EXPIRED
    )

    if bed.status == BedStatus.RESERVED:
        bed.status = BedStatus.AVAILABLE

    db.session.flush()

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="bed_reservation",
        entity_id=reservation.id,
        clinic_id=reservation.bed.ward.clinic_id,
        description=(
            f"Expired bed reservation "
            f"{reservation.id}"
        ),
        new_value={
            "status": reservation.status.value,
        },
    )

    return reservation


@transactional
def expire_due_bed_reservations(
    clinic_id: int | None = None,
) -> list[BedReservation]:
    if clinic_id is not None:
        clinic_id = _validate_clinic_id(
            clinic_id
        )

    now = _db_now()

    statement = (
        select(BedReservation)
        .join(
            Bed,
            BedReservation.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            BedReservation.status
            == ReservationStatus.PENDING,
            BedReservation.expires_at.isnot(None),
            BedReservation.expires_at <= now,
        )
        .order_by(
            BedReservation.expires_at.asc(),
            BedReservation.id.asc(),
        )
        .with_for_update()
    )

    if clinic_id is not None:
        statement = statement.where(
            Ward.clinic_id == clinic_id
        )

    reservations = (
        db.session.execute(
            statement
        )
        .scalars()
        .all()
    )

    for reservation in reservations:
        bed = _get_bed(
            reservation.bed_id,
            clinic_id=clinic_id,
            lock=True,
        )

        reservation.status = (
            ReservationStatus.EXPIRED
        )

        if bed.status == BedStatus.RESERVED:
            bed.status = BedStatus.AVAILABLE

        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="bed_reservation",
            entity_id=reservation.id,
            clinic_id=clinic_id,
            description=(
                f"Expired bed reservation "
                f"{reservation.id}"
            ),
            new_value={
                "status": reservation.status.value,
            },
        )

    db.session.flush()

    return reservations


def get_admission(
    admission_id: int,
    clinic_id: int | None = None,
) -> Admission:
    return _get_admission(
        admission_id,
        clinic_id=clinic_id,
    )


def get_active_admission_for_patient(
    patient_id: int,
    clinic_id: int,
) -> Admission | None:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    _get_patient(
        patient_id,
        clinic_id=clinic_id,
    )

    return _get_active_admission_for_patient(
        patient_id,
        clinic_id=clinic_id,
    )


def list_admissions_for_patient(
    patient_id: int,
    clinic_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> dict:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    patient_id = _validate_positive_id(
        patient_id,
        "patient_id",
    )

    _get_patient(
        patient_id,
        clinic_id=clinic_id,
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = (
        select(Admission)
        .join(
            Bed,
            Admission.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            Admission.patient_id == patient_id,
            Ward.clinic_id == clinic_id,
        )
        .order_by(
            Admission.admitted_at.desc(),
            Admission.id.desc(),
        )
    )

    count_statement = (
        select(
            func.count(Admission.id)
        )
        .select_from(Admission)
        .join(
            Bed,
            Admission.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            Admission.patient_id == patient_id,
            Ward.clinic_id == clinic_id,
        )
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
        count_statement=count_statement,
    )


def get_current_bed(
    patient_id: int,
    clinic_id: int,
) -> Bed | None:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    patient_id = _validate_positive_id(
        patient_id,
        "patient_id",
    )

    admission = get_active_admission_for_patient(
        patient_id,
        clinic_id,
    )

    if admission is None:
        return None

    return admission.bed


@transactional
def admit_patient(
    patient_id: int,
    bed_id: int,
    admitted_by_id: int,
    clinic_id: int,
    reason: str | None = None,
    actor_user_id: int | None = None,
) -> Admission:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    admitted_by_id = _validate_positive_id(
        admitted_by_id,
        "admitted_by_id",
    )

    if actor_user_id is None:
        actor_user_id = admitted_by_id

    actor_user_id = _validate_actor_id(
        actor_user_id
    )

    _ensure_ward_active(
        clinic_id
    )

    patient = _get_patient(
        patient_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _validate_patient_for_clinic(
        patient,
        clinic_id,
    )

    active_admission = (
        _get_active_admission_for_patient(
            patient.id,
            clinic_id=clinic_id,
            lock=True,
        )
    )

    if active_admission is not None:
        raise ConflictError(
            f"Patient {patient.id} already has "
            f"an active admission"
        )

    active_reservation = (
        _get_active_reservation_for_patient(
            patient.id,
            clinic_id=clinic_id,
            lock=True,
        )
    )

    if active_reservation is not None:
        raise ConflictError(
            f"Patient {patient.id} has an active "
            f"reservation that must be fulfilled "
            f"or cancelled first"
        )

    bed = _get_bed(
        bed_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _validate_bed_for_clinic(
        bed,
        clinic_id,
    )

    _ensure_bed_available(
        bed
    )

    staff = _validate_staff_for_clinic(
        admitted_by_id,
        clinic_id,
    )

    if staff.user_id != actor_user_id:
        raise ConflictError(
            "Admission actor does not match "
            "the authenticated user"
        )

    reason = _validate_reason(
        reason
    )

    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=staff.id,
        reservation_id=None,
        status=AdmissionStatus.ADMITTED,
        reason=reason,
        admitted_at=_db_now(),
    )

    db.session.add(
        admission
    )

    bed.status = BedStatus.OCCUPIED

    db.session.flush()

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.CREATE,
        entity_type="admission",
        entity_id=admission.id,
        clinic_id=clinic_id,
        description=(
            f"Admitted patient {patient.id} "
            f"to bed {bed.id}"
        ),
    )

    return admission


@transactional
def admit_patient_from_reservation(
    reservation_id: int,
    admitted_by_id: int,
    clinic_id: int,
    reason: str | None = None,
    actor_user_id: int | None = None,
) -> Admission:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    admitted_by_id = _validate_positive_id(
        admitted_by_id,
        "admitted_by_id",
    )

    if actor_user_id is None:
        actor_user_id = admitted_by_id

    actor_user_id = _validate_actor_id(
        actor_user_id
    )

    _ensure_ward_active(
        clinic_id
    )

    reservation = _get_reservation(
        reservation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _ensure_reservation_pending(
        reservation
    )

    _ensure_reservation_not_expired(
        reservation
    )

    patient = _get_patient(
        reservation.patient_id,
        clinic_id=clinic_id,
        lock=True,
    )

    bed = _get_bed(
        reservation.bed_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _validate_patient_for_clinic(
        patient,
        clinic_id,
    )

    _validate_bed_for_clinic(
        bed,
        clinic_id,
    )

    staff = _validate_staff_for_clinic(
        admitted_by_id,
        clinic_id,
    )

    if staff.user_id != actor_user_id:
        raise ConflictError(
            "Admission actor does not match "
            "the authenticated user"
        )

    active_admission = (
        _get_active_admission_for_patient(
            patient.id,
            clinic_id=clinic_id,
            lock=True,
        )
    )

    if active_admission is not None:
        raise ConflictError(
            f"Patient {patient.id} already has "
            f"an active admission"
        )

    active_reservation = (
        _get_active_reservation_for_bed(
            bed.id,
            clinic_id=clinic_id,
            lock=True,
        )
    )

    if (
        active_reservation is None
        or active_reservation.id != reservation.id
    ):
        raise ConflictError(
            f"Reservation {reservation.id} "
            f"is no longer the active reservation "
            f"for bed {bed.id}"
        )

    if bed.status != BedStatus.RESERVED:
        raise ConflictError(
            f"Bed {bed.id} is currently "
            f"'{bed.status.value}' and is not reserved"
        )

    reason = _validate_reason(
        reason
    )

    now = _db_now()

    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=staff.id,
        reservation_id=reservation.id,
        status=AdmissionStatus.ADMITTED,
        reason=reason or reservation.reason,
        admitted_at=now,
    )

    db.session.add(
        admission
    )

    reservation.status = (
        ReservationStatus.FULFILLED
    )
    reservation.fulfilled_at = now

    bed.status = BedStatus.OCCUPIED

    db.session.flush()

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.CREATE,
        entity_type="admission",
        entity_id=admission.id,
        clinic_id=clinic_id,
        description=(
            f"Admitted patient {patient.id} "
            f"from reservation {reservation.id}"
        ),
    )

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.UPDATE,
        entity_type="bed_reservation",
        entity_id=reservation.id,
        clinic_id=clinic_id,
        description=(
            f"Fulfilled bed reservation "
            f"{reservation.id}"
        ),
    )

    return admission


fulfill_bed_reservation = (
    admit_patient_from_reservation
)


@transactional
def transfer_bed(
    admission_id: int,
    to_bed_id: int,
    clinic_id: int,
    reason: str | None = None,
    actor_user_id: int | None = None,
) -> WardTransfer:
    clinic_id = _validate_clinic_id(
        clinic_id
    )

    to_bed_id = _validate_positive_id(
        to_bed_id,
        "to_bed_id",
    )

    if actor_user_id is not None:
        actor_user_id = _validate_actor_id(
            actor_user_id
        )

    _ensure_ward_active(
        clinic_id
    )

    admission = _get_admission(
        admission_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _ensure_admission_active(
        admission
    )

    source_bed_id = admission.bed_id

    if source_bed_id == to_bed_id:
        raise ValidationError(
            "Source and destination beds "
            "must be different"
        )

    first_bed_id, second_bed_id = sorted(
        (
            source_bed_id,
            to_bed_id,
        )
    )

    first_bed = _get_bed(
        first_bed_id,
        clinic_id=clinic_id,
        lock=True,
    )

    second_bed = _get_bed(
        second_bed_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if first_bed.id == source_bed_id:
        from_bed = first_bed
        to_bed = second_bed
    else:
        from_bed = second_bed
        to_bed = first_bed

    _validate_bed_for_clinic(
        from_bed,
        clinic_id,
    )

    _validate_bed_for_clinic(
        to_bed,
        clinic_id,
    )

    if from_bed.status != BedStatus.OCCUPIED:
        raise ConflictError(
            f"Source bed {from_bed.id} is not occupied"
        )

    _ensure_bed_available(
        to_bed
    )

    reason = _validate_reason(
        reason
    )

    transfer = WardTransfer(
        admission_id=admission.id,
        from_bed_id=from_bed.id,
        to_bed_id=to_bed.id,
        reason=reason,
        transferred_at=_db_now(),
    )

    db.session.add(
        transfer
    )

    from_bed.status = BedStatus.AVAILABLE
    to_bed.status = BedStatus.OCCUPIED
    admission.bed_id = to_bed.id

    db.session.flush()

    create_audit_log(
        user_id=actor_user_id,
        action=AuditAction.UPDATE,
        entity_type="admission",
        entity_id=admission.id,
        clinic_id=clinic_id,
        description=(
            f"Transferred admission {admission.id} "
            f"from bed {from_bed.id} "
            f"to bed {to_bed.id}"
        ),
        old_value={
            "bed_id": from_bed.id,
        },
        new_value={
            "bed_id": to_bed.id,
        },
    )

    return transfer

@transactional
def discharge_patient(
    admission_id: int,
    clinic_id: int,
    reason: str | None = None,
    actor_user_id: int | None = None,
    actor_staff_id: int | None = None,
    primary_diagnosis: str | None = None,
    secondary_diagnoses: list[str] | None = None,
    condition_at_discharge: str | None = None,
    instructions: str | None = None,
    follow_up_plan: str | None = None,
    destination: str | None = None,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> Admission:
    from app.modules.ward.services.discharge_service import (
        complete_clinical_discharge,
    )

    admission, _summary = complete_clinical_discharge(
        admission_id=admission_id,
        clinic_id=clinic_id,
        actor_staff_id=actor_staff_id,
        actor_user_id=actor_user_id,
        reason=reason,
        primary_diagnosis=primary_diagnosis,
        secondary_diagnoses=list(
            secondary_diagnoses or []
        ),
        condition_at_discharge=condition_at_discharge,
        instructions=instructions,
        follow_up_plan=follow_up_plan,
        destination=destination,
        idempotency_key=idempotency_key,
        idempotency_user_id=idempotency_user_id,
    )

    return admission
