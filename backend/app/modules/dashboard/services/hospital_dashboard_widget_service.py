from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.extensions import db
from app.core.enums.clinical_order_enums import ClinicalOrderLifecycleStatus
from app.core.enums.emar_enums import MedicationAdministrationStatus
from app.core.enums.nursing_enums import (
    NursingCarePlanStatus, NursingCareTaskPriority, NursingCareTaskStatus,
    NursingHandoffStatus, NursingNoteStatus, NursingNoteType,
)
from app.modules.clinical_orders.services.unified_order_service import list_unified_clinical_orders
from app.modules.dashboard.services.dashboard_widget_service import count_scalar
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.medication_reconciliation.models.medication_reconciliation_model import MedicationReconciliation
from app.modules.nursing.models.nursing_model import NursingCarePlan, NursingCareTask, NursingHandoff, NursingNote
from app.modules.ward.models.ward_model import Admission, Bed, Ward


def _positive(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _count(model, *filters) -> int:
    return count_scalar(db.select(db.func.count(model.id)).where(*filters))


def build_admission_aggregates(*, clinic_id: int) -> dict[str, int]:
    _positive(clinic_id, "Clinic ID")
    scoped = db.select(Admission.id).join(Bed).join(Ward).where(Ward.clinic_id == clinic_id)
    return {
        "active_admissions": _count(Admission, Admission.id.in_(scoped), Admission.status == "ADMITTED"),
        "total_beds": _count(Bed, Bed.ward_id.in_(db.select(Ward.id).where(Ward.clinic_id == clinic_id))),
        "occupied_beds": _count(Bed, Bed.ward_id.in_(db.select(Ward.id).where(Ward.clinic_id == clinic_id)), Bed.status == "OCCUPIED"),
        "available_beds": _count(Bed, Bed.ward_id.in_(db.select(Ward.id).where(Ward.clinic_id == clinic_id)), Bed.status == "AVAILABLE"),
    }


def build_nursing_aggregates(*, clinic_id: int, now: datetime | None = None) -> dict[str, int]:
    _positive(clinic_id, "Clinic ID")
    now = now or datetime.now(timezone.utc)
    open_states = [NursingCareTaskStatus.PENDING, NursingCareTaskStatus.IN_PROGRESS]
    return {
        "active_care_plans": _count(NursingCarePlan, NursingCarePlan.clinic_id == clinic_id, NursingCarePlan.status == NursingCarePlanStatus.ACTIVE),
        "pending_tasks": _count(NursingCareTask, NursingCareTask.clinic_id == clinic_id, NursingCareTask.status == NursingCareTaskStatus.PENDING),
        "in_progress_tasks": _count(NursingCareTask, NursingCareTask.clinic_id == clinic_id, NursingCareTask.status == NursingCareTaskStatus.IN_PROGRESS),
        "urgent_open_tasks": _count(NursingCareTask, NursingCareTask.clinic_id == clinic_id, NursingCareTask.status.in_(open_states), NursingCareTask.priority == NursingCareTaskPriority.URGENT),
        "overdue_open_tasks": _count(NursingCareTask, NursingCareTask.clinic_id == clinic_id, NursingCareTask.status.in_(open_states), NursingCareTask.due_at.is_not(None), NursingCareTask.due_at < now),
        "unsigned_assessments": _count(NursingNote, NursingNote.clinic_id == clinic_id, NursingNote.note_type == NursingNoteType.ASSESSMENT, NursingNote.status == NursingNoteStatus.DRAFT),
        "ready_handoffs": _count(NursingHandoff, NursingHandoff.clinic_id == clinic_id, NursingHandoff.status == NursingHandoffStatus.READY),
    }


def build_emar_aggregates(*, clinic_id: int) -> dict[str, int]:
    _positive(clinic_id, "Clinic ID")
    def status_count(status):
        return _count(MedicationAdministration, MedicationAdministration.clinic_id == clinic_id, MedicationAdministration.status == status)
    return {status.value: status_count(status) for status in (
        MedicationAdministrationStatus.SCHEDULED, MedicationAdministrationStatus.DUE,
        MedicationAdministrationStatus.ADMINISTERED, MedicationAdministrationStatus.HELD,
        MedicationAdministrationStatus.REFUSED, MedicationAdministrationStatus.MISSED,
    )}


def build_reconciliation_aggregates(*, clinic_id: int) -> dict[str, int]:
    _positive(clinic_id, "Clinic ID")
    base = MedicationReconciliation.clinic_id == clinic_id
    return {
        "draft": _count(MedicationReconciliation, base, MedicationReconciliation.record_status == "draft"),
        "final": _count(MedicationReconciliation, base, MedicationReconciliation.record_status == "final"),
        "amended": _count(MedicationReconciliation, base, MedicationReconciliation.record_status == "amended"),
        "draft_discharge": _count(MedicationReconciliation, base, MedicationReconciliation.event_type == "discharge", MedicationReconciliation.record_status == "draft"),
        "finalized_discharge": _count(MedicationReconciliation, base, MedicationReconciliation.event_type == "discharge", MedicationReconciliation.record_status.in_(["final", "amended"])),
    }


def build_patient_order_aggregates(*, clinic_id: int, patient_id: int) -> dict[str, Any]:
    _positive(clinic_id, "Clinic ID"); _positive(patient_id, "Patient ID")
    orders = list_unified_clinical_orders(patient_id=patient_id, clinic_id=clinic_id)
    by_type: dict[str, int] = {}; by_status: dict[str, int] = {}
    for order in orders:
        typ = order.order_type.value; status = order.status.value
        by_type[typ] = by_type.get(typ, 0) + 1; by_status[status] = by_status.get(status, 0) + 1
    open_states = {ClinicalOrderLifecycleStatus.ACTIVE.value, ClinicalOrderLifecycleStatus.PENDING.value}
    return {"total": len(orders), "open": sum(v for k, v in by_status.items() if k in open_states), "by_type": by_type, "by_status": by_status}


def build_hospital_read_model(*, clinic_id: int, patient_id: int | None = None, now: datetime | None = None) -> dict[str, Any]:
    _positive(clinic_id, "Clinic ID")
    result = {
        "admissions": build_admission_aggregates(clinic_id=clinic_id),
        "nursing": build_nursing_aggregates(clinic_id=clinic_id, now=now),
        "emar": build_emar_aggregates(clinic_id=clinic_id),
        "medication_reconciliation": build_reconciliation_aggregates(clinic_id=clinic_id),
    }
    if patient_id is not None:
        result["clinical_orders"] = build_patient_order_aggregates(clinic_id=clinic_id, patient_id=patient_id)
    return result
