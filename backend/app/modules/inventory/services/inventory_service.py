from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select

from app.extensions import db

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.inventory_enums import (
    DECREASING_MOVEMENTS,
    INCREASING_MOVEMENTS,
    InventoryCategory,
    InventoryTransferStatus,
    StockMovementDirection,
    StockMovementType,
)
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional

from app.modules.clinic.models.clinic_model import Clinic
from app.modules.inventory.models.inventory_model import (
    InventoryBatch,
    InventoryItem,
    InventorySupplier,
    InventoryTransfer,
    StockMovement,
)
from app.modules.staff.models.staff_model import Staff


# ============================================================================
# CONSTANTS
# ============================================================================

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500

_ITEM_EDITABLE_FIELDS = {
    "name",
    "category",
    "sku",
    "barcode",
    "unit",
    "reorder_level",
}

_SUPPLIER_EDITABLE_FIELDS = {
    "name",
    "contact_person",
    "phone",
    "email",
    "address",
}

_BATCH_EDITABLE_FIELDS = {
    "batch_number",
    "unit_cost",
    "expiry_date",
    "supplier_id",
}


# ============================================================================
# GENERAL HELPERS
# ============================================================================

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_enum(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _normalize_optional_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValidationError(
            "Expected a string value"
        )

    value = value.strip()

    return value or None


def _validate_positive_id(
    value: int,
    field_name: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{field_name} must be an integer"
        )

    if value <= 0:
        raise ValidationError(
            f"{field_name} must be greater than zero"
        )

    return value


def _validate_positive_quantity(
    quantity: int,
) -> None:
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValidationError(
            "Quantity must be an integer"
        )

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero"
        )


def _validate_non_negative(
    value: int,
    field_name: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{field_name} must be an integer"
        )

    if value < 0:
        raise ValidationError(
            f"{field_name} cannot be negative"
        )


def _validate_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
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


def _get_active_clinic(
    clinic_id: int,
) -> Clinic:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
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
        raise ConflictError(
            f"Clinic {clinic_id} is inactive"
        )

    return clinic


def _ensure_same_organization(source: Clinic | None, destination: Clinic | None) -> None:
    if (
        source is None
        or destination is None
        or source.organization_id is None
        or source.organization_id != destination.organization_id
    ):
        raise ValidationError("Inventory transfers require clinics in the same organization")


def _ensure_transfer_organization(transfer: InventoryTransfer) -> None:
    _ensure_same_organization(
        db.session.get(Clinic, transfer.source_clinic_id),
        db.session.get(Clinic, transfer.destination_clinic_id),
    )


def _ensure_same_clinic(
    *,
    expected_clinic_id: int,
    actual_clinic_id: int | None,
    resource_name: str,
) -> None:
    if actual_clinic_id != expected_clinic_id:
        raise ValidationError(
            f"{resource_name} does not belong to "
            f"clinic {expected_clinic_id}"
        )


# ============================================================================
# STAFF HELPERS
# ============================================================================

def _get_staff(
    staff_id: int,
) -> Staff:
    staff_id = _validate_positive_id(
        staff_id,
        "staff_id",
    )

    staff = db.session.get(
        Staff,
        staff_id,
    )

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    return staff


def _validate_staff_for_clinic(
    *,
    staff_id: int,
    clinic_id: int,
) -> Staff:
    staff = _get_staff(
        staff_id
    )

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    if staff.clinic_id != clinic_id:
        raise ValidationError(
            f"Staff {staff_id} does not belong to "
            f"clinic {clinic_id}"
        )

    if staff.status != StaffStatus.ACTIVE:
        raise ConflictError(
            f"Staff {staff_id} is inactive"
        )

    return staff


# ============================================================================
# INVENTORY ITEM HELPERS
# ============================================================================

def _get_item(
    item_id: int,
) -> InventoryItem:
    item_id = _validate_positive_id(
        item_id,
        "item_id",
    )

    item = db.session.get(
        InventoryItem,
        item_id,
    )

    if item is None:
        raise NotFoundError(
            f"Inventory item {item_id} not found"
        )

    return item


def _get_locked_item(
    item_id: int,
) -> InventoryItem:
    item_id = _validate_positive_id(
        item_id,
        "item_id",
    )

    statement = (
        select(InventoryItem)
        .where(
            InventoryItem.id == item_id,
        )
        .with_for_update()
    )

    item = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if item is None:
        raise NotFoundError(
            f"Inventory item {item_id} not found"
        )

    return item


def _get_locked_active_item(
    item_id: int,
) -> InventoryItem:
    item = _get_locked_item(
        item_id
    )

    if not item.is_active:
        raise ConflictError(
            f"Inventory item {item_id} is inactive"
        )

    return item


def _validate_item_clinic(
    item: InventoryItem,
    clinic_id: int | None,
) -> InventoryItem:
    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        _ensure_same_clinic(
            expected_clinic_id=clinic_id,
            actual_clinic_id=item.clinic_id,
            resource_name=(
                f"Inventory item {item.id}"
            ),
        )

    return item


# ============================================================================
# SUPPLIER HELPERS
# ============================================================================

def _get_supplier(
    supplier_id: int,
) -> InventorySupplier:
    supplier_id = _validate_positive_id(
        supplier_id,
        "supplier_id",
    )

    supplier = db.session.get(
        InventorySupplier,
        supplier_id,
    )

    if supplier is None:
        raise NotFoundError(
            f"Inventory supplier {supplier_id} not found"
        )

    return supplier


def _get_locked_supplier(
    supplier_id: int,
) -> InventorySupplier:
    supplier_id = _validate_positive_id(
        supplier_id,
        "supplier_id",
    )

    statement = (
        select(InventorySupplier)
        .where(
            InventorySupplier.id == supplier_id,
        )
        .with_for_update()
    )

    supplier = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if supplier is None:
        raise NotFoundError(
            f"Inventory supplier {supplier_id} not found"
        )

    return supplier


def _validate_supplier_for_clinic(
    supplier: InventorySupplier,
    clinic_id: int,
) -> None:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    if (
        supplier.clinic_id is not None
        and supplier.clinic_id != clinic_id
    ):
        raise ValidationError(
            f"Supplier {supplier.id} does not belong to "
            f"clinic {clinic_id}"
        )


def _ensure_supplier_mutation_clinic(
    *,
    supplier: InventorySupplier,
    clinic_id: int | None,
) -> int:
    if supplier.clinic_id is None:
        raise ValidationError("Shared inventory suppliers are read-only")

    if clinic_id is None:
        clinic_id = supplier.clinic_id

    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    _get_active_clinic(
        clinic_id
    )

    _validate_supplier_for_clinic(
        supplier,
        clinic_id,
    )

    return clinic_id


# ============================================================================
# BATCH HELPERS
# ============================================================================

def _get_batch(
    batch_id: int,
) -> InventoryBatch:
    batch_id = _validate_positive_id(
        batch_id,
        "batch_id",
    )

    batch = db.session.get(
        InventoryBatch,
        batch_id,
    )

    if batch is None:
        raise NotFoundError(
            f"Inventory batch {batch_id} not found"
        )

    return batch


def _get_locked_batch(
    batch_id: int,
) -> InventoryBatch:
    batch_id = _validate_positive_id(
        batch_id,
        "batch_id",
    )

    statement = (
        select(InventoryBatch)
        .where(
            InventoryBatch.id == batch_id,
        )
        .with_for_update()
    )

    batch = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if batch is None:
        raise NotFoundError(
            f"Inventory batch {batch_id} not found"
        )

    return batch


def _validate_batch_for_item(
    *,
    batch: InventoryBatch,
    item: InventoryItem,
) -> None:
    if batch.item_id != item.id:
        raise ValidationError(
            f"Batch {batch.id} does not belong to "
            f"inventory item {item.id}"
        )


def _validate_batch_active(
    batch: InventoryBatch,
) -> None:
    if not batch.is_active:
        raise ConflictError(
            f"Inventory batch {batch.id} is inactive"
        )


def _validate_batch_expiry(
    expiry_date: date | None,
) -> None:
    if (
        expiry_date is not None
        and expiry_date < date.today()
    ):
        raise ValidationError(
            "Expiry date cannot be in the past"
        )


# ============================================================================
# TRANSFER HELPERS
# ============================================================================

def _get_locked_transfer(
    transfer_id: int,
) -> InventoryTransfer:
    transfer_id = _validate_positive_id(
        transfer_id,
        "transfer_id",
    )

    statement = (
        select(InventoryTransfer)
        .where(
            InventoryTransfer.id == transfer_id,
        )
        .with_for_update()
    )

    transfer = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if transfer is None:
        raise NotFoundError(
            f"Inventory transfer {transfer_id} not found"
        )

    return transfer


# ============================================================================
# AUDIT HELPERS
# ============================================================================

def _audit_value(
    value: Any,
) -> Any:
    if hasattr(value, "value"):
        return value.value

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    return value


def _build_change_dict(
    old_values: dict[str, Any],
    new_values: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    return (
        {
            key: _audit_value(value)
            for key, value in old_values.items()
        },
        {
            key: _audit_value(value)
            for key, value in new_values.items()
        },
    )


# ============================================================================
# STOCK HELPERS
# ============================================================================

def _validate_stock_invariants(
    *,
    item: InventoryItem,
    batch: InventoryBatch | None = None,
) -> None:
    if item.quantity_on_hand < 0:
        raise ConflictError(
            f"Inventory item {item.id} has invalid negative stock"
        )

    if (
        batch is not None
        and batch.quantity_on_hand < 0
    ):
        raise ConflictError(
            f"Inventory batch {batch.id} has invalid negative stock"
        )


def _resolve_movement_direction(
    movement_type: StockMovementType,
    quantity: int,
) -> tuple[
    StockMovementDirection,
    int,
]:
    if movement_type == StockMovementType.ADJUSTMENT:
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ValidationError(
                "Quantity must be an integer"
            )

        if quantity == 0:
            raise ValidationError(
                "Adjustment quantity cannot be zero"
            )

        if quantity > 0:
            return (
                StockMovementDirection.IN,
                quantity,
            )

        return (
            StockMovementDirection.OUT,
            abs(quantity),
        )

    _validate_positive_quantity(
        quantity
    )

    if movement_type in INCREASING_MOVEMENTS:
        return (
            StockMovementDirection.IN,
            quantity,
        )

    if movement_type in DECREASING_MOVEMENTS:
        return (
            StockMovementDirection.OUT,
            quantity,
        )

    raise ValidationError(
        "Cannot determine stock direction for movement "
        f"type '{_serialize_enum(movement_type)}'"
    )


# ============================================================================
# INVENTORY ITEMS
# ============================================================================

def get_inventory_item(
    item_id: int,
    clinic_id: int | None = None,
) -> InventoryItem:
    item = _get_item(
        item_id
    )

    _validate_item_clinic(
        item,
        clinic_id,
    )

    return item


def list_inventory_items(
    clinic_id: int,
    category: InventoryCategory | None = None,
    low_stock_only: bool = False,
    include_inactive: bool = False,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    _get_active_clinic(
        clinic_id
    )

    statement = (
        select(InventoryItem)
        .where(
            InventoryItem.clinic_id == clinic_id,
        )
    )

    if not include_inactive:
        statement = statement.where(
            InventoryItem.is_active.is_(True),
        )

    if category is not None:
        statement = statement.where(
            InventoryItem.category == category,
        )

    if low_stock_only:
        statement = statement.where(
            InventoryItem.quantity_on_hand
            <= InventoryItem.reorder_level
        )

    statement = statement.order_by(
        InventoryItem.name.asc(),
        InventoryItem.id.asc(),
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
    )


def get_low_stock_items(
    clinic_id: int,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    return list_inventory_items(
        clinic_id=clinic_id,
        low_stock_only=True,
        include_inactive=False,
        page=page,
        per_page=per_page,
    )


@transactional
def create_inventory_item(
    clinic_id: int,
    name: str,
    category: InventoryCategory = (
        InventoryCategory.MEDICAL_SUPPLY
    ),
    initial_quantity: int = 0,
    performed_by_id: int | None = None,
    **fields,
) -> InventoryItem:
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    _get_active_clinic(
        clinic_id
    )

    if (
        not isinstance(name, str)
        or not name.strip()
    ):
        raise ValidationError(
            "Inventory item name is required"
        )

    _validate_non_negative(
        initial_quantity,
        "Initial quantity",
    )

    if performed_by_id is not None:
        performed_by_id = _validate_positive_id(
            performed_by_id,
            "performed_by_id",
        )

    unknown = set(fields) - _ITEM_EDITABLE_FIELDS

    if unknown:
        raise ValidationError(
            "Unknown inventory item field(s): "
            + ", ".join(sorted(unknown))
        )

    name = name.strip()

    sku = fields.get("sku")

    if sku is not None:
        sku = _normalize_optional_text(
            sku
        )

        if sku:
            statement = select(
                InventoryItem
            ).where(
                InventoryItem.sku == sku,
            )

            existing = (
                db.session.execute(statement)
                .scalars()
                .first()
            )

            if existing is not None:
                raise ConflictError(
                    f"SKU '{sku}' is already assigned to "
                    f"inventory item {existing.id}"
                )

        fields["sku"] = sku

    barcode = fields.get("barcode")

    if barcode is not None:
        barcode = _normalize_optional_text(
            barcode
        )

        if barcode:
            statement = select(
                InventoryItem
            ).where(
                InventoryItem.barcode == barcode,
            )

            existing = (
                db.session.execute(statement)
                .scalars()
                .first()
            )

            if existing is not None:
                raise ConflictError(
                    f"Barcode '{barcode}' is already assigned "
                    f"to inventory item {existing.id}"
                )

        fields["barcode"] = barcode

    if "unit" in fields:
        fields["unit"] = _normalize_optional_text(
            fields["unit"]
        )

    if "reorder_level" in fields:
        _validate_non_negative(
            fields["reorder_level"],
            "Reorder level",
        )

    item = InventoryItem(
        clinic_id=clinic_id,
        name=name,
        category=category,
        quantity_on_hand=initial_quantity,
        **fields,
    )

    db.session.add(
        item
    )
    db.session.flush()

    if initial_quantity > 0:
        if performed_by_id is None:
            raise ValidationError(
                "performed_by_id is required when "
                "initial_quantity is greater than zero"
            )

        _validate_staff_for_clinic(
            staff_id=performed_by_id,
            clinic_id=clinic_id,
        )

        movement = StockMovement(
            item_id=item.id,
            movement_type=StockMovementType.RESTOCK,
            direction=StockMovementDirection.IN,
            quantity=initial_quantity,
            reason="Initial inventory quantity",
            performed_by_id=performed_by_id,
            reference_type="inventory_item",
            reference_id=item.id,
        )

        db.session.add(
            movement
        )
        db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="InventoryItem",
        entity_id=item.id,
        description=(
            f"Inventory item '{item.name}' created"
        ),
        new_value={
            "clinic_id": clinic_id,
            "quantity_on_hand": initial_quantity,
        },
    )

    return item


@transactional
def update_inventory_item(
    item_id: int,
    clinic_id: int | None = None,
    **fields,
) -> InventoryItem:
    item = _get_locked_item(
        item_id
    )

    _get_active_clinic(
        item.clinic_id
    )

    _validate_item_clinic(
        item,
        clinic_id,
    )

    unknown = set(fields) - _ITEM_EDITABLE_FIELDS

    if unknown:
        raise ValidationError(
            "Unknown inventory item field(s): "
            + ", ".join(sorted(unknown))
        )

    old_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}

    for key, new_value in fields.items():
        if key == "name":
            new_value = _normalize_optional_text(
                new_value
            )

            if not new_value:
                raise ValidationError(
                    "Inventory item name cannot be empty"
                )

        elif key in {
            "sku",
            "barcode",
            "unit",
        }:
            new_value = _normalize_optional_text(
                new_value
            )

        elif key == "reorder_level":
            _validate_non_negative(
                new_value,
                "Reorder level",
            )

        current_value = getattr(
            item,
            key,
        )

        if current_value == new_value:
            continue

        if key == "sku" and new_value:
            statement = select(
                InventoryItem
            ).where(
                InventoryItem.sku == new_value,
                InventoryItem.id != item.id,
            )

            existing = (
                db.session.execute(statement)
                .scalars()
                .first()
            )

            if existing is not None:
                raise ConflictError(
                    f"SKU '{new_value}' is already assigned "
                    f"to inventory item {existing.id}"
                )

        if key == "barcode" and new_value:
            statement = select(
                InventoryItem
            ).where(
                InventoryItem.barcode == new_value,
                InventoryItem.id != item.id,
            )

            existing = (
                db.session.execute(statement)
                .scalars()
                .first()
            )

            if existing is not None:
                raise ConflictError(
                    f"Barcode '{new_value}' is already assigned "
                    f"to inventory item {existing.id}"
                )

        old_values[key] = current_value
        new_values[key] = new_value

        setattr(
            item,
            key,
            new_value,
        )

    if new_values:
        old_value, new_value = _build_change_dict(
            old_values,
            new_values,
        )

        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="InventoryItem",
            entity_id=item.id,
            description=(
                f"Inventory item '{item.name}' updated"
            ),
            old_value=old_value,
            new_value=new_value,
        )

    return item


@transactional
def deactivate_inventory_item(
    item_id: int,
    clinic_id: int | None = None,
) -> InventoryItem:
    item = _get_locked_item(
        item_id
    )

    _get_active_clinic(
        item.clinic_id
    )

    _validate_item_clinic(
        item,
        clinic_id,
    )

    if not item.is_active:
        return item

    item.is_active = False

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="InventoryItem",
        entity_id=item.id,
        description=(
            f"Inventory item '{item.name}' deactivated"
        ),
        old_value={
            "is_active": True,
        },
        new_value={
            "is_active": False,
        },
    )

    return item


@transactional
def reactivate_inventory_item(
    item_id: int,
    clinic_id: int | None = None,
) -> InventoryItem:
    item = _get_locked_item(
        item_id
    )

    _get_active_clinic(
        item.clinic_id
    )

    _validate_item_clinic(
        item,
        clinic_id,
    )

    if item.is_active:
        return item

    item.is_active = True

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="InventoryItem",
        entity_id=item.id,
        description=(
            f"Inventory item '{item.name}' reactivated"
        ),
        old_value={
            "is_active": False,
        },
        new_value={
            "is_active": True,
        },
    )

    return item


# ============================================================================
# SUPPLIERS
# ============================================================================

def get_supplier(
    supplier_id: int,
    clinic_id: int | None = None,
) -> InventorySupplier:
    supplier = _get_supplier(
        supplier_id
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        _validate_supplier_for_clinic(
            supplier,
            clinic_id,
        )

    return supplier


def list_suppliers(
    clinic_id: int | None = None,
    include_inactive: bool = False,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    if clinic_id is not None:
        _get_active_clinic(
            clinic_id
        )

    statement = select(
        InventorySupplier
    )

    if clinic_id is not None:
        statement = statement.where(
            or_(
                InventorySupplier.clinic_id == clinic_id,
                InventorySupplier.clinic_id.is_(None),
            )
        )

    if not include_inactive:
        statement = statement.where(
            InventorySupplier.is_active.is_(True),
        )

    statement = statement.order_by(
        InventorySupplier.name.asc(),
        InventorySupplier.id.asc(),
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
    )


@transactional
def create_supplier(
    name: str,
    clinic_id: int | None = None,
    **fields,
) -> InventorySupplier:
    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        _get_active_clinic(
            clinic_id
        )

    if (
        not isinstance(name, str)
        or not name.strip()
    ):
        raise ValidationError(
            "Supplier name is required"
        )

    unknown = set(fields) - _SUPPLIER_EDITABLE_FIELDS

    if unknown:
        raise ValidationError(
            "Unknown supplier field(s): "
            + ", ".join(sorted(unknown))
        )

    name = name.strip()

    duplicate_statement = select(
        InventorySupplier
    ).where(
        func.lower(
            InventorySupplier.name
        ) == name.lower(),
    )

    if clinic_id is not None:
        duplicate_statement = duplicate_statement.where(
            or_(
                InventorySupplier.clinic_id == clinic_id,
                InventorySupplier.clinic_id.is_(None),
            )
        )

    existing = (
        db.session.execute(
            duplicate_statement
        )
        .scalars()
        .first()
    )

    if existing is not None:
        raise ConflictError(
            f"Supplier '{name}' already exists"
        )

    for key in fields:
        fields[key] = _normalize_optional_text(
            fields[key]
        )

    supplier = InventorySupplier(
        name=name,
        clinic_id=clinic_id,
        **fields,
    )

    db.session.add(
        supplier
    )
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="InventorySupplier",
        entity_id=supplier.id,
        description=(
            f"Inventory supplier '{supplier.name}' created"
        ),
        new_value={
            "clinic_id": supplier.clinic_id,
        },
    )

    return supplier


@transactional
def update_supplier(
    supplier_id: int,
    clinic_id: int | None = None,
    **fields,
) -> InventorySupplier:
    supplier = _get_locked_supplier(
        supplier_id
    )

    mutation_clinic_id = _ensure_supplier_mutation_clinic(
        supplier=supplier,
        clinic_id=clinic_id,
    )

    unknown = set(fields) - _SUPPLIER_EDITABLE_FIELDS

    if unknown:
        raise ValidationError(
            "Unknown supplier field(s): "
            + ", ".join(sorted(unknown))
        )

    old_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}

    for key, new_value in fields.items():
        if key in {
            "name",
            "contact_person",
            "phone",
            "email",
            "address",
        }:
            new_value = _normalize_optional_text(
                new_value
            )

        if key == "name" and not new_value:
            raise ValidationError(
                "Supplier name cannot be empty"
            )

        current_value = getattr(
            supplier,
            key,
        )

        if current_value == new_value:
            continue

        if key == "name" and new_value:
            duplicate_statement = select(
                InventorySupplier
            ).where(
                func.lower(
                    InventorySupplier.name
                ) == new_value.lower(),
                InventorySupplier.id != supplier.id,
            )

            if supplier.clinic_id is not None:
                duplicate_statement = (
                    duplicate_statement.where(
                        or_(
                            InventorySupplier.clinic_id
                            == supplier.clinic_id,
                            InventorySupplier.clinic_id.is_(None),
                        )
                    )
                )

            duplicate = (
                db.session.execute(
                    duplicate_statement
                )
                .scalars()
                .first()
            )

            if duplicate is not None:
                raise ConflictError(
                    f"Supplier '{new_value}' already exists"
                )

        old_values[key] = current_value
        new_values[key] = new_value

        setattr(
            supplier,
            key,
            new_value,
        )

    if new_values:
        old_value, new_value = _build_change_dict(
            old_values,
            new_values,
        )

        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="InventorySupplier",
            entity_id=supplier.id,
            description=(
                f"Inventory supplier '{supplier.name}' "
                f"updated in clinic {mutation_clinic_id}"
            ),
            old_value=old_value,
            new_value=new_value,
        )

    return supplier


@transactional
def deactivate_supplier(
    supplier_id: int,
    clinic_id: int | None = None,
) -> InventorySupplier:
    supplier = _get_locked_supplier(
        supplier_id
    )

    _ensure_supplier_mutation_clinic(
        supplier=supplier,
        clinic_id=clinic_id,
    )

    if not supplier.is_active:
        return supplier

    supplier.is_active = False

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="InventorySupplier",
        entity_id=supplier.id,
        description=(
            f"Inventory supplier '{supplier.name}' "
            "deactivated"
        ),
        old_value={
            "is_active": True,
        },
        new_value={
            "is_active": False,
        },
    )

    return supplier


@transactional
def reactivate_supplier(
    supplier_id: int,
    clinic_id: int | None = None,
) -> InventorySupplier:
    supplier = _get_locked_supplier(
        supplier_id
    )

    _ensure_supplier_mutation_clinic(
        supplier=supplier,
        clinic_id=clinic_id,
    )

    if supplier.is_active:
        return supplier

    supplier.is_active = True

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="InventorySupplier",
        entity_id=supplier.id,
        description=(
            f"Inventory supplier '{supplier.name}' "
            "reactivated"
        ),
        old_value={
            "is_active": False,
        },
        new_value={
            "is_active": True,
        },
    )

    return supplier


# ============================================================================
# INVENTORY BATCHES
# ============================================================================

def get_inventory_batch(
    batch_id: int,
    item_id: int | None = None,
    clinic_id: int | None = None,
) -> InventoryBatch:
    batch = _get_batch(
        batch_id
    )

    item = _get_item(
        batch.item_id
    )

    if item_id is not None:
        item_id = _validate_positive_id(
            item_id,
            "item_id",
        )

        _validate_batch_for_item(
            batch=batch,
            item=_get_item(item_id),
        )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        _validate_item_clinic(
            item,
            clinic_id,
        )

    return batch


def list_inventory_batches(
    item_id: int,
    clinic_id: int | None = None,
    include_inactive: bool = False,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    item_id = _validate_positive_id(
        item_id,
        "item_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    item = get_inventory_item(
        item_id,
        clinic_id,
    )

    statement = select(
        InventoryBatch
    ).where(
        InventoryBatch.item_id == item.id,
    )

    if not include_inactive:
        statement = statement.where(
            InventoryBatch.is_active.is_(True),
        )

    statement = statement.order_by(
        InventoryBatch.expiry_date.asc().nulls_last(),
        InventoryBatch.received_at.asc(),
        InventoryBatch.id.asc(),
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
    )


@transactional
def create_inventory_batch(
    item_id: int,
    batch_number: str,
    unit_cost: Decimal | None = None,
    expiry_date: date | None = None,
    supplier_id: int | None = None,
    clinic_id: int | None = None,
) -> InventoryBatch:
    item = _get_locked_active_item(
        item_id
    )

    _get_active_clinic(
        item.clinic_id
    )

    _validate_item_clinic(
        item,
        clinic_id,
    )

    if (
        not isinstance(batch_number, str)
        or not batch_number.strip()
    ):
        raise ValidationError(
            "Batch number is required"
        )

    batch_number = batch_number.strip()

    statement = select(
        InventoryBatch
    ).where(
        InventoryBatch.item_id == item.id,
        InventoryBatch.batch_number == batch_number,
    )

    existing = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if existing is not None:
        raise ConflictError(
            f"Batch '{batch_number}' already exists "
            f"for inventory item {item.id}"
        )

    if (
        unit_cost is not None
        and unit_cost < 0
    ):
        raise ValidationError(
            "Unit cost cannot be negative"
        )

    _validate_batch_expiry(
        expiry_date
    )

    if supplier_id is not None:
        supplier_id = _validate_positive_id(
            supplier_id,
            "supplier_id",
        )

        supplier = _get_supplier(
            supplier_id
        )

        if not supplier.is_active:
            raise ConflictError(
                f"Supplier {supplier_id} is inactive"
            )

        _validate_supplier_for_clinic(
            supplier,
            item.clinic_id,
        )

    batch = InventoryBatch(
        item_id=item.id,
        supplier_id=supplier_id,
        batch_number=batch_number,
        quantity_on_hand=0,
        unit_cost=unit_cost,
        expiry_date=expiry_date,
    )

    db.session.add(
        batch
    )
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="InventoryBatch",
        entity_id=batch.id,
        description=(
            f"Inventory batch '{batch.batch_number}' "
            "created"
        ),
        new_value={
            "item_id": item.id,
            "supplier_id": supplier_id,
            "expiry_date": (
                expiry_date.isoformat()
                if expiry_date is not None
                else None
            ),
        },
    )

    return batch


@transactional
def update_inventory_batch(
    batch_id: int,
    clinic_id: int | None = None,
    **fields,
) -> InventoryBatch:
    batch = _get_locked_batch(
        batch_id
    )

    item = _get_locked_item(
        batch.item_id
    )

    _get_active_clinic(
        item.clinic_id
    )

    _validate_item_clinic(
        item,
        clinic_id,
    )

    unknown = set(fields) - _BATCH_EDITABLE_FIELDS

    if unknown:
        raise ValidationError(
            "Unknown inventory batch field(s): "
            + ", ".join(sorted(unknown))
        )

    old_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}

    for key, new_value in fields.items():
        if key == "batch_number":
            new_value = _normalize_optional_text(
                new_value
            )

            if not new_value:
                raise ValidationError(
                    "Batch number cannot be empty"
                )

            statement = select(
                InventoryBatch
            ).where(
                InventoryBatch.item_id == batch.item_id,
                InventoryBatch.batch_number == new_value,
                InventoryBatch.id != batch.id,
            )

            duplicate = (
                db.session.execute(statement)
                .scalars()
                .first()
            )

            if duplicate is not None:
                raise ConflictError(
                    f"Batch '{new_value}' already exists "
                    f"for inventory item {item.id}"
                )

        elif key == "unit_cost":
            if (
                new_value is not None
                and new_value < 0
            ):
                raise ValidationError(
                    "Unit cost cannot be negative"
                )

        elif key == "expiry_date":
            _validate_batch_expiry(
                new_value
            )

        elif key == "supplier_id":
            if new_value is not None:
                new_value = _validate_positive_id(
                    new_value,
                    "supplier_id",
                )

                supplier = _get_supplier(
                    new_value
                )

                if not supplier.is_active:
                    raise ConflictError(
                        f"Supplier {new_value} is inactive"
                    )

                _validate_supplier_for_clinic(
                    supplier,
                    item.clinic_id,
                )

        current_value = getattr(
            batch,
            key,
        )

        if current_value == new_value:
            continue

        old_values[key] = current_value
        new_values[key] = new_value

        setattr(
            batch,
            key,
            new_value,
        )

    if new_values:
        old_value, new_value = _build_change_dict(
            old_values,
            new_values,
        )

        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="InventoryBatch",
            entity_id=batch.id,
            description=(
                f"Inventory batch '{batch.batch_number}' "
                "updated"
            ),
            old_value=old_value,
            new_value=new_value,
        )

    return batch


def list_expiring_inventory_batches(
    clinic_id: int,
    days: int = 30,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    _get_active_clinic(
        clinic_id
    )

    if isinstance(days, bool) or not isinstance(days, int):
        raise ValidationError(
            "Days must be an integer"
        )

    if days < 0:
        raise ValidationError(
            "Days cannot be negative"
        )

    today = date.today()

    expiry_limit = today + timedelta(
        days=days
    )

    statement = (
        select(InventoryBatch)
        .join(
            InventoryItem,
            InventoryItem.id
            == InventoryBatch.item_id,
        )
        .where(
            InventoryItem.clinic_id == clinic_id,
            InventoryItem.is_active.is_(True),
            InventoryBatch.is_active.is_(True),
            InventoryBatch.quantity_on_hand > 0,
            InventoryBatch.expiry_date.isnot(None),
            InventoryBatch.expiry_date >= today,
            InventoryBatch.expiry_date <= expiry_limit,
        )
        .order_by(
            InventoryBatch.expiry_date.asc(),
            InventoryBatch.id.asc(),
        )
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
    )


# ============================================================================
# STOCK MOVEMENTS
# ============================================================================

def get_stock_movements(
    item_id: int,
    clinic_id: int | None = None,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    item_id = _validate_positive_id(
        item_id,
        "item_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    item = get_inventory_item(
        item_id,
        clinic_id,
    )

    statement = (
        select(StockMovement)
        .where(
            StockMovement.item_id == item.id,
        )
        .order_by(
            StockMovement.created_at.desc(),
            StockMovement.id.desc(),
        )
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
    )


@transactional
def record_stock_movement(
    *,
    item_id: int,
    movement_type: StockMovementType,
    quantity: int,
    performed_by_id: int,
    batch_id: int | None = None,
    reason: str | None = None,
    reference_type: str | None = None,
    reference_id: int | None = None,
    clinic_id: int | None = None,
) -> StockMovement:
    # Transfer ledger entries are generated only by complete_inventory_transfer.
    if movement_type in (StockMovementType.TRANSFER_IN, StockMovementType.TRANSFER_OUT):
        raise ValidationError("Use the inventory transfer workflow for transfer movements")
    if isinstance(reference_type, str) and reference_type.strip().lower() == "inventory_transfer":
        raise ValidationError("Inventory transfer references are reserved for the transfer workflow")

    item = _get_locked_active_item(
        item_id
    )

    _get_active_clinic(
        item.clinic_id
    )

    _validate_item_clinic(
        item,
        clinic_id,
    )

    performed_by_id = _validate_positive_id(
        performed_by_id,
        "performed_by_id",
    )

    _validate_staff_for_clinic(
        staff_id=performed_by_id,
        clinic_id=item.clinic_id,
    )

    if batch_id is not None:
        batch_id = _validate_positive_id(
            batch_id,
            "batch_id",
        )

    if reference_id is not None:
        reference_id = _validate_positive_id(
            reference_id,
            "reference_id",
        )

    direction, effective_quantity = (
        _resolve_movement_direction(
            movement_type,
            quantity,
        )
    )

    batch = None

    if batch_id is not None:
        batch = _get_locked_batch(
            batch_id
        )

        _validate_batch_for_item(
            batch=batch,
            item=item,
        )

        _validate_batch_active(
            batch
        )

    _validate_stock_invariants(
        item=item,
        batch=batch,
    )

    if direction == StockMovementDirection.OUT:
        if (
            item.quantity_on_hand
            < effective_quantity
        ):
            raise ConflictError(
                f"Insufficient stock for inventory item "
                f"{item.id}. Available: "
                f"{item.quantity_on_hand}, requested: "
                f"{effective_quantity}"
            )

        if (
            batch is not None
            and batch.quantity_on_hand
            < effective_quantity
        ):
            raise ConflictError(
                f"Insufficient stock in batch {batch.id}. "
                f"Available: {batch.quantity_on_hand}, "
                f"requested: {effective_quantity}"
            )

        item.quantity_on_hand -= (
            effective_quantity
        )

        if batch is not None:
            batch.quantity_on_hand -= (
                effective_quantity
            )

    else:
        item.quantity_on_hand += (
            effective_quantity
        )

        if batch is not None:
            batch.quantity_on_hand += (
                effective_quantity
            )

    _validate_stock_invariants(
        item=item,
        batch=batch,
    )

    normalized_reason = _normalize_optional_text(
        reason
    )

    normalized_reference_type = _normalize_optional_text(
        reference_type
    )

    movement = StockMovement(
        item_id=item.id,
        batch_id=(
            batch.id
            if batch is not None
            else None
        ),
        movement_type=movement_type,
        direction=direction,
        quantity=effective_quantity,
        reason=normalized_reason,
        performed_by_id=performed_by_id,
        reference_type=normalized_reference_type,
        reference_id=reference_id,
    )

    db.session.add(
        movement
    )
    db.session.flush()

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="StockMovement",
        entity_id=movement.id,
        description=(
            f"Stock movement recorded for item "
            f"{item.id}: "
            f"{direction.value} "
            f"{effective_quantity}"
        ),
        new_value={
            "item_id": item.id,
            "batch_id": (
                batch.id
                if batch is not None
                else None
            ),
            "movement_type": movement_type.value,
            "direction": direction.value,
            "quantity": effective_quantity,
            "reference_type": normalized_reference_type,
            "reference_id": reference_id,
            "quantity_on_hand": item.quantity_on_hand,
        },
    )

    return movement


# ============================================================================
# INVENTORY TRANSFERS
# ============================================================================

def get_inventory_transfer(
    transfer_id: int,
    clinic_id: int | None = None,
) -> InventoryTransfer:
    transfer_id = _validate_positive_id(
        transfer_id,
        "transfer_id",
    )

    transfer = db.session.get(
        InventoryTransfer,
        transfer_id,
    )

    if transfer is None:
        raise NotFoundError(
            f"Inventory transfer {transfer_id} not found"
        )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        if (
            transfer.source_clinic_id != clinic_id
            and transfer.destination_clinic_id
            != clinic_id
        ):
            raise ValidationError(
                f"Inventory transfer {transfer_id} "
                f"is not associated with clinic {clinic_id}"
            )

    _ensure_transfer_organization(transfer)
    return transfer


def list_inventory_transfers(
    clinic_id: int,
    status: InventoryTransferStatus | None = None,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    _get_active_clinic(
        clinic_id
    )

    # Filter before pagination, including historical cross-tenant records.
    organization = select(Clinic.organization_id).where(Clinic.id == clinic_id).correlate(None).scalar_subquery()
    allowed_clinics = select(Clinic.id).where(Clinic.organization_id == organization)
    statement = select(
        InventoryTransfer
    ).where(
        InventoryTransfer.source_clinic_id.in_(allowed_clinics),
        InventoryTransfer.destination_clinic_id.in_(allowed_clinics),
        or_(
            InventoryTransfer.source_clinic_id
            == clinic_id,
            InventoryTransfer.destination_clinic_id
            == clinic_id,
        )
    )

    if status is not None:
        statement = statement.where(
            InventoryTransfer.status == status,
        )

    statement = statement.order_by(
        InventoryTransfer.created_at.desc(),
        InventoryTransfer.id.desc(),
    )

    return _paginate(
        statement,
        page=page,
        per_page=per_page,
    )


@transactional
def create_inventory_transfer(
    *,
    item_id: int,
    source_clinic_id: int,
    destination_clinic_id: int,
    quantity: int,
    requested_by_id: int,
    batch_id: int | None = None,
    reason: str | None = None,
) -> InventoryTransfer:
    item_id = _validate_positive_id(
        item_id,
        "item_id",
    )

    source_clinic_id = _validate_positive_id(
        source_clinic_id,
        "source_clinic_id",
    )

    destination_clinic_id = _validate_positive_id(
        destination_clinic_id,
        "destination_clinic_id",
    )

    requested_by_id = _validate_positive_id(
        requested_by_id,
        "requested_by_id",
    )

    if batch_id is not None:
        batch_id = _validate_positive_id(
            batch_id,
            "batch_id",
        )

    _validate_positive_quantity(
        quantity
    )

    if source_clinic_id == destination_clinic_id:
        raise ValidationError(
            "Source and destination clinics must be different"
        )

    source_clinic = _get_active_clinic(
        source_clinic_id
    )

    destination_clinic = _get_active_clinic(
        destination_clinic_id
    )
    _ensure_same_organization(source_clinic, destination_clinic)

    item = _get_locked_active_item(
        item_id
    )

    _ensure_same_clinic(
        expected_clinic_id=source_clinic.id,
        actual_clinic_id=item.clinic_id,
        resource_name=(
            f"Inventory item {item.id}"
        ),
    )

    _validate_staff_for_clinic(
        staff_id=requested_by_id,
        clinic_id=source_clinic_id,
    )

    if item.quantity_on_hand < quantity:
        raise ConflictError(
            "Insufficient stock for transfer. "
            f"Available: {item.quantity_on_hand}, "
            f"requested: {quantity}"
        )

    batch = None

    if batch_id is not None:
        batch = _get_locked_batch(
            batch_id
        )

        _validate_batch_for_item(
            batch=batch,
            item=item,
        )

        _validate_batch_active(
            batch
        )

        if batch.quantity_on_hand < quantity:
            raise ConflictError(
                f"Insufficient stock in batch {batch.id}. "
                f"Available: {batch.quantity_on_hand}, "
                f"requested: {quantity}"
            )

    transfer = InventoryTransfer(
        item_id=item.id,
        batch_id=(
            batch.id
            if batch is not None
            else None
        ),
        source_clinic_id=source_clinic_id,
        destination_clinic_id=destination_clinic_id,
        quantity=quantity,
        status=InventoryTransferStatus.PENDING,
        reason=_normalize_optional_text(
            reason
        ),
        requested_by_id=requested_by_id,
        requested_at=_utcnow(),
    )

    db.session.add(
        transfer
    )
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="InventoryTransfer",
        entity_id=transfer.id,
        description=(
            "Inventory transfer requested: "
            f"{source_clinic_id} -> "
            f"{destination_clinic_id}"
        ),
        new_value={
            "item_id": item.id,
            "batch_id": (
                batch.id
                if batch is not None
                else None
            ),
            "quantity": quantity,
            "source_clinic_id": source_clinic_id,
            "destination_clinic_id": destination_clinic_id,
            "requested_by_id": requested_by_id,
        },
    )

    return transfer


@transactional
def approve_inventory_transfer(
    transfer_id: int,
    approved_by_id: int,
    clinic_id: int | None = None,
) -> InventoryTransfer:
    transfer = _get_locked_transfer(
        transfer_id
    )
    _ensure_transfer_organization(transfer)

    _get_active_clinic(
        transfer.source_clinic_id
    )

    approved_by_id = _validate_positive_id(
        approved_by_id,
        "approved_by_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        _ensure_same_clinic(
            expected_clinic_id=clinic_id,
            actual_clinic_id=transfer.source_clinic_id,
            resource_name=(
                f"Inventory transfer {transfer.id}"
            ),
        )

    if (
        transfer.status
        != InventoryTransferStatus.PENDING
    ):
        raise ConflictError(
            f"Transfer {transfer.id} cannot be approved "
            f"from status '{transfer.status.value}'"
        )

    _validate_staff_for_clinic(
        staff_id=approved_by_id,
        clinic_id=transfer.source_clinic_id,
    )

    if approved_by_id == transfer.requested_by_id:
        raise ValidationError(
            "The staff member who requested a transfer "
            "cannot approve the same transfer"
        )

    transfer.status = (
        InventoryTransferStatus.APPROVED
    )
    transfer.approved_by_id = approved_by_id
    transfer.approved_at = _utcnow()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="InventoryTransfer",
        entity_id=transfer.id,
        description=(
            f"Inventory transfer {transfer.id} approved"
        ),
        old_value={
            "status": (
                InventoryTransferStatus.PENDING.value
            ),
        },
        new_value={
            "status": (
                InventoryTransferStatus.APPROVED.value
            ),
            "approved_by_id": approved_by_id,
        },
    )

    return transfer


def _get_or_create_destination_item(
    *,
    source_item: InventoryItem,
    destination_clinic_id: int,
) -> InventoryItem:
    destination_clinic_id = _validate_positive_id(
        destination_clinic_id,
        "destination_clinic_id",
    )

    statement = (
        select(InventoryItem)
        .where(
            InventoryItem.clinic_id
            == destination_clinic_id,
            InventoryItem.name
            == source_item.name,
            InventoryItem.category
            == source_item.category,
        )
        .with_for_update()
    )

    destination_item = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if destination_item is not None:
        if not destination_item.is_active:
            raise ConflictError(
                f"Destination inventory item "
                f"{destination_item.id} is inactive"
            )

        return destination_item

    destination_item = InventoryItem(
        clinic_id=destination_clinic_id,
        name=source_item.name,
        category=source_item.category,
        sku=None,
        barcode=None,
        unit=source_item.unit,
        quantity_on_hand=0,
        reorder_level=source_item.reorder_level,
        is_active=True,
    )

    db.session.add(
        destination_item
    )
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="InventoryItem",
        entity_id=destination_item.id,
        description=(
            "Destination inventory item created for "
            f"transfer from source item {source_item.id}"
        ),
        new_value={
            "source_item_id": source_item.id,
            "destination_clinic_id": destination_clinic_id,
        },
    )

    return destination_item


def _get_or_create_destination_batch(
    *,
    source_batch: InventoryBatch,
    destination_item: InventoryItem,
) -> InventoryBatch:
    statement = (
        select(InventoryBatch)
        .where(
            InventoryBatch.item_id
            == destination_item.id,
            InventoryBatch.batch_number
            == source_batch.batch_number,
        )
        .with_for_update()
    )

    destination_batch = (
        db.session.execute(statement)
        .scalars()
        .first()
    )

    if destination_batch is not None:
        if not destination_batch.is_active:
            raise ConflictError(
                f"Destination batch "
                f"{destination_batch.id} is inactive"
            )

        return destination_batch

    destination_supplier_id = None

    if source_batch.supplier_id is not None:
        source_supplier = _get_supplier(
            source_batch.supplier_id
        )

        if (
            source_supplier.clinic_id is None
            or source_supplier.clinic_id
            == destination_item.clinic_id
        ):
            destination_supplier_id = (
                source_supplier.id
            )

    destination_batch = InventoryBatch(
        item_id=destination_item.id,
        supplier_id=destination_supplier_id,
        batch_number=source_batch.batch_number,
        quantity_on_hand=0,
        unit_cost=source_batch.unit_cost,
        expiry_date=source_batch.expiry_date,
        received_at=_utcnow(),
        is_active=True,
    )

    db.session.add(
        destination_batch
    )
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="InventoryBatch",
        entity_id=destination_batch.id,
        description=(
            f"Destination batch "
            f"'{destination_batch.batch_number}' "
            f"created from source batch {source_batch.id}"
        ),
        new_value={
            "source_batch_id": source_batch.id,
            "destination_item_id": destination_item.id,
            "supplier_id": destination_supplier_id,
        },
    )

    return destination_batch


@transactional
def complete_inventory_transfer(
    transfer_id: int,
    performed_by_id: int,
    clinic_id: int | None = None,
) -> InventoryTransfer:
    transfer = _get_locked_transfer(
        transfer_id
    )
    _ensure_transfer_organization(transfer)

    _get_active_clinic(
        transfer.source_clinic_id
    )

    _get_active_clinic(
        transfer.destination_clinic_id
    )

    performed_by_id = _validate_positive_id(
        performed_by_id,
        "performed_by_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        _ensure_same_clinic(
            expected_clinic_id=clinic_id,
            actual_clinic_id=transfer.source_clinic_id,
            resource_name=(
                f"Inventory transfer {transfer.id}"
            ),
        )

    if transfer.status not in (
        InventoryTransferStatus.APPROVED,
        InventoryTransferStatus.IN_TRANSIT,
    ):
        raise ConflictError(
            f"Transfer {transfer.id} cannot be completed "
            f"from status '{transfer.status.value}'"
        )

    _validate_staff_for_clinic(
        staff_id=performed_by_id,
        clinic_id=transfer.source_clinic_id,
    )

    source_item = _get_locked_active_item(
        transfer.item_id
    )

    _ensure_same_clinic(
        expected_clinic_id=transfer.source_clinic_id,
        actual_clinic_id=source_item.clinic_id,
        resource_name=(
            f"Inventory item {source_item.id}"
        ),
    )

    source_batch = None

    if transfer.batch_id is not None:
        source_batch = _get_locked_batch(
            transfer.batch_id
        )

        _validate_batch_for_item(
            batch=source_batch,
            item=source_item,
        )

        _validate_batch_active(
            source_batch
        )

    _validate_stock_invariants(
        item=source_item,
        batch=source_batch,
    )

    if (
        source_item.quantity_on_hand
        < transfer.quantity
    ):
        raise ConflictError(
            f"Insufficient source stock for transfer "
            f"{transfer.id}. Available: "
            f"{source_item.quantity_on_hand}, required: "
            f"{transfer.quantity}"
        )

    if (
        source_batch is not None
        and source_batch.quantity_on_hand
        < transfer.quantity
    ):
        raise ConflictError(
            f"Insufficient source batch stock for "
            f"transfer {transfer.id}. Available: "
            f"{source_batch.quantity_on_hand}, required: "
            f"{transfer.quantity}"
        )

    destination_item = _get_or_create_destination_item(
        source_item=source_item,
        destination_clinic_id=(
            transfer.destination_clinic_id
        ),
    )

    _ensure_same_clinic(
        expected_clinic_id=(
            transfer.destination_clinic_id
        ),
        actual_clinic_id=destination_item.clinic_id,
        resource_name=(
            f"Destination inventory item "
            f"{destination_item.id}"
        ),
    )

    destination_batch = None

    if source_batch is not None:
        destination_batch = (
            _get_or_create_destination_batch(
                source_batch=source_batch,
                destination_item=destination_item,
            )
        )

        _validate_batch_for_item(
            batch=destination_batch,
            item=destination_item,
        )

        _validate_batch_active(
            destination_batch
        )

    old_status = transfer.status.value

    source_item.quantity_on_hand -= (
        transfer.quantity
    )

    if source_batch is not None:
        source_batch.quantity_on_hand -= (
            transfer.quantity
        )

    _validate_stock_invariants(
        item=source_item,
        batch=source_batch,
    )

    source_movement = StockMovement(
        item_id=source_item.id,
        batch_id=(
            source_batch.id
            if source_batch is not None
            else None
        ),
        movement_type=StockMovementType.TRANSFER_OUT,
        direction=StockMovementDirection.OUT,
        quantity=transfer.quantity,
        reason=transfer.reason,
        performed_by_id=performed_by_id,
        reference_type="inventory_transfer",
        reference_id=transfer.id,
    )

    db.session.add(
        source_movement
    )

    destination_item.quantity_on_hand += (
        transfer.quantity
    )

    if destination_batch is not None:
        destination_batch.quantity_on_hand += (
            transfer.quantity
        )

    _validate_stock_invariants(
        item=destination_item,
        batch=destination_batch,
    )

    destination_movement = StockMovement(
        item_id=destination_item.id,
        batch_id=(
            destination_batch.id
            if destination_batch is not None
            else None
        ),
        movement_type=StockMovementType.TRANSFER_IN,
        direction=StockMovementDirection.IN,
        quantity=transfer.quantity,
        reason=transfer.reason,
        performed_by_id=performed_by_id,
        reference_type="inventory_transfer",
        reference_id=transfer.id,
    )

    db.session.add(
        destination_movement
    )

    transfer.status = (
        InventoryTransferStatus.COMPLETED
    )
    transfer.completed_at = _utcnow()

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="InventoryTransfer",
        entity_id=transfer.id,
        description=(
            f"Inventory transfer {transfer.id} completed"
        ),
        old_value={
            "status": old_status,
        },
        new_value={
            "status": (
                InventoryTransferStatus.COMPLETED.value
            ),
            "performed_by_id": performed_by_id,
            "destination_item_id": (
                destination_item.id
            ),
            "destination_batch_id": (
                destination_batch.id
                if destination_batch is not None
                else None
            ),
        },
    )

    return transfer


@transactional
def cancel_inventory_transfer(
    transfer_id: int,
    cancelled_by_id: int,
    reason: str | None = None,
    clinic_id: int | None = None,
) -> InventoryTransfer:
    transfer = _get_locked_transfer(
        transfer_id
    )
    _ensure_transfer_organization(transfer)

    _get_active_clinic(
        transfer.source_clinic_id
    )

    cancelled_by_id = _validate_positive_id(
        cancelled_by_id,
        "cancelled_by_id",
    )

    if clinic_id is not None:
        clinic_id = _validate_positive_id(
            clinic_id,
            "clinic_id",
        )

        _ensure_same_clinic(
            expected_clinic_id=clinic_id,
            actual_clinic_id=transfer.source_clinic_id,
            resource_name=(
                f"Inventory transfer {transfer.id}"
            ),
        )

    if transfer.status in (
        InventoryTransferStatus.COMPLETED,
        InventoryTransferStatus.CANCELLED,
    ):
        raise ConflictError(
            f"Transfer {transfer.id} cannot be cancelled "
            f"from status '{transfer.status.value}'"
        )

    _validate_staff_for_clinic(
        staff_id=cancelled_by_id,
        clinic_id=transfer.source_clinic_id,
    )

    normalized_reason = None

    if reason is not None:
        normalized_reason = _normalize_optional_text(
            reason
        )

    old_status = transfer.status.value

    transfer.status = (
        InventoryTransferStatus.CANCELLED
    )
    transfer.cancelled_at = _utcnow()

    if reason is not None:
        transfer.reason = normalized_reason

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="InventoryTransfer",
        entity_id=transfer.id,
        description=(
            f"Inventory transfer {transfer.id} cancelled"
            + (
                f": {normalized_reason}"
                if normalized_reason
                else ""
            )
        ),
        old_value={
            "status": old_status,
        },
        new_value={
            "status": (
                InventoryTransferStatus.CANCELLED.value
            ),
            "cancelled_by_id": cancelled_by_id,
            "reason": normalized_reason,
        },
    )

    return transfer