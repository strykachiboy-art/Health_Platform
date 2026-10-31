from types import SimpleNamespace
import pytest
from app.core.enums.clinical_order_enums import ClinicalOrderLifecycleStatus, ClinicalOrderType
from app.modules.dashboard.services import hospital_dashboard_widget_service as service


def test_positive_id_validation():
    for value in (True, 0, -1):
        with pytest.raises(ValueError):
            service._positive(value, "Clinic ID")


def test_read_model_composes_without_patient_orders(monkeypatch):
    monkeypatch.setattr(service, "build_admission_aggregates", lambda **_: {"active_admissions": 3})
    monkeypatch.setattr(service, "build_nursing_aggregates", lambda **_: {"pending_tasks": 4})
    monkeypatch.setattr(service, "build_emar_aggregates", lambda **_: {"due": 5})
    monkeypatch.setattr(service, "build_reconciliation_aggregates", lambda **_: {"draft_discharge": 2})
    assert service.build_hospital_read_model(clinic_id=7) == {
        "admissions": {"active_admissions": 3}, "nursing": {"pending_tasks": 4},
        "emar": {"due": 5}, "medication_reconciliation": {"draft_discharge": 2},
    }


def test_patient_orders_reuse_existing_unified_cpoe(monkeypatch):
    orders = [
        SimpleNamespace(order_type=ClinicalOrderType.MEDICATION, status=ClinicalOrderLifecycleStatus.ACTIVE),
        SimpleNamespace(order_type=ClinicalOrderType.LABORATORY, status=ClinicalOrderLifecycleStatus.PENDING),
        SimpleNamespace(order_type=ClinicalOrderType.LABORATORY, status=ClinicalOrderLifecycleStatus.COMPLETED),
    ]
    calls = []
    monkeypatch.setattr(service, "list_unified_clinical_orders", lambda **kw: calls.append(kw) or orders)
    result = service.build_patient_order_aggregates(clinic_id=7, patient_id=11)
    assert calls == [{"patient_id": 11, "clinic_id": 7}]
    assert result["total"] == 3
    assert result["open"] == 2
    assert result["by_type"] == {"medication": 1, "laboratory": 2}


def test_assessment_uses_nursing_note_contract():
    names = set(service.build_nursing_aggregates.__code__.co_names)
    assert "NursingNote" in names
    assert "NursingNoteType" in names
    assert "NursingAssessment" not in names


def test_read_model_adds_orders_only_for_patient_scope(monkeypatch):
    for name in ("build_admission_aggregates", "build_nursing_aggregates", "build_emar_aggregates", "build_reconciliation_aggregates"):
        monkeypatch.setattr(service, name, lambda **_: {})
    calls = []
    monkeypatch.setattr(service, "build_patient_order_aggregates", lambda **kw: calls.append(kw) or {"total": 1})
    assert "clinical_orders" not in service.build_hospital_read_model(clinic_id=7)
    assert service.build_hospital_read_model(clinic_id=7, patient_id=11)["clinical_orders"] == {"total": 1}
    assert calls == [{"clinic_id": 7, "patient_id": 11}]
