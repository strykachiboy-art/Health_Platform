from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.reports_enums import ReportType
from app.modules.inventory.models.inventory_model import InventoryItem
from app.modules.reports.services import reports_service as service


@pytest.fixture(autouse=True)
def app_context(app):
    with app.app_context():
        yield


def _item(**kwargs):
    data = dict(
        id=11,
        clinic_id=41,
        name="Sterile gloves",
        category="medical_supply",
        sku="GLOVES-001",
        barcode="BAR-001",
        unit="box",
        quantity_on_hand=17,
        reorder_level=5,
        is_active=True,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


def _fake_execute(monkeypatch, items):
    statements = []
    def execute(stmt):
        statements.append(stmt)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items))
    monkeypatch.setattr(service.db.session, "execute", execute)
    return statements


def test_inventory_gatherer_uses_clinic_scoped_ordered_query(monkeypatch):
    statements = _fake_execute(monkeypatch, [_item()])
    rows = service._gather_inventory(41, {})
    assert rows == [{
        "id": 11, "clinic_id": 41, "name": "Sterile gloves",
        "category": "medical_supply", "sku": "GLOVES-001",
        "barcode": "BAR-001", "unit": "box",
        "quantity_on_hand": 17, "reorder_level": 5,
        "is_active": True, "created_at": "2026-05-01T00:00:00+00:00",
    }]
    sql = str(statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "inventory_items.clinic_id = 41" in sql
    assert "ORDER BY inventory_items.id ASC" in sql


def test_inventory_active_filter_and_date_filters(monkeypatch):
    statements = _fake_execute(monkeypatch, [])
    rows = service._gather_inventory(82, {
        "active_only": True,
        "date_from": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2026, 2, 1, tzinfo=timezone.utc),
    })
    assert rows == []
    sql = str(statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "inventory_items.clinic_id = 82" in sql
    assert "inventory_items.is_active IS true" in sql
    assert "inventory_items.created_at" in sql


def test_inventory_preserves_zero_stock_and_reorder_value(monkeypatch):
    _fake_execute(monkeypatch, [_item(quantity_on_hand=0, reorder_level=9)])
    rows = service._gather_inventory(41, {})
    assert rows[0]["quantity_on_hand"] == 0
    assert rows[0]["reorder_level"] == 9


def test_inventory_is_registered_for_all_existing_formats():
    assert service._GATHERERS[ReportType.INVENTORY] is service._gather_inventory
    for supported in (service.SUPPORTED_CSV_TYPES,
                      service.SUPPORTED_XLSX_TYPES,
                      service.SUPPORTED_PDF_TYPES):
        assert ReportType.INVENTORY in supported
    assert ReportType.INVENTORY not in service.UNSUPPORTED_TYPES


def test_inventory_csv_formula_injection_stays_escaped(monkeypatch):
    _fake_execute(monkeypatch, [_item(name="=EVIL()")])
    rows = service._gather_inventory(41, {})
    result = service._write_csv(rows).decode("utf-8")
    assert "'=EVIL()" in result
    assert "\n=EVIL()" not in result
