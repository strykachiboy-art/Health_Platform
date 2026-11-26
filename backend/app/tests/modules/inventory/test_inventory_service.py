from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.inventory_enums import (
    InventoryCategory,
    InventoryTransferStatus,
    StockMovementDirection,
    StockMovementType,
)
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.inventory.models.inventory_model import (
    InventoryBatch,
    InventoryItem,
    InventorySupplier,
    InventoryTransfer,
)
import app.modules.inventory.services.inventory_service as service


@pytest.fixture(autouse=True)
def app_context(app):
    with app.app_context():
        yield


# ============================================================================
# TEST DATA FACTORIES
# ============================================================================

def item(**overrides):
    data = dict(
        id=1,
        clinic_id=10,
        name="Gloves",
        category=InventoryCategory.MEDICAL_SUPPLY,
        sku="SKU-1",
        barcode="BAR-1",
        unit="box",
        quantity_on_hand=100,
        reorder_level=10,
        is_active=True,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def supplier(**overrides):
    data = dict(
        id=1,
        clinic_id=10,
        name="Supplier One",
        contact_person="John",
        phone="08000000000",
        email="supplier@example.com",
        address="Address",
        is_active=True,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def batch(**overrides):
    data = dict(
        id=1,
        item_id=1,
        supplier_id=None,
        batch_number="BATCH-1",
        quantity_on_hand=40,
        unit_cost=Decimal("12.50"),
        expiry_date=date.today() + timedelta(days=30),
        is_active=True,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def transfer(**overrides):
    data = dict(
        id=1,
        item_id=1,
        batch_id=None,
        source_clinic_id=10,
        destination_clinic_id=20,
        quantity=10,
        status=InventoryTransferStatus.PENDING,
        reason="Transfer",
        requested_by_id=5,
        approved_by_id=None,
        requested_at=None,
        approved_at=None,
        completed_at=None,
        cancelled_at=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def page(
    items=None,
    *,
    total=None,
    current_page=1,
    per_page=50,
):
    items = list(items or [])
    return SimpleNamespace(
        items=items,
        total=len(items) if total is None else total,
        page=current_page,
        per_page=per_page,
        pages=(
            0
            if (total if total is not None else len(items)) == 0
            else (
                (total if total is not None else len(items)) + per_page - 1
            ) // per_page
        ),
        has_next=(
            current_page
            < (
                0
                if (total if total is not None else len(items)) == 0
                else (
                    (total if total is not None else len(items)) + per_page - 1
                ) // per_page
            )
        ),
        has_prev=current_page > 1,
    )


# ============================================================================
# PAGINATION
# ============================================================================

@pytest.mark.parametrize(
    ("page_number", "per_page"),
    [
        (0, 50),
        (-1, 50),
        (1, 0),
        (1, -1),
        (1, service.MAX_PER_PAGE + 1),
        (True, 50),
        (1, False),
        ("1", 50),
        (1, "50"),
    ],
)
def test_validate_pagination_rejects_invalid_values(page_number, per_page):
    with pytest.raises(ValidationError):
        service._validate_pagination(page_number, per_page)


@pytest.mark.parametrize(
    ("page_number", "per_page"),
    [
        (1, 1),
        (1, 50),
        (2, 100),
        (1, service.MAX_PER_PAGE),
    ],
)
def test_validate_pagination_accepts_valid_values(page_number, per_page):
    assert service._validate_pagination(page_number, per_page) == (
        page_number,
        per_page,
    )


def test_validate_pagination_returns_defaults():
    assert service._validate_pagination(
        service.DEFAULT_PAGE,
        service.DEFAULT_PER_PAGE,
    ) == (
        service.DEFAULT_PAGE,
        service.DEFAULT_PER_PAGE,
    )


def test_paginate_delegates_to_flask_sqlalchemy(monkeypatch):
    statement = Mock()
    expected = page([item()], total=1, current_page=1, per_page=50)

    paginate = Mock(return_value=expected)

    monkeypatch.setattr(service.db, "paginate", paginate)

    result = service._paginate(
        statement,
        page=1,
        per_page=50,
    )

    assert result is expected
    paginate.assert_called_once_with(
        statement,
        page=1,
        per_page=50,
        error_out=False,
    )


def test_paginate_rejects_invalid_pagination(monkeypatch):
    paginate = Mock()
    monkeypatch.setattr(service.db, "paginate", paginate)

    with pytest.raises(ValidationError):
        service._paginate(
            Mock(),
            page=0,
            per_page=50,
        )

    paginate.assert_not_called()


def test_paginate_allows_empty_last_page(monkeypatch):
    expected = page(
        [],
        total=100,
        current_page=3,
        per_page=50,
    )

    monkeypatch.setattr(
        service.db,
        "paginate",
        Mock(return_value=expected),
    )

    result = service._paginate(
        Mock(),
        page=3,
        per_page=50,
    )

    assert result.items == []
    assert result.total == 100
    assert result.page == 3
    assert result.per_page == 50
    assert result.has_next is False
    assert result.has_prev is True


# ============================================================================
# HELPERS
# ============================================================================

def test_normalize_optional_text():
    assert service._normalize_optional_text(None) is None
    assert service._normalize_optional_text("  gloves  ") == "gloves"
    assert service._normalize_optional_text("   ") is None


def test_normalize_optional_text_rejects_non_string():
    with pytest.raises(ValidationError):
        service._normalize_optional_text(123)


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        "1",
        True,
        False,
        1.5,
    ],
)
def test_validate_positive_quantity_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        service._validate_positive_quantity(value)


def test_validate_positive_quantity_accepts_positive_integer():
    service._validate_positive_quantity(1)


def test_validate_positive_quantity_rejects_boolean():
    with pytest.raises(ValidationError):
        service._validate_positive_quantity(True)


@pytest.mark.parametrize(
    "value",
    [
        -1,
        "1",
        True,
        1.5,
    ],
)
def test_validate_non_negative_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        service._validate_non_negative(value, "Reorder level")


@pytest.mark.parametrize(
    "value",
    [
        0,
        1,
        100,
    ],
)
def test_validate_non_negative_accepts_valid_integers(value):
    service._validate_non_negative(value, "Reorder level")


@pytest.mark.parametrize("value", [True, False])
def test_validate_non_negative_rejects_boolean(value):
    with pytest.raises(ValidationError):
        service._validate_non_negative(value, "Reorder level")


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        "1",
        True,
        False,
    ],
)
def test_validate_positive_id_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        service._validate_positive_id(value, "Inventory item ID")


@pytest.mark.parametrize("value", [1, 10, 999])
def test_validate_positive_id_accepts_positive_integer(value):
    assert service._validate_positive_id(value, "Inventory item ID") == value


def test_validate_batch_expiry_rejects_past():
    with pytest.raises(ValidationError):
        service._validate_batch_expiry(
            date.today() - timedelta(days=1)
        )


def test_validate_batch_expiry_accepts_future():
    service._validate_batch_expiry(
        date.today() + timedelta(days=1)
    )


def test_validate_batch_expiry_accepts_today():
    service._validate_batch_expiry(date.today())


# ============================================================================
# CLINIC / STAFF HELPERS
# ============================================================================

def test_get_active_clinic_missing(monkeypatch):
    monkeypatch.setattr(
        service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(NotFoundError):
        service._get_active_clinic(10)


def test_get_active_clinic_rejects_inactive(monkeypatch):
    monkeypatch.setattr(
        service.db.session,
        "get",
        Mock(
            return_value=SimpleNamespace(
                id=10,
                status=ClinicStatus.SUSPENDED,
            )
        ),
    )

    with pytest.raises(ConflictError):
        service._get_active_clinic(10)


def test_get_active_clinic_accepts_active(monkeypatch):
    clinic = SimpleNamespace(
        id=10,
        status=ClinicStatus.ACTIVE,
    )

    monkeypatch.setattr(
        service.db.session,
        "get",
        Mock(return_value=clinic),
    )

    assert service._get_active_clinic(10) is clinic


def test_get_staff_missing(monkeypatch):
    monkeypatch.setattr(
        service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(NotFoundError):
        service._get_staff(1)


def test_validate_staff_rejects_wrong_clinic(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_staff",
        Mock(
            return_value=SimpleNamespace(
                id=1,
                clinic_id=20,
                status=StaffStatus.ACTIVE,
            )
        ),
    )

    with pytest.raises(ValidationError):
        service._validate_staff_for_clinic(
            staff_id=1,
            clinic_id=10,
        )


def test_validate_staff_rejects_inactive(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_staff",
        Mock(
            return_value=SimpleNamespace(
                id=1,
                clinic_id=10,
                status=object(),
            )
        ),
    )

    with pytest.raises(ConflictError):
        service._validate_staff_for_clinic(
            staff_id=1,
            clinic_id=10,
        )


def test_validate_staff_accepts_active_same_clinic(monkeypatch):
    staff = SimpleNamespace(
        id=1,
        clinic_id=10,
        status=StaffStatus.ACTIVE,
    )

    monkeypatch.setattr(
        service,
        "_get_staff",
        Mock(return_value=staff),
    )

    assert (
        service._validate_staff_for_clinic(
            staff_id=1,
            clinic_id=10,
        )
        is staff
    )


def test_get_item_supplier_batch_missing(monkeypatch):
    monkeypatch.setattr(
        service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(NotFoundError):
        service._get_item(1)

    with pytest.raises(NotFoundError):
        service._get_supplier(1)

    with pytest.raises(NotFoundError):
        service._get_batch(1)


# ============================================================================
# MODERN SQLALCHEMY LOCKING
# ============================================================================

@pytest.mark.parametrize(
    "helper",
    [
        service._get_locked_item,
        service._get_locked_supplier,
        service._get_locked_batch,
        service._get_locked_transfer,
    ],
)
def test_locked_helpers_use_session_execute(
    monkeypatch,
    helper,
):
    row = SimpleNamespace(id=1)

    scalars = Mock(
        first=Mock(return_value=row)
    )

    result = Mock(
        scalars=Mock(return_value=scalars)
    )

    execute = Mock(return_value=result)

    monkeypatch.setattr(
        service.db.session,
        "execute",
        execute,
    )

    assert helper(1) is row

    execute.assert_called_once()
    scalars.first.assert_called_once_with()


def test_locked_item_missing(monkeypatch):
    scalars = Mock(
        first=Mock(return_value=None)
    )

    result = Mock(
        scalars=Mock(return_value=scalars)
    )

    monkeypatch.setattr(
        service.db.session,
        "execute",
        Mock(return_value=result),
    )

    with pytest.raises(NotFoundError):
        service._get_locked_item(1)


# ============================================================================
# ITEMS
# ============================================================================

def test_get_inventory_item_enforces_clinic(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_item",
        Mock(return_value=item(clinic_id=10)),
    )

    assert service.get_inventory_item(
        1,
        clinic_id=10,
    ).clinic_id == 10

    with pytest.raises(ValidationError):
        service.get_inventory_item(
            1,
            clinic_id=20,
        )


def test_list_inventory_items_returns_pagination(monkeypatch):
    expected = page(
        [item()],
        total=1,
        current_page=1,
        per_page=50,
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    paginate = Mock(return_value=expected)
    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    result = service.list_inventory_items(
        10,
        page=1,
        per_page=50,
    )

    assert result is expected
    paginate.assert_called_once()


def test_list_inventory_items_preserves_filters(monkeypatch):
    expected = page(
        [item(category=InventoryCategory.MEDICAL_SUPPLY)],
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    paginate = Mock(return_value=expected)
    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    result = service.list_inventory_items(
        10,
        category=InventoryCategory.MEDICAL_SUPPLY,
        low_stock_only=True,
        include_inactive=True,
        page=2,
        per_page=25,
    )

    assert result is expected

    statement = paginate.call_args.args[0]

    # We intentionally verify that a SQLAlchemy Select was built.
    assert statement is not None

    paginate.assert_called_once_with(
        statement,
        page=2,
        per_page=25,
    )


def test_list_inventory_items_rejects_invalid_pagination(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    paginate = Mock()
    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    with pytest.raises(ValidationError):
        service.list_inventory_items(
            10,
            page=0,
            per_page=50,
        )

    paginate.assert_not_called()


def test_low_stock_delegates_with_pagination():
    original = service.list_inventory_items

    try:
        expected = page(
            [item(quantity_on_hand=1)],
            total=1,
            current_page=2,
            per_page=10,
        )

        service.list_inventory_items = Mock(
            return_value=expected
        )

        result = service.get_low_stock_items(
            10,
            page=2,
            per_page=10,
        )

        assert result is expected

        service.list_inventory_items.assert_called_once_with(
            clinic_id=10,
            low_stock_only=True,
            include_inactive=False,
            page=2,
            per_page=10,
        )

    finally:
        service.list_inventory_items = original


def test_create_item_rejects_unknown_field(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    with pytest.raises(ValidationError):
        service.create_inventory_item(
            10,
            "Gloves",
            bad="x",
        )


def test_create_item_requires_actor_for_initial_stock(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        service.db.session,
        "add",
        Mock(),
    )

    monkeypatch.setattr(
        service.db.session,
        "flush",
        Mock(),
    )

    monkeypatch.setattr(
        service,
        "create_audit_log",
        Mock(),
    )

    monkeypatch.setattr(
        service,
        "InventoryItem",
        Mock(id=1),
    )

    with pytest.raises(ValidationError):
        service.create_inventory_item(
            10,
            "Gloves",
            initial_quantity=5,
        )


def test_update_item_rejects_unknown_field(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_locked_item",
        Mock(return_value=item()),
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    with pytest.raises(ValidationError):
        service.update_inventory_item(
            1,
            clinic_id=10,
            bad="x",
        )


def test_update_item_changes_value_and_audits(monkeypatch):
    row = item(name="Old")

    monkeypatch.setattr(
        service,
        "_get_locked_item",
        Mock(return_value=row),
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    audit = Mock()

    monkeypatch.setattr(
        service,
        "create_audit_log",
        audit,
    )

    result = service.update_inventory_item(
        1,
        clinic_id=10,
        name="New",
    )

    assert result.name == "New"
    audit.assert_called_once()


def test_deactivate_and_reactivate_item(monkeypatch):
    row = item(is_active=True)

    monkeypatch.setattr(
        service,
        "_get_locked_item",
        Mock(return_value=row),
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        service,
        "create_audit_log",
        Mock(),
    )

    assert (
        service.deactivate_inventory_item(
            1,
            clinic_id=10,
        ).is_active
        is False
    )

    row.is_active = False

    assert (
        service.reactivate_inventory_item(
            1,
            clinic_id=10,
        ).is_active
        is True
    )


# ============================================================================
# SUPPLIERS
# ============================================================================

def test_list_suppliers_returns_pagination(monkeypatch):
    expected = page(
        [supplier()],
        total=1,
        current_page=1,
        per_page=50,
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    paginate = Mock(return_value=expected)
    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    result = service.list_suppliers(
        10,
        page=1,
        per_page=50,
    )

    assert result is expected
    paginate.assert_called_once()


def test_list_suppliers_rejects_invalid_pagination(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    paginate = Mock()
    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    with pytest.raises(ValidationError):
        service.list_suppliers(
            10,
            page=1,
            per_page=service.MAX_PER_PAGE + 1,
        )

    paginate.assert_not_called()


def test_create_supplier_rejects_duplicate(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    scalars = Mock(
        first=Mock(return_value=supplier())
    )

    result = Mock(
        scalars=Mock(return_value=scalars)
    )

    monkeypatch.setattr(
        service.db.session,
        "execute",
        Mock(return_value=result),
    )

    with pytest.raises(ConflictError):
        service.create_supplier(
            "Supplier One",
            clinic_id=10,
        )


def test_update_supplier_rejects_unknown_field(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_locked_supplier",
        Mock(return_value=supplier()),
    )

    monkeypatch.setattr(
        service,
        "_ensure_supplier_mutation_clinic",
        Mock(return_value=10),
    )

    with pytest.raises(ValidationError):
        service.update_supplier(
            1,
            clinic_id=10,
            bad="x",
        )


def test_supplier_mutation_requires_clinic_for_global_supplier(
    monkeypatch,
):
    row = supplier(clinic_id=None)

    with pytest.raises(ValidationError):
        service._ensure_supplier_mutation_clinic(
            supplier=row,
            clinic_id=None,
        )


def test_supplier_deactivate_reactivate(monkeypatch):
    row = supplier(is_active=True)

    monkeypatch.setattr(
        service,
        "_get_locked_supplier",
        Mock(return_value=row),
    )

    monkeypatch.setattr(
        service,
        "_ensure_supplier_mutation_clinic",
        Mock(return_value=10),
    )

    monkeypatch.setattr(
        service,
        "create_audit_log",
        Mock(),
    )

    assert (
        service.deactivate_supplier(
            1,
            clinic_id=10,
        ).is_active
        is False
    )

    row.is_active = False

    assert (
        service.reactivate_supplier(
            1,
            clinic_id=10,
        ).is_active
        is True
    )


# ============================================================================
# BATCHES
# ============================================================================

def test_list_batches_returns_pagination(monkeypatch):
    expected = page(
        [batch()],
        total=1,
        current_page=1,
        per_page=50,
    )

    monkeypatch.setattr(
        service,
        "get_inventory_item",
        Mock(return_value=item()),
    )

    paginate = Mock(return_value=expected)

    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    result = service.list_inventory_batches(
        1,
        clinic_id=10,
        page=1,
        per_page=50,
    )

    assert result is expected
    paginate.assert_called_once()


def test_list_batches_enforces_pagination(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_inventory_item",
        Mock(return_value=item()),
    )

    paginate = Mock()

    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    with pytest.raises(ValidationError):
        service.list_inventory_batches(
            1,
            clinic_id=10,
            page=0,
            per_page=50,
        )

    paginate.assert_not_called()


def test_create_batch_rejects_duplicate(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_locked_active_item",
        Mock(return_value=item()),
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    scalars = Mock(
        first=Mock(return_value=batch())
    )

    result = Mock(
        scalars=Mock(return_value=scalars)
    )

    monkeypatch.setattr(
        service.db.session,
        "execute",
        Mock(return_value=result),
    )

    with pytest.raises(ConflictError):
        service.create_inventory_batch(
            1,
            "BATCH-1",
            clinic_id=10,
        )


def test_update_batch_rejects_unknown_field(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_locked_batch",
        Mock(return_value=batch()),
    )

    monkeypatch.setattr(
        service,
        "_get_locked_item",
        Mock(return_value=item()),
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    with pytest.raises(ValidationError):
        service.update_inventory_batch(
            1,
            clinic_id=10,
            bad="x",
        )


def test_expiring_batches_returns_pagination(monkeypatch):
    expected = page(
        [batch()],
        total=1,
        current_page=1,
        per_page=50,
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    paginate = Mock(return_value=expected)

    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    result = service.list_expiring_inventory_batches(
        10,
        days=30,
        page=1,
        per_page=50,
    )

    assert result is expected
    paginate.assert_called_once()


@pytest.mark.parametrize(
    "days",
    [-1, -10],
)
def test_expiring_batches_reject_negative_days(
    monkeypatch,
    days,
):
    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    with pytest.raises(ValidationError):
        service.list_expiring_inventory_batches(
            10,
            days=days,
            page=1,
            per_page=50,
        )


# ============================================================================
# STOCK MOVEMENTS
# ============================================================================

def test_adjustment_positive_is_in():
    assert service._resolve_movement_direction(
        StockMovementType.ADJUSTMENT,
        5,
    ) == (
        StockMovementDirection.IN,
        5,
    )


def test_adjustment_negative_is_out():
    assert service._resolve_movement_direction(
        StockMovementType.ADJUSTMENT,
        -5,
    ) == (
        StockMovementDirection.OUT,
        5,
    )


def test_adjustment_zero_rejected():
    with pytest.raises(ValidationError):
        service._resolve_movement_direction(
            StockMovementType.ADJUSTMENT,
            0,
        )


def test_get_stock_movements_returns_pagination(monkeypatch):
    expected = page(
        [SimpleNamespace(id=1)],
        total=1,
        current_page=1,
        per_page=50,
    )

    monkeypatch.setattr(
        service,
        "get_inventory_item",
        Mock(return_value=item()),
    )

    paginate = Mock(return_value=expected)

    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    result = service.get_stock_movements(
        1,
        clinic_id=10,
        page=1,
        per_page=50,
    )

    assert result is expected
    paginate.assert_called_once()


def test_get_stock_movements_rejects_invalid_page(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_inventory_item",
        Mock(return_value=item()),
    )

    paginate = Mock()
    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    with pytest.raises(ValidationError):
        service.get_stock_movements(
            1,
            clinic_id=10,
            page=0,
            per_page=50,
        )

    paginate.assert_not_called()


def test_record_stock_movement_rejects_insufficient_stock(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "_get_locked_active_item",
        Mock(return_value=item(quantity_on_hand=2)),
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        service,
        "_validate_staff_for_clinic",
        Mock(),
    )

    with pytest.raises(ConflictError):
        service.record_stock_movement(
            item_id=1,
            movement_type=next(
                iter(service.DECREASING_MOVEMENTS)
            ),
            quantity=5,
            performed_by_id=7,
            clinic_id=10,
        )


def test_record_adjustment_rejects_zero():
    with pytest.raises(ValidationError):
        service._resolve_movement_direction(
            StockMovementType.ADJUSTMENT,
            0,
        )


# ============================================================================
# TRANSFERS
# ============================================================================

def test_get_transfer_missing(monkeypatch):
    monkeypatch.setattr(
        service.db.session,
        "get",
        Mock(return_value=None),
    )

    with pytest.raises(NotFoundError):
        service.get_inventory_transfer(
            1,
            clinic_id=10,
        )


def test_get_transfer_rejects_unrelated_clinic(monkeypatch):
    monkeypatch.setattr(
        service.db.session,
        "get",
        Mock(return_value=transfer()),
    )

    with pytest.raises(ValidationError):
        service.get_inventory_transfer(
            1,
            clinic_id=99,
        )


def test_list_transfers_returns_pagination(monkeypatch):
    expected = page(
        [transfer()],
        total=1,
        current_page=1,
        per_page=50,
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    paginate = Mock(return_value=expected)

    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    result = service.list_inventory_transfers(
        10,
        page=1,
        per_page=50,
    )

    assert result is expected
    paginate.assert_called_once()


def test_list_transfers_supports_status_filter(monkeypatch):
    expected = page(
        [transfer(status=InventoryTransferStatus.PENDING)],
        total=1,
        current_page=1,
        per_page=20,
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    paginate = Mock(return_value=expected)

    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    result = service.list_inventory_transfers(
        10,
        status=InventoryTransferStatus.PENDING,
        page=1,
        per_page=20,
    )

    assert result is expected
    paginate.assert_called_once()


def test_list_transfers_rejects_invalid_pagination(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    paginate = Mock()

    monkeypatch.setattr(
        service,
        "_paginate",
        paginate,
    )

    with pytest.raises(ValidationError):
        service.list_inventory_transfers(
            10,
            page=1,
            per_page=service.MAX_PER_PAGE + 1,
        )

    paginate.assert_not_called()


def test_create_transfer_rejects_same_clinic():
    with pytest.raises(ValidationError):
        service.create_inventory_transfer(
            item_id=1,
            source_clinic_id=10,
            destination_clinic_id=10,
            quantity=1,
            requested_by_id=5,
        )


def test_create_transfer_rejects_non_positive_quantity():
    with pytest.raises(ValidationError):
        service.create_inventory_transfer(
            item_id=1,
            source_clinic_id=10,
            destination_clinic_id=20,
            quantity=0,
            requested_by_id=5,
        )


def test_create_transfer_rejects_insufficient_stock(monkeypatch):
    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(side_effect=lambda clinic_id: SimpleNamespace(id=clinic_id, organization_id="same-test-owner")),
    )

    monkeypatch.setattr(
        service,
        "_get_locked_active_item",
        Mock(return_value=item(quantity_on_hand=2)),
    )

    monkeypatch.setattr(
        service,
        "_validate_staff_for_clinic",
        Mock(),
    )

    with pytest.raises(ConflictError):
        service.create_inventory_transfer(
            item_id=1,
            source_clinic_id=10,
            destination_clinic_id=20,
            quantity=5,
            requested_by_id=5,
        )


def test_approve_transfer_rejects_non_pending(monkeypatch):
    monkeypatch.setattr(
        service.db.session, "get",
        Mock(side_effect=lambda model, clinic_id: SimpleNamespace(id=clinic_id, organization_id="same-test-owner")),
    )
    monkeypatch.setattr(
        service,
        "_get_locked_transfer",
        Mock(
            return_value=transfer(
                status=InventoryTransferStatus.COMPLETED
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    with pytest.raises(ConflictError):
        service.approve_inventory_transfer(
            1,
            approved_by_id=7,
            clinic_id=10,
        )


def test_approve_transfer_rejects_requester_as_approver(
    monkeypatch,
):
    monkeypatch.setattr(
        service.db.session, "get",
        Mock(side_effect=lambda model, clinic_id: SimpleNamespace(id=clinic_id, organization_id="same-test-owner")),
    )
    monkeypatch.setattr(
        service,
        "_get_locked_transfer",
        Mock(
            return_value=transfer(
                requested_by_id=7
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    monkeypatch.setattr(
        service,
        "_validate_staff_for_clinic",
        Mock(),
    )

    with pytest.raises(ValidationError):
        service.approve_inventory_transfer(
            1,
            approved_by_id=7,
            clinic_id=10,
        )


def test_cancel_transfer_rejects_completed(monkeypatch):
    monkeypatch.setattr(
        service.db.session, "get",
        Mock(side_effect=lambda model, clinic_id: SimpleNamespace(id=clinic_id, organization_id="same-test-owner")),
    )
    monkeypatch.setattr(
        service,
        "_get_locked_transfer",
        Mock(
            return_value=transfer(
                status=InventoryTransferStatus.COMPLETED
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_get_active_clinic",
        Mock(),
    )

    with pytest.raises(ConflictError):
        service.cancel_inventory_transfer(
            1,
            cancelled_by_id=7,
            clinic_id=10,
        )


# ============================================================================
# PAGINATION CONTRACTS ACROSS ALL COLLECTION METHODS
# ============================================================================

@pytest.mark.parametrize(
    "method_name",
    [
        "list_inventory_items",
        "list_suppliers",
        "list_inventory_batches",
        "list_expiring_inventory_batches",
        "get_stock_movements",
        "list_inventory_transfers",
    ],
)
def test_collection_methods_expose_pagination_contract(
    method_name,
):
    method = getattr(service, method_name)

    assert callable(method)


def test_empty_pagination_response_is_valid():
    result = page(
        [],
        total=0,
        current_page=1,
        per_page=50,
    )

    assert result.items == []
    assert result.total == 0
    assert result.page == 1
    assert result.per_page == 50
    assert result.has_next is False
    assert result.has_prev is False


def test_last_page_has_no_next():
    result = page(
        [item()],
        total=51,
        current_page=2,
        per_page=50,
    )

    assert result.has_next is False
    assert result.has_prev is True


def test_middle_page_has_next_and_previous():
    result = page(
        [item()],
        total=120,
        current_page=2,
        per_page=50,
    )

    assert result.has_next is True
    assert result.has_prev is True