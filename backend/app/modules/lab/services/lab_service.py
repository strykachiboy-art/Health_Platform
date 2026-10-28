from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.core.utils.decorators import transactional
from app.core.utils.qrcode_util import generate_tracking_code
from app.core.exceptions import (
    NotFoundError,
    ValidationError,
    ConflictError,
)
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.lab_enums import LabOrderStatus, LabResultFlag

from app.modules.lab.models.lab_model import (
    LabTest,
    LabOrder,
    LabOrderItem,
)

from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff


# ============================================================================
# CONSTANTS
# ============================================================================

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500

_EDITABLE_LAB_TEST_FIELDS = {
    "name",
    "loinc_code",
    "code",
    "sample_type",
    "reference_range",
    "unit",
    "price",
    "critical_low",
    "critical_high",
    "is_active",
}


# ============================================================================
# INTERNAL HELPERS
# ============================================================================

def _utcnow() -> datetime:
    """
    Return the current UTC time as a timezone-aware datetime.
    """
    return datetime.now(timezone.utc)


def _serialize_value(value: Any) -> Any:
    """
    Convert common SQLAlchemy/Python values into audit-safe values.
    """
    if value is None:
        return None

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if hasattr(value, "value"):
        return value.value

    if isinstance(value, dict):
        return {
            key: _serialize_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _serialize_value(item)
            for item in value
        ]

    return value


def _validate_positive_id(
    value: int,
    field_name: str,
) -> int:
    """
    Validate and return a strict positive integer identifier.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    if value <= 0:
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _validate_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    """
    Validate pagination parameters and return them unchanged.
    """
    if isinstance(page, bool) or not isinstance(page, int):
        raise ValidationError(
            "Page must be an integer"
        )

    if isinstance(per_page, bool) or not isinstance(per_page, int):
        raise ValidationError(
            "per_page must be an integer"
        )

    if page < 1:
        raise ValidationError(
            "Page must be greater than or equal to 1"
        )

    if per_page < 1:
        raise ValidationError(
            "per_page must be greater than zero"
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
):
    """
    Execute a SQLAlchemy statement using bounded pagination.

    error_out=False intentionally allows an empty final/out-of-range
    page rather than raising a 404.
    """
    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    return db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )


# ============================================================================
# PATIENT / STAFF HELPERS
# ============================================================================

def _get_patient(
    patient_id: int,
    clinic_id: int,
) -> Patient:
    """
    Retrieve a patient strictly within the authenticated clinic.
    """
    patient_id = _validate_positive_id(
        patient_id,
        "patient_id",
    )

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = (
        select(Patient)
        .where(
            Patient.id == patient_id,
            Patient.clinic_id == clinic_id,
        )
    )

    patient = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    return patient


def _get_staff(
    staff_id: int,
    clinic_id: int,
    *,
    require_active: bool = False,
) -> Staff:
    """
    Retrieve staff strictly within the authenticated clinic.
    """
    staff_id = _validate_positive_id(
        staff_id,
        "staff_id",
    )

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = (
        select(Staff)
        .where(
            Staff.id == staff_id,
            Staff.clinic_id == clinic_id,
        )
    )

    staff = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    if require_active:
        _validate_staff_active(staff)

    return staff


def _validate_patient_clinic(
    patient: Patient,
    clinic_id: int,
) -> None:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    if patient.clinic_id != clinic_id:
        raise ValidationError(
            f"Patient {patient.id} does not belong "
            f"to clinic {clinic_id}"
        )

    if not patient.is_active:
        raise ValidationError(
            f"Patient {patient.id} is inactive"
        )


def _validate_staff_clinic(
    staff: Staff,
    clinic_id: int,
) -> None:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    if staff.clinic_id != clinic_id:
        raise ValidationError(
            f"Staff {staff.id} does not belong "
            f"to clinic {clinic_id}"
        )


def _validate_staff_active(
    staff: Staff,
) -> None:
    from app.core.enums.staff_enums import StaffStatus

    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError(
            f"Staff {staff.id} is not active"
        )


# ============================================================================
# CONSULTATION HELPERS
# ============================================================================

def _get_consultation(
    consultation_id: int,
    clinic_id: int,
):
    """
    Retrieve consultation strictly within the authenticated clinic.
    """
    consultation_id = _validate_positive_id(
        consultation_id,
        "consultation_id",
    )

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    from app.modules.consultation.models.consultation_model import (
        Consultation,
    )

    statement = (
        select(Consultation)
        .where(
            Consultation.id == consultation_id,
            Consultation.clinic_id == clinic_id,
        )
    )

    consultation = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if consultation is None:
        raise NotFoundError(
            f"Consultation {consultation_id} not found"
        )

    return consultation


def _validate_consultation(
    consultation,
    clinic_id: int,
    patient_id: int,
) -> None:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    patient_id = _validate_positive_id(
        patient_id,
        "patient_id",
    )

    if consultation.clinic_id != clinic_id:
        raise ValidationError(
            f"Consultation {consultation.id} does not belong "
            f"to clinic {clinic_id}"
        )

    if consultation.patient_id != patient_id:
        raise ValidationError(
            f"Consultation {consultation.id} does not belong "
            f"to patient {patient_id}"
        )


# ============================================================================
# LAB TEST HELPERS
# ============================================================================

def _get_lab_test(
    test_id: int,
    clinic_id: int | None = None,
) -> LabTest:
    """
    Retrieve a laboratory test.

    When clinic_id is supplied:
        - clinic-specific tests are allowed
        - global catalog tests are allowed

    When clinic_id is None:
        - only global catalog tests are allowed
    """
    test_id = _validate_positive_id(
        test_id,
        "test_id",
    )

    statement = (
        select(LabTest)
        .where(
            LabTest.id == test_id
        )
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        statement = statement.where(
            or_(
                LabTest.clinic_id == clinic_id,
                LabTest.clinic_id.is_(None),
            )
        )
    else:
        statement = statement.where(
            LabTest.clinic_id.is_(None)
        )

    test = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if test is None:
        raise NotFoundError(
            f"Lab test {test_id} not found"
        )

    return test


def _get_lab_order(
    order_id: int,
    clinic_id: int,
    *,
    for_update: bool = False,
) -> LabOrder:
    """
    Retrieve a lab order strictly within the authenticated clinic.

    Write operations should use for_update=True to prevent
    concurrent state transitions.
    """
    order_id = _validate_positive_id(
        order_id,
        "order_id",
    )

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = (
        select(LabOrder)
        .where(
            LabOrder.id == order_id,
            LabOrder.clinic_id == clinic_id,
        )
    )

    if for_update:
        statement = statement.with_for_update()

    order = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if order is None:
        raise NotFoundError(
            f"Lab order {order_id} not found"
        )

    return order


def _get_lab_order_item(
    order_item_id: int,
    clinic_id: int,
    *,
    for_update: bool = False,
) -> LabOrderItem:
    """
    Retrieve an order item only when its parent order belongs
    to the authenticated clinic.
    """
    order_item_id = _validate_positive_id(
        order_item_id,
        "order_item_id",
    )

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = (
        select(LabOrderItem)
        .join(
            LabOrder,
            LabOrder.id == LabOrderItem.order_id,
        )
        .where(
            LabOrderItem.id == order_item_id,
            LabOrder.clinic_id == clinic_id,
        )
    )

    if for_update:
        statement = statement.with_for_update()

    item = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if item is None:
        raise NotFoundError(
            f"Lab order item {order_item_id} not found"
        )

    return item


def _validate_actor_for_order(
    actor_id: int,
    order: LabOrder,
) -> Staff:
    """
    Validate that an operational actor:

    - exists
    - belongs to the order's clinic
    - is active
    """
    actor = _get_staff(
        actor_id,
        order.clinic_id,
        require_active=True,
    )

    _validate_staff_clinic(
        actor,
        order.clinic_id,
    )

    return actor


def _assert_status(
    order: LabOrder,
    *allowed: LabOrderStatus,
) -> None:
    if order.status not in allowed:
        raise ConflictError(
            f"Lab order {order.id} is "
            f"'{order.status.value}', expected one of "
            f"{[status.value for status in allowed]}"
        )


def _validate_actor_pair(
    order: LabOrder,
    actor_field: str,
    timestamp_field: str,
) -> None:
    """
    Ensure actor/timestamp fields are synchronized.
    """
    actor_id = getattr(order, actor_field)
    timestamp = getattr(order, timestamp_field)

    if (actor_id is None) != (timestamp is None):
        raise ValidationError(
            f"{actor_field} and {timestamp_field} "
            "must either both be set or both be null"
        )


# ============================================================================
# LAB TEST CATALOG
# ============================================================================

def get_lab_test(
    test_id: int,
    clinic_id: int | None = None,
) -> LabTest:
    return _get_lab_test(
        test_id,
        clinic_id,
    )


def list_lab_tests(
    clinic_id: int | None = None,
    active_only: bool = True,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    """
    clinic_id=None:
        Return global catalog entries only.

    clinic_id=<id>:
        Return global entries plus clinic-specific entries.

    Results are deterministically ordered and paginated.
    """
    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = select(LabTest)

    if clinic_id is not None:
        statement = statement.where(
            or_(
                LabTest.clinic_id == clinic_id,
                LabTest.clinic_id.is_(None),
            )
        )
    else:
        statement = statement.where(
            LabTest.clinic_id.is_(None)
        )

    if active_only:
        statement = statement.where(
            LabTest.is_active.is_(True)
        )

    statement = statement.order_by(
        LabTest.name.asc(),
        LabTest.id.asc(),
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
    )


@transactional
def create_lab_test(
    name: str,
    clinic_id: int | None = None,
    **fields,
) -> LabTest:
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(
            "Lab test name is required"
        )

    unknown = set(fields) - _EDITABLE_LAB_TEST_FIELDS

    if unknown:
        raise ValidationError(
            "Unknown lab test field(s): "
            f"{', '.join(sorted(unknown))}"
        )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        ensure_clinic_active(clinic_id)

    name = name.strip()

    code = fields.get("code")

    if code is not None:
        if not isinstance(code, str):
            raise ValidationError(
                "Lab test code must be a string"
            )

        code = code.strip()
        fields["code"] = code or None

    if code:
        statement = (
            select(LabTest)
            .where(
                LabTest.code == code
            )
        )

        existing = (
            db.session.execute(statement)
            .scalars()
            .first()
        )

        if existing:
            raise ConflictError(
                f"Lab test code '{code}' already exists"
            )

    critical_low = fields.get("critical_low")
    critical_high = fields.get("critical_high")

    if (
        critical_low is not None
        and critical_high is not None
        and critical_low >= critical_high
    ):
        raise ValidationError(
            "critical_low must be less than critical_high"
        )

    price = fields.get("price")

    if price is not None and price < 0:
        raise ValidationError(
            "Lab test price cannot be negative"
        )

    test = LabTest(
        clinic_id=clinic_id,
        name=name,
        **fields,
    )

    db.session.add(test)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="LabTest",
        entity_id=test.id,
        clinic_id=clinic_id,
        description=(
            f"Lab test '{test.name}' added to catalog"
        ),
        new_value={
            "clinic_id": clinic_id,
            "name": test.name,
            "code": test.code,
        },
    )

    return test


@transactional
def update_lab_test(
    test_id: int,
    clinic_id: int | None = None,
    **fields,
) -> LabTest:
    test_id = _validate_positive_id(
        test_id,
        "test_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

    test = _get_lab_test(
        test_id,
        clinic_id,
    )

    if test.clinic_id is not None:
        ensure_clinic_active(
            test.clinic_id
        )

    unknown = set(fields) - _EDITABLE_LAB_TEST_FIELDS

    if unknown:
        raise ValidationError(
            "Unknown lab test field(s): "
            f"{', '.join(sorted(unknown))}"
        )

    if "name" in fields:
        if (
            fields["name"] is None
            or not isinstance(fields["name"], str)
            or not fields["name"].strip()
        ):
            raise ValidationError(
                "Lab test name cannot be empty"
            )

        fields["name"] = fields["name"].strip()

    if "code" in fields:
        code = fields["code"]

        if code is not None:
            if not isinstance(code, str):
                raise ValidationError(
                    "Lab test code must be a string"
                )

            code = code.strip()

        fields["code"] = code or None

        if code:
            statement = (
                select(LabTest)
                .where(
                    LabTest.code == code,
                    LabTest.id != test.id,
                )
            )

            existing = (
                db.session.execute(statement)
                .scalars()
                .first()
            )

            if existing:
                raise ConflictError(
                    f"Lab test code '{code}' already exists"
                )

    new_low = fields.get(
        "critical_low",
        test.critical_low,
    )

    new_high = fields.get(
        "critical_high",
        test.critical_high,
    )

    if (
        new_low is not None
        and new_high is not None
        and new_low >= new_high
    ):
        raise ValidationError(
            "critical_low must be less than critical_high"
        )

    if (
        "price" in fields
        and fields["price"] is not None
        and fields["price"] < 0
    ):
        raise ValidationError(
            "Lab test price cannot be negative"
        )

    old_value = {}
    new_value = {}

    for key, new_val in fields.items():
        current_val = getattr(
            test,
            key,
        )

        if current_val != new_val:
            old_value[key] = _serialize_value(
                current_val
            )

            new_value[key] = _serialize_value(
                new_val
            )

            setattr(
                test,
                key,
                new_val,
            )

    if new_value:
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="LabTest",
            entity_id=test.id,
            clinic_id=clinic_id,
            description=(
                f"Lab test '{test.name}' updated"
            ),
            old_value=old_value,
            new_value=new_value,
        )

    return test


# ============================================================================
# LAB ORDERS
# ============================================================================

def get_lab_order(
    order_id: int,
    clinic_id: int,
) -> LabOrder:
    return _get_lab_order(
        order_id,
        clinic_id,
    )


def list_orders_for_patient(
    patient_id: int,
    clinic_id: int,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    """
    Tenant-scoped patient order lookup.

    clinic_id is mandatory.

    Results are deterministically ordered and paginated.
    """
    patient_id = _validate_positive_id(
        patient_id,
        "patient_id",
    )

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    _get_patient(
        patient_id,
        clinic_id,
    )

    statement = (
        select(LabOrder)
        .options(
            selectinload(
                LabOrder.items,
            ).selectinload(
                LabOrderItem.test,
            )
        )
        .where(
            LabOrder.patient_id == patient_id,
            LabOrder.clinic_id == clinic_id,
        )
        .order_by(
            LabOrder.created_at.desc(),
            LabOrder.id.desc(),
        )
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
    )


# ============================================================================
# QR CODE HELPERS
# ============================================================================

def _generate_qr_code() -> str:
    return generate_tracking_code(
        prefix="LAB"
    )


def _generate_unique_qr_code() -> str:
    """
    Application-level collision avoidance.

    The database unique constraint remains the final
    protection against concurrent collisions.
    """
    for _ in range(10):
        qr_code = _generate_qr_code()

        statement = (
            select(LabOrder)
            .where(
                LabOrder.qr_code == qr_code
            )
        )

        existing = (
            db.session.execute(statement)
            .scalars()
            .first()
        )

        if existing is None:
            return qr_code

    raise ConflictError(
        "Could not generate a unique QR code, "
        "try again"
    )


# ============================================================================
# CREATE LAB ORDER
# ============================================================================

@transactional
def create_lab_order(
    clinic_id: int,
    patient_id: int,
    ordered_by_id: int,
    test_ids: list[int],
    consultation_id: int | None = None,
) -> LabOrder:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    patient_id = _validate_positive_id(
        patient_id,
        "patient_id",
    )

    ordered_by_id = _validate_positive_id(
        ordered_by_id,
        "ordered_by_id",
    )

    if consultation_id is not None:
        consultation_id = _validate_positive_id(
            consultation_id,
            "consultation_id",
        )

    ensure_clinic_active(
        clinic_id
    )

    if not test_ids:
        raise ValidationError(
            "A lab order must include at least one test"
        )

    if len(test_ids) != len(set(test_ids)):
        raise ValidationError(
            "Duplicate test IDs are not allowed"
        )

    if any(
        isinstance(test_id, bool)
        or not isinstance(test_id, int)
        or test_id <= 0
        for test_id in test_ids
    ):
        raise ValidationError(
            "All test IDs must be positive integers"
        )

    patient = _get_patient(
        patient_id,
        clinic_id,
    )

    _validate_patient_clinic(
        patient,
        clinic_id,
    )

    staff = _get_staff(
        ordered_by_id,
        clinic_id,
        require_active=True,
    )

    _validate_staff_clinic(
        staff,
        clinic_id,
    )

    consultation = None

    if consultation_id is not None:
        consultation = _get_consultation(
            consultation_id,
            clinic_id,
        )

        _validate_consultation(
            consultation,
            clinic_id,
            patient_id,
        )

    statement = (
        select(LabTest)
        .where(
            LabTest.id.in_(test_ids)
        )
    )

    tests = list(
        db.session.execute(statement)
        .scalars()
        .all()
    )

    found_ids = {
        test.id
        for test in tests
    }

    missing = set(test_ids) - found_ids

    if missing:
        raise NotFoundError(
            "Lab test(s) not found: "
            f"{sorted(missing)}"
        )

    invalid_clinic = [
        test.id
        for test in tests
        if (
            test.clinic_id is not None
            and test.clinic_id != clinic_id
        )
    ]

    if invalid_clinic:
        raise ValidationError(
            f"Test(s) {invalid_clinic} do not belong "
            f"to clinic {clinic_id}"
        )

    inactive = [
        test.id
        for test in tests
        if not test.is_active
    ]

    if inactive:
        raise ValidationError(
            f"Test(s) {inactive} are inactive "
            "and cannot be ordered"
        )

    qr_code = _generate_unique_qr_code()

    order = LabOrder(
        clinic_id=clinic_id,
        patient_id=patient_id,
        consultation_id=consultation_id,
        ordered_by_id=staff.id,
        status=LabOrderStatus.ORDERED,
        qr_code=qr_code,
    )

    db.session.add(order)
    db.session.flush()

    for test in tests:
        db.session.add(
            LabOrderItem(
                order_id=order.id,
                test_id=test.id,
            )
        )

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="LabOrder",
        entity_id=order.id,
        clinic_id=clinic_id,
        description=(
            f"Lab order created for patient "
            f"{patient_id} ({len(tests)} test(s))"
        ),
        new_value={
            "clinic_id": clinic_id,
            "patient_id": patient_id,
            "ordered_by_id": staff.id,
            "consultation_id": consultation_id,
            "test_ids": sorted(found_ids),
            "qr_code": qr_code,
        },
    )

    return order


# ============================================================================
# SAMPLE COLLECTION
# ============================================================================

@transactional
def collect_sample(
    order_id: int,
    collected_by_id: int,
    scanned_qr_code: str | None,
    clinic_id: int,
) -> LabOrder:
    """
    Transition:

        ORDERED
            ↓
        SAMPLE_COLLECTED

    Records:

        - collector
        - collection timestamp
    """
    order = _get_lab_order(
        order_id,
        clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        clinic_id
    )

    actor = _validate_actor_for_order(
        collected_by_id,
        order,
    )

    _assert_status(
        order,
        LabOrderStatus.ORDERED,
    )

    if scanned_qr_code is not None:
        if not isinstance(scanned_qr_code, str):
            raise ValidationError(
                "scanned_qr_code must be a string"
            )

        scanned_qr_code = scanned_qr_code.strip()

        if not scanned_qr_code:
            raise ValidationError(
                "scanned_qr_code cannot be empty"
            )

        if order.qr_code != scanned_qr_code:
            raise ConflictError(
                "Scanned QR code does not match "
                "this lab order"
            )

    if order.sample_collected_at is not None:
        raise ConflictError(
            "Sample collection timestamp already exists"
        )

    if order.collected_by_id is not None:
        raise ConflictError(
            "Sample collector is already recorded"
        )

    now = _utcnow()
    old_status = order.status.value

    order.status = LabOrderStatus.SAMPLE_COLLECTED
    order.collected_by_id = actor.id
    order.sample_collected_at = now

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="LabOrder",
        entity_id=order.id,
        clinic_id=clinic_id,
        description=(
            f"Sample collected by staff {actor.id}"
        ),
        old_value={
            "status": old_status,
            "collected_by_id": None,
            "sample_collected_at": None,
        },
        new_value={
            "status": order.status.value,
            "collected_by_id": actor.id,
            "sample_collected_at": now.isoformat(),
        },
    )

    return order


# ============================================================================
# EQUIPMENT
# ============================================================================

@transactional
def link_equipment(
    order_id: int,
    equipment_reference_id: str,
    clinic_id: int,
) -> LabOrder:
    """
    Link a laboratory order to equipment.

    Equipment linkage is optional and independent from
    sample processing.

    Transition:

        SAMPLE_COLLECTED
                ↓
          IN_PROGRESS
    """
    order = _get_lab_order(
        order_id,
        clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        clinic_id
    )

    if (
        not isinstance(equipment_reference_id, str)
        or not equipment_reference_id.strip()
    ):
        raise ValidationError(
            "Equipment reference ID is required"
        )

    equipment_reference_id = (
        equipment_reference_id.strip()
    )

    if len(equipment_reference_id) > 150:
        raise ValidationError(
            "Equipment reference ID must not exceed "
            "150 characters"
        )

    _assert_status(
        order,
        LabOrderStatus.SAMPLE_COLLECTED,
    )

    if order.processed_by_id is not None:
        raise ConflictError(
            "Cannot link equipment after processing"
        )

    old_equipment_reference_id = (
        order.equipment_reference_id
    )

    old_status = order.status.value

    order.equipment_reference_id = (
        equipment_reference_id
    )

    order.status = LabOrderStatus.IN_PROGRESS

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="LabOrder",
        entity_id=order.id,
        clinic_id=clinic_id,
        description=(
            "Lab order linked to equipment reference "
            f"'{equipment_reference_id}'"
        ),
        old_value={
            "status": old_status,
            "equipment_reference_id": (
                old_equipment_reference_id
            ),
        },
        new_value={
            "status": order.status.value,
            "equipment_reference_id": (
                equipment_reference_id
            ),
        },
    )

    return order


# ============================================================================
# SAMPLE PROCESSING
# ============================================================================

@transactional
def process_sample(
    order_id: int,
    processed_by_id: int,
    clinic_id: int,
    equipment_reference_id: str | None = None,
) -> LabOrder:
    """
    Record laboratory sample processing.

    Allowed states:

        SAMPLE_COLLECTED
        IN_PROGRESS

    The first processing operation records:

        - processed_by_id
        - processed_at

    Equipment is optional.

    If equipment_reference_id is supplied, it is recorded.

    Processing cannot happen twice.
    """
    order = _get_lab_order(
        order_id,
        clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        clinic_id
    )

    actor = _validate_actor_for_order(
        processed_by_id,
        order,
    )

    _assert_status(
        order,
        LabOrderStatus.SAMPLE_COLLECTED,
        LabOrderStatus.IN_PROGRESS,
    )

    if order.processed_by_id is not None:
        raise ConflictError(
            "Lab order has already been processed"
        )

    if order.processed_at is not None:
        raise ConflictError(
            "Lab order processing timestamp already exists"
        )

    if order.sample_collected_at is None:
        raise ValidationError(
            "Cannot process a sample before collection"
        )

    if order.collected_by_id is None:
        raise ValidationError(
            "Cannot process a sample without "
            "a recorded collector"
        )

    if equipment_reference_id is not None:
        if not isinstance(
            equipment_reference_id,
            str,
        ):
            raise ValidationError(
                "Equipment reference ID must be a string"
            )

        equipment_reference_id = (
            equipment_reference_id.strip()
        )

        if not equipment_reference_id:
            raise ValidationError(
                "Equipment reference ID cannot be empty"
            )

        if len(equipment_reference_id) > 150:
            raise ValidationError(
                "Equipment reference ID must not exceed "
                "150 characters"
            )

        order.equipment_reference_id = (
            equipment_reference_id
        )

    now = _utcnow()
    old_status = order.status.value

    order.status = LabOrderStatus.IN_PROGRESS
    order.processed_by_id = actor.id
    order.processed_at = now

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="LabOrder",
        entity_id=order.id,
        clinic_id=clinic_id,
        description=(
            f"Sample processed by staff {actor.id}"
        ),
        old_value={
            "status": old_status,
            "processed_by_id": None,
            "processed_at": None,
        },
        new_value={
            "status": order.status.value,
            "processed_by_id": actor.id,
            "processed_at": now.isoformat(),
            "equipment_reference_id": (
                order.equipment_reference_id
            ),
        },
    )

    return order


# ============================================================================
# CANCELLATION
# ============================================================================

@transactional
def cancel_order(
    order_id: int,
    reason: str | None,
    clinic_id: int,
    cancelled_by_id: int,
) -> LabOrder:
    """
    Cancel a lab order.

    Cancellation is actor-controlled and tenant-scoped.
    """
    order = _get_lab_order(
        order_id,
        clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        clinic_id
    )

    actor = _validate_actor_for_order(
        cancelled_by_id,
        order,
    )

    if order.status in (
        LabOrderStatus.COMPLETED,
        LabOrderStatus.CANCELLED,
    ):
        raise ConflictError(
            "Cannot cancel a lab order that is already "
            f"{order.status.value}"
        )

    if reason is not None:
        if not isinstance(reason, str):
            raise ValidationError(
                "Cancellation reason must be a string"
            )

        reason = reason.strip()

        if not reason:
            raise ValidationError(
                "Cancellation reason cannot be empty"
            )

        if len(reason) > 255:
            raise ValidationError(
                "Cancellation reason must not exceed "
                "255 characters"
            )

    old_status = order.status.value

    order.status = LabOrderStatus.CANCELLED
    order.cancellation_reason = reason

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="LabOrder",
        entity_id=order.id,
        clinic_id=clinic_id,
        description=(
            "Lab order cancelled by staff "
            f"{actor.id}"
            + (
                f": {reason}"
                if reason
                else ""
            )
        ),
        old_value={
            "status": old_status,
        },
        new_value={
            "status": order.status.value,
            "cancellation_reason": reason,
            "cancelled_by_id": actor.id,
        },
    )

    return order


# ============================================================================
# RESULT FLAGGING
# ============================================================================

_RANGE_PATTERN = re.compile(
    r"^\s*"
    r"(?P<low>-?\d+(?:\.\d+)?)"
    r"\s*-\s*"
    r"(?P<high>-?\d+(?:\.\d+)?)"
    r"\s*$"
)

_BOUND_PATTERN = re.compile(
    r"^\s*"
    r"(?P<op><=|>=|<|>)"
    r"\s*"
    r"(?P<bound>-?\d+(?:\.\d+)?)"
    r"\s*$"
)


def _auto_flag(
    test: LabTest,
    result_value: str,
) -> LabResultFlag | None:
    """
    Determine a result flag automatically.

    Priority:

        CRITICAL
            ↓
        NORMAL / ABNORMAL
            ↓
        None

    Unsupported/non-numeric ranges are intentionally left
    unflagged rather than guessed.
    """
    if not isinstance(result_value, str):
        return None

    if not result_value.strip():
        return None

    try:
        value = Decimal(
            result_value.strip()
        )

    except (
        InvalidOperation,
        ValueError,
        AttributeError,
    ):
        return None

    if (
        test.critical_low is not None
        and value <= Decimal(
            str(test.critical_low)
        )
    ):
        return LabResultFlag.CRITICAL

    if (
        test.critical_high is not None
        and value >= Decimal(
            str(test.critical_high)
        )
    ):
        return LabResultFlag.CRITICAL

    reference_range = test.reference_range

    if not reference_range:
        return None

    range_match = _RANGE_PATTERN.match(
        reference_range
    )

    if range_match:
        try:
            low = Decimal(
                range_match["low"]
            )

            high = Decimal(
                range_match["high"]
            )

        except InvalidOperation:
            return None

        if low > high:
            return None

        return (
            LabResultFlag.NORMAL
            if low <= value <= high
            else LabResultFlag.ABNORMAL
        )

    bound_match = _BOUND_PATTERN.match(
        reference_range
    )

    if bound_match:
        try:
            bound = Decimal(
                bound_match["bound"]
            )

        except InvalidOperation:
            return None

        op = bound_match["op"]

        in_range = {
            "<": value < bound,
            "<=": value <= bound,
            ">": value > bound,
            ">=": value >= bound,
        }[op]

        return (
            LabResultFlag.NORMAL
            if in_range
            else LabResultFlag.ABNORMAL
        )

    return None


def _resolve_result_flag(
    *,
    test: LabTest,
    result_value: str,
    supplied_flag: LabResultFlag | None,
) -> tuple[
    LabResultFlag | None,
    bool,
]:
    """
    Resolve the final result flag.

    Automatic evaluation always takes precedence.

    Client-provided flags are only used when the service
    cannot determine a numerical flag from the configured
    laboratory reference information.
    """
    automatic_flag = _auto_flag(
        test,
        result_value,
    )

    if automatic_flag is not None:
        return automatic_flag, True

    if supplied_flag is not None:
        return supplied_flag, False

    return None, False


# ============================================================================
# RESULT ENTRY
# ============================================================================

@transactional
def enter_result(
    order_item_id: int,
    result_value: str,
    result_notes: str | None,
    result_file_url: str | None,
    flag: LabResultFlag | None,
    clinic_id: int,
) -> LabOrderItem:
    """
    Enter or update a laboratory result.

    Result entry does NOT complete the order.

    Results become immutable after order verification.
    """
    if not isinstance(result_value, str):
        raise ValidationError(
            "Result value must be a string"
        )

    result_value = result_value.strip()

    if not result_value:
        raise ValidationError(
            "Result value is required"
        )

    if len(result_value) > 150:
        raise ValidationError(
            "Result value must not exceed 150 characters"
        )

    if result_notes is not None:
        if not isinstance(result_notes, str):
            raise ValidationError(
                "Result notes must be a string"
            )

        result_notes = result_notes.strip()

        if not result_notes:
            result_notes = None

    if result_file_url is not None:
        if not isinstance(result_file_url, str):
            raise ValidationError(
                "Result file URL must be a string"
            )

        result_file_url = result_file_url.strip()

        if not result_file_url:
            result_file_url = None

        if (
            result_file_url
            and len(result_file_url) > 255
        ):
            raise ValidationError(
                "Result file URL must not exceed "
                "255 characters"
            )

    item = _get_lab_order_item(
        order_item_id,
        clinic_id,
        for_update=True,
    )

    order = item.order

    ensure_clinic_active(
        clinic_id
    )

    _assert_status(
        order,
        LabOrderStatus.IN_PROGRESS,
    )

    if order.sample_collected_at is None:
        raise ValidationError(
            "Cannot enter results before sample collection"
        )

    if order.collected_by_id is None:
        raise ValidationError(
            "Cannot enter results without "
            "a recorded collector"
        )

    if order.processed_by_id is None:
        raise ValidationError(
            "Cannot enter results before the sample "
            "has been processed"
        )

    if order.processed_at is None:
        raise ValidationError(
            "Cannot enter results before processing "
            "timestamp is recorded"
        )

    if order.verified_by_id is not None:
        raise ConflictError(
            "Cannot modify a result after verification"
        )

    if order.verified_at is not None:
        raise ConflictError(
            "Cannot modify a result after verification"
        )

    resolved_flag, automatic_flag_used = (
        _resolve_result_flag(
            test=item.test,
            result_value=result_value,
            supplied_flag=flag,
        )
    )

    old_value = {
        "result_value": item.result_value,
        "flag": (
            item.flag.value
            if item.flag is not None
            else None
        ),
        "result_notes": item.result_notes,
        "result_file_url": item.result_file_url,
        "resulted_at": _serialize_value(
            item.resulted_at
        ),
    }

    now = _utcnow()

    item.result_value = result_value
    item.flag = resolved_flag
    item.result_notes = result_notes
    item.result_file_url = result_file_url
    item.resulted_at = now

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="LabOrderItem",
        entity_id=item.id,
        clinic_id=clinic_id,
        description=(
            f"Result entered for test "
            f"'{item.test.name}'"
            + (
                " [AUTO-FLAGGED]"
                if automatic_flag_used
                and resolved_flag is not None
                else ""
            )
        ),
        old_value=old_value,
        new_value={
            "result_value": result_value,
            "flag": (
                resolved_flag.value
                if resolved_flag is not None
                else None
            ),
            "result_notes": result_notes,
            "result_file_url": result_file_url,
            "resulted_at": now.isoformat(),
        },
    )

    return item


# ============================================================================
# RESULT VERIFICATION
# ============================================================================

@transactional
def verify_results(
    order_id: int,
    verified_by_id: int,
    clinic_id: int,
) -> LabOrder:
    """
    Verify every result on a laboratory order.

    This intentionally loads ALL order items because verification
    must establish that no result is missing.
    """
    order = _get_lab_order(
        order_id,
        clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        clinic_id
    )

    actor = _validate_actor_for_order(
        verified_by_id,
        order,
    )

    _assert_status(
        order,
        LabOrderStatus.IN_PROGRESS,
    )

    if order.sample_collected_at is None:
        raise ValidationError(
            "Cannot verify an order before "
            "sample collection"
        )

    if order.collected_by_id is None:
        raise ValidationError(
            "Cannot verify an order without "
            "a recorded collector"
        )

    if order.processed_at is None:
        raise ValidationError(
            "Cannot verify an order before processing"
        )

    if order.processed_by_id is None:
        raise ValidationError(
            "Cannot verify an order without "
            "a recorded processor"
        )

    if order.verified_at is not None:
        raise ConflictError(
            "Lab order has already been verified"
        )

    if order.verified_by_id is not None:
        raise ConflictError(
            "Lab order already has a verification actor"
        )

    statement = (
        select(LabOrderItem)
        .where(
            LabOrderItem.order_id == order.id
        )
        .with_for_update()
    )

    items = list(
        db.session.execute(statement)
        .scalars()
        .all()
    )

    if not items:
        raise ValidationError(
            "Cannot verify a lab order with no test items"
        )

    missing_results = [
        item.id
        for item in items
        if (
            item.result_value is None
            or not item.result_value.strip()
            or item.resulted_at is None
        )
    ]

    if missing_results:
        raise ConflictError(
            "Cannot verify lab order because "
            f"result(s) are missing for item(s): "
            f"{missing_results}"
        )

    now = _utcnow()

    order.verified_by_id = actor.id
    order.verified_at = now

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="LabOrder",
        entity_id=order.id,
        clinic_id=clinic_id,
        description=(
            f"Lab results verified by staff {actor.id}"
        ),
        old_value={
            "verified_by_id": None,
            "verified_at": None,
        },
        new_value={
            "verified_by_id": actor.id,
            "verified_at": now.isoformat(),
        },
    )

    return order


# ============================================================================
# ORDER COMPLETION
# ============================================================================

@transactional
def complete_order(
    order_id: int,
    clinic_id: int,
) -> LabOrder:
    """
    Finalize a verified laboratory order.

    This intentionally loads ALL order items because completion
    must establish that no result is missing.
    """
    order = _get_lab_order(
        order_id,
        clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        clinic_id
    )

    _assert_status(
        order,
        LabOrderStatus.IN_PROGRESS,
    )

    if order.verified_by_id is None:
        raise ConflictError(
            "Cannot complete a lab order before verification"
        )

    if order.verified_at is None:
        raise ConflictError(
            "Cannot complete a lab order before "
            "verification timestamp is recorded"
        )

    if order.processed_by_id is None:
        raise ValidationError(
            "Cannot complete a lab order without "
            "a recorded processor"
        )

    if order.processed_at is None:
        raise ValidationError(
            "Cannot complete a lab order without "
            "a processing timestamp"
        )

    statement = (
        select(LabOrderItem)
        .where(
            LabOrderItem.order_id == order.id
        )
        .with_for_update()
    )

    items = list(
        db.session.execute(statement)
        .scalars()
        .all()
    )

    if not items:
        raise ValidationError(
            "Cannot complete a lab order with no test items"
        )

    missing_results = [
        item.id
        for item in items
        if (
            item.result_value is None
            or not item.result_value.strip()
            or item.resulted_at is None
        )
    ]

    if missing_results:
        raise ConflictError(
            "Cannot complete lab order because "
            f"result(s) are missing for item(s): "
            f"{missing_results}"
        )

    old_status = order.status.value
    now = _utcnow()

    order.status = LabOrderStatus.COMPLETED
    order.completed_at = now

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="LabOrder",
        entity_id=order.id,
        clinic_id=clinic_id,
        description=(
            "All results verified — lab order completed"
        ),
        old_value={
            "status": old_status,
        },
        new_value={
            "status": order.status.value,
            "verified_by_id": order.verified_by_id,
            "verified_at": _serialize_value(
                order.verified_at
            ),
            "completed_at": now.isoformat(),
        },
    )

    return order


# ============================================================================
# CONSISTENCY / INTEGRITY HELPERS
# ============================================================================

def validate_lab_order_integrity(
    order: LabOrder,
) -> None:
    """
    Validate the actor/timestamp lifecycle of an existing order.
    """
    _validate_actor_pair(
        order,
        "collected_by_id",
        "sample_collected_at",
    )

    _validate_actor_pair(
        order,
        "processed_by_id",
        "processed_at",
    )

    _validate_actor_pair(
        order,
        "verified_by_id",
        "verified_at",
    )

    # ------------------------------------------------------------------
    # Processing requires collection
    # ------------------------------------------------------------------

    if order.processed_at is not None:
        if order.sample_collected_at is None:
            raise ValidationError(
                "Processed timestamp exists without "
                "sample collection timestamp"
            )

        if order.collected_by_id is None:
            raise ValidationError(
                "Processed timestamp exists without "
                "a recorded collector"
            )

    if order.processed_by_id is not None:
        if order.sample_collected_at is None:
            raise ValidationError(
                "Processor exists without "
                "sample collection"
            )

        if order.collected_by_id is None:
            raise ValidationError(
                "Processor exists without "
                "a recorded collector"
            )

    # ------------------------------------------------------------------
    # Verification requires processing
    # ------------------------------------------------------------------

    if order.verified_at is not None:
        if order.processed_at is None:
            raise ValidationError(
                "Verified timestamp exists without "
                "processing timestamp"
            )

        if order.processed_by_id is None:
            raise ValidationError(
                "Verification exists without "
                "a recorded processor"
            )

    if order.verified_by_id is not None:
        if order.processed_at is None:
            raise ValidationError(
                "Verification actor exists without "
                "processing timestamp"
            )

        if order.processed_by_id is None:
            raise ValidationError(
                "Verification actor exists without "
                "a recorded processor"
            )

    # ------------------------------------------------------------------
    # Completion requires verification
    # ------------------------------------------------------------------

    if order.completed_at is not None:
        if order.verified_at is None:
            raise ValidationError(
                "Completed timestamp exists without "
                "verification timestamp"
            )

        if order.verified_by_id is None:
            raise ValidationError(
                "Completed timestamp exists without "
                "a recorded verifier"
            )

    # ------------------------------------------------------------------
    # Status consistency
    # ------------------------------------------------------------------

    if order.status == LabOrderStatus.ORDERED:
        if order.sample_collected_at is not None:
            raise ValidationError(
                "ORDERED order cannot have "
                "a sample collection timestamp"
            )

        if order.collected_by_id is not None:
            raise ValidationError(
                "ORDERED order cannot have "
                "a recorded collector"
            )

        if order.processed_at is not None:
            raise ValidationError(
                "ORDERED order cannot have "
                "a processing timestamp"
            )

        if order.processed_by_id is not None:
            raise ValidationError(
                "ORDERED order cannot have "
                "a recorded processor"
            )

        if order.verified_at is not None:
            raise ValidationError(
                "ORDERED order cannot have "
                "a verification timestamp"
            )

        if order.verified_by_id is not None:
            raise ValidationError(
                "ORDERED order cannot have "
                "a recorded verifier"
            )

        if order.completed_at is not None:
            raise ValidationError(
                "ORDERED order cannot have "
                "a completion timestamp"
            )

    if order.status == LabOrderStatus.SAMPLE_COLLECTED:
        if order.sample_collected_at is None:
            raise ValidationError(
                "SAMPLE_COLLECTED order requires "
                "a collection timestamp"
            )

        if order.collected_by_id is None:
            raise ValidationError(
                "SAMPLE_COLLECTED order requires "
                "a recorded collector"
            )

        if order.processed_at is not None:
            raise ValidationError(
                "SAMPLE_COLLECTED order cannot have "
                "a processing timestamp"
            )

        if order.processed_by_id is not None:
            raise ValidationError(
                "SAMPLE_COLLECTED order cannot have "
                "a recorded processor"
            )

        if order.verified_at is not None:
            raise ValidationError(
                "SAMPLE_COLLECTED order cannot have "
                "a verification timestamp"
            )

        if order.verified_by_id is not None:
            raise ValidationError(
                "SAMPLE_COLLECTED order cannot have "
                "a recorded verifier"
            )

        if order.completed_at is not None:
            raise ValidationError(
                "SAMPLE_COLLECTED order cannot have "
                "a completion timestamp"
            )

    if order.status == LabOrderStatus.IN_PROGRESS:
        if order.processed_at is None:
            raise ValidationError(
                "IN_PROGRESS order requires "
                "a processing timestamp"
            )

        if order.processed_by_id is None:
            raise ValidationError(
                "IN_PROGRESS order requires "
                "a recorded processor"
            )

        if order.sample_collected_at is None:
            raise ValidationError(
                "IN_PROGRESS order requires "
                "a collection timestamp"
            )

        if order.collected_by_id is None:
            raise ValidationError(
                "IN_PROGRESS order requires "
                "a recorded collector"
            )

        if order.completed_at is not None:
            raise ValidationError(
                "IN_PROGRESS order cannot have "
                "a completion timestamp"
            )

    if order.status == LabOrderStatus.COMPLETED:
        if order.sample_collected_at is None:
            raise ValidationError(
                "COMPLETED order requires "
                "a collection timestamp"
            )

        if order.collected_by_id is None:
            raise ValidationError(
                "COMPLETED order requires "
                "a recorded collector"
            )

        if order.processed_at is None:
            raise ValidationError(
                "COMPLETED order requires "
                "a processing timestamp"
            )

        if order.processed_by_id is None:
            raise ValidationError(
                "COMPLETED order requires "
                "a recorded processor"
            )

        if order.verified_at is None:
            raise ValidationError(
                "COMPLETED order requires "
                "a verification timestamp"
            )

        if order.verified_by_id is None:
            raise ValidationError(
                "COMPLETED order requires "
                "a recorded verifier"
            )

        if order.completed_at is None:
            raise ValidationError(
                "COMPLETED order requires "
                "a completion timestamp"
            )

    if order.status == LabOrderStatus.CANCELLED:
        # Cancelled orders may exist at different points in
        # the lifecycle, but they must never appear finalized.
        if order.completed_at is not None:
            raise ValidationError(
                "CANCELLED order cannot have "
                "a completion timestamp"
            )
