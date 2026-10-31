from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)
from app.core.enums.emar_enums import MedicationAdministrationStatus
from app.core.enums.medication_reconciliation_enums import (
    MedicationReconciliationDecision,
    MedicationReconciliationEvent,
)
from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskStatus,
)
from app.core.enums.prescription_enums import (
    MedicationOrderType,
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus, BedStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.clinical_records.services import clinical_record_service
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.medication_reconciliation.services.medication_reconciliation_service import (
    get_finalized_reconciliation_for_event,
    get_medication_reconciliation_effective_document,
)
from app.modules.nursing.models.nursing_model import (
    NursingCarePlan,
    NursingCareTask,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.discharge_summary_model import DischargeSummary
from app.modules.ward.models.ward_model import Admission, Bed, Ward


_SUMMARY_DOCUMENT_KEYS = {
    "primary_diagnosis",
    "secondary_diagnoses",
    "condition_at_discharge",
    "instructions",
    "follow_up_plan",
    "destination",
}

_PENDING_EMAR_STATUSES = {
    MedicationAdministrationStatus.SCHEDULED,
    MedicationAdministrationStatus.DUE,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _db_now() -> datetime:
    return _utcnow().replace(tzinfo=None)


def _positive_id(value, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )
    return value


def _required_text(value, field_name: str, max_length: int) -> str:
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
            f"{field_name} cannot exceed {max_length} characters"
        )

    return value


def _optional_text(value, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string or null"
        )

    value = value.strip()

    if not value:
        return None

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )

    return value


def _normalize_diagnoses(values) -> list[str]:
    if not isinstance(values, list):
        raise ValidationError(
            "secondary_diagnoses must be a list"
        )

    if len(values) > 100:
        raise ValidationError(
            "secondary_diagnoses cannot exceed 100 items"
        )

    result = []

    for raw in values:
        item = _required_text(
            raw,
            "secondary_diagnosis",
            2000,
        )
        if item not in result:
            result.append(item)

    return result


def _normalize_summary_document(
    *,
    primary_diagnosis,
    secondary_diagnoses,
    condition_at_discharge,
    instructions,
    follow_up_plan,
    destination,
) -> dict:
    return {
        "primary_diagnosis": _required_text(
            primary_diagnosis,
            "primary_diagnosis",
            2000,
        ),
        "secondary_diagnoses": _normalize_diagnoses(
            secondary_diagnoses
        ),
        "condition_at_discharge": _required_text(
            condition_at_discharge,
            "condition_at_discharge",
            5000,
        ),
        "instructions": _required_text(
            instructions,
            "instructions",
            20000,
        ),
        "follow_up_plan": _required_text(
            follow_up_plan,
            "follow_up_plan",
            20000,
        ),
        "destination": _required_text(
            destination,
            "destination",
            255,
        ),
    }


def _active_doctor(
    *,
    staff_id: int,
    actor_user_id: int,
    clinic_id: int,
) -> Staff:
    staff = db.session.execute(
        select(Staff).where(
            Staff.id == _positive_id(
                staff_id,
                "actor_staff_id",
            ),
            Staff.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()

    if staff is None:
        raise NotFoundError("Discharge actor not found")

    if staff.status != StaffStatus.ACTIVE:
        raise ConflictError(
            "Discharge actor must be active staff"
        )

    if staff.user is None or not staff.user.is_active:
        raise ConflictError(
            "Discharge actor user must be active"
        )

    if staff.user_id != _positive_id(
        actor_user_id,
        "actor_user_id",
    ):
        raise ConflictError(
            "Discharge actor does not match "
            "the authenticated user"
        )

    if staff.user.role != Role.DOCTOR:
        raise ConflictError(
            "Clinical discharge requires doctor role"
        )

    return staff


def _active_admission(
    *,
    admission_id: int,
    clinic_id: int,
) -> Admission:
    admission = db.session.execute(
        select(Admission)
        .join(Bed, Admission.bed_id == Bed.id)
        .join(Ward, Bed.ward_id == Ward.id)
        .where(
            Admission.id == _positive_id(
                admission_id,
                "admission_id",
            ),
            Ward.clinic_id == clinic_id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if admission is None:
        raise NotFoundError("Admission not found")

    if (
        admission.status != AdmissionStatus.ADMITTED
        or admission.discharged_at is not None
    ):
        raise ConflictError(
            "Clinical discharge requires an active admission"
        )

    return admission


def _occupied_bed(
    admission: Admission,
    *,
    clinic_id: int,
) -> Bed:
    bed = db.session.execute(
        select(Bed)
        .join(Ward, Bed.ward_id == Ward.id)
        .where(
            Bed.id == admission.bed_id,
            Ward.clinic_id == clinic_id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if bed is None:
        raise NotFoundError("Admission bed not found")

    if bed.status != BedStatus.OCCUPIED:
        raise ConflictError(
            "Admission bed must be occupied before discharge"
        )

    return bed


def _active_inpatient_item_ids(
    *,
    clinic_id: int,
    patient_id: int,
    now: datetime,
) -> set[int]:
    items = list(
        db.session.execute(
            select(PrescriptionItem)
            .join(
                Prescription,
                Prescription.id == PrescriptionItem.prescription_id,
            )
            .where(
                Prescription.clinic_id == clinic_id,
                Prescription.patient_id == patient_id,
                Prescription.status == PrescriptionStatus.ACTIVE,
                PrescriptionItem.order_type
                == MedicationOrderType.INPATIENT.value,
            )
        )
        .scalars()
        .all()
    )

    now_naive = now.astimezone(
        timezone.utc
    ).replace(tzinfo=None)

    active_ids = set()

    for item in items:
        end_at = item.end_at

        if end_at is not None and end_at.tzinfo is not None:
            end_at = (
                end_at.astimezone(timezone.utc)
                .replace(tzinfo=None)
            )

        if end_at is not None and end_at < now_naive:
            continue

        active_ids.add(item.id)

    return active_ids


def _validate_reconciliation_coverage(
    *,
    clinic_id: int,
    patient_id: int,
    reconciliation_document: dict,
    now: datetime,
) -> None:
    required_ids = _active_inpatient_item_ids(
        clinic_id=clinic_id,
        patient_id=patient_id,
        now=now,
    )

    covered_ids = {
        item["source_prescription_item_id"]
        for item in reconciliation_document["items"]
        if item["source_prescription_item_id"] is not None
    }

    if required_ids - covered_ids:
        raise ConflictError(
            "Discharge medication reconciliation "
            "does not resolve every active inpatient "
            "medication order"
        )


def _final_medication_list(
    reconciliation_document: dict,
) -> list[dict]:
    result = []

    for item in reconciliation_document["items"]:
        decision = MedicationReconciliationDecision(
            item["decision"]
        )

        if decision in {
            MedicationReconciliationDecision.STOP,
            MedicationReconciliationDecision.HOLD,
        }:
            continue

        medication_name = item["medication_name"]

        if decision == MedicationReconciliationDecision.REPLACE:
            medication_name = item[
                "replacement_medication_name"
            ]

        result.append({
            "medication_name": medication_name,
            "source_medication_name": item["medication_name"],
            "decision": decision.value,
            "dose_text": item["dose_text"],
            "route_text": item["route_text"],
            "frequency_text": item["frequency_text"],
            "instructions": item["instructions"],
            "rationale": item["rationale"],
            "source_prescription_item_id": item[
                "source_prescription_item_id"
            ],
        })

    return result


def _close_inpatient_nursing(
    *,
    admission: Admission,
    actor: Staff,
    clinic_id: int,
    now_db: datetime,
) -> tuple[int, int]:
    tasks = list(
        db.session.execute(
            select(NursingCareTask)
            .where(
                NursingCareTask.clinic_id == clinic_id,
                NursingCareTask.admission_id == admission.id,
                NursingCareTask.status.in_(
                    (
                        NursingCareTaskStatus.PENDING,
                        NursingCareTaskStatus.IN_PROGRESS,
                    )
                ),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )

    for task in tasks:
        old_status = task.status.value
        task.status = NursingCareTaskStatus.CANCELLED
        task.cancelled_at = now_db
        task.completed_at = None
        task.completed_by_staff_id = None
        task.cancellation_reason = "Admission discharged"
        task.version += 1

        create_audit_log(
            action=AuditAction.STATUS_CHANGE,
            entity_type="NursingCareTask",
            entity_id=task.id,
            clinic_id=clinic_id,
            user_id=actor.user_id,
            description="Closed nursing task at discharge",
            old_value={"status": old_status},
            new_value={
                "status": task.status.value,
                "cancellation_reason": task.cancellation_reason,
            },
        )

    plans = list(
        db.session.execute(
            select(NursingCarePlan)
            .where(
                NursingCarePlan.clinic_id == clinic_id,
                NursingCarePlan.admission_id == admission.id,
                NursingCarePlan.status.in_(
                    (
                        NursingCarePlanStatus.ACTIVE,
                        NursingCarePlanStatus.ON_HOLD,
                    )
                ),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )

    for plan in plans:
        old_status = plan.status.value
        plan.status = NursingCarePlanStatus.CANCELLED
        plan.cancelled_at = now_db
        plan.completed_at = None
        plan.version += 1

        create_audit_log(
            action=AuditAction.STATUS_CHANGE,
            entity_type="NursingCarePlan",
            entity_id=plan.id,
            clinic_id=clinic_id,
            user_id=actor.user_id,
            description="Closed nursing care plan at discharge",
            old_value={"status": old_status},
            new_value={
                "status": plan.status.value,
                "closure_reason": "Admission discharged",
            },
        )

    return len(plans), len(tasks)


def _cancel_pending_emar(
    *,
    admission: Admission,
    actor: Staff,
    clinic_id: int,
    now: datetime,
) -> int:
    rows = list(
        db.session.execute(
            select(MedicationAdministration)
            .where(
                MedicationAdministration.clinic_id == clinic_id,
                MedicationAdministration.admission_id == admission.id,
                MedicationAdministration.status.in_(
                    tuple(_PENDING_EMAR_STATUSES)
                ),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )

    for record in rows:
        old_status = record.status.value

        clinical_record_service.validate_finalization_transition(
            record.record_status
        )

        record.status = MedicationAdministrationStatus.CANCELLED
        record.cancelled_at = now
        record.cancel_reason = "Admission discharged"
        record.record_status = ClinicalRecordStatus.FINAL.value
        record.record_version = 1
        record.finalized_by_staff_id = actor.id
        record.finalized_at = now
        record.latest_amendment_id = None

        create_audit_log(
            action=AuditAction.STATUS_CHANGE,
            entity_type="MedicationAdministration",
            entity_id=record.id,
            clinic_id=clinic_id,
            user_id=actor.user_id,
            description=(
                "Cancelled pending inpatient medication "
                "administration at discharge"
            ),
            old_value={"status": old_status},
            new_value={
                "status": record.status.value,
                "record_status": record.record_status,
                "cancel_reason": record.cancel_reason,
            },
        )

    return len(rows)


def _existing_summary(
    *,
    admission_id: int,
    clinic_id: int,
    lock: bool = False,
) -> DischargeSummary | None:
    stmt = select(DischargeSummary).where(
        DischargeSummary.admission_id == admission_id,
        DischargeSummary.clinic_id == clinic_id,
    )

    if lock:
        stmt = stmt.with_for_update()

    return db.session.execute(
        stmt
    ).scalar_one_or_none()


def complete_clinical_discharge(
    *,
    admission_id: int,
    clinic_id: int,
    actor_staff_id: int,
    actor_user_id: int,
    reason: str | None,
    primary_diagnosis: str,
    secondary_diagnoses: list[str],
    condition_at_discharge: str,
    instructions: str,
    follow_up_plan: str,
    destination: str,
) -> tuple[Admission, DischargeSummary]:
    ensure_clinic_active(clinic_id)

    actor = _active_doctor(
        staff_id=actor_staff_id,
        actor_user_id=actor_user_id,
        clinic_id=clinic_id,
    )

    admission = _active_admission(
        admission_id=admission_id,
        clinic_id=clinic_id,
    )

    bed = _occupied_bed(
        admission,
        clinic_id=clinic_id,
    )

    if _existing_summary(
        admission_id=admission.id,
        clinic_id=clinic_id,
        lock=True,
    ) is not None:
        raise ConflictError(
            "Discharge summary already exists for this admission"
        )

    reconciliation = get_finalized_reconciliation_for_event(
        clinic_id=clinic_id,
        admission_id=admission.id,
        event_type=MedicationReconciliationEvent.DISCHARGE,
    )

    reconciliation_document = (
        get_medication_reconciliation_effective_document(
            reconciliation
        )
    )

    now = _utcnow()
    now_db = now.replace(tzinfo=None)

    _validate_reconciliation_coverage(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        reconciliation_document=reconciliation_document,
        now=now,
    )

    document = _normalize_summary_document(
        primary_diagnosis=primary_diagnosis,
        secondary_diagnoses=secondary_diagnoses,
        condition_at_discharge=condition_at_discharge,
        instructions=instructions,
        follow_up_plan=follow_up_plan,
        destination=destination,
    )

    summary = DischargeSummary(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        medication_reconciliation_id=reconciliation.id,
        discharged_by_staff_id=actor.id,
        primary_diagnosis=document["primary_diagnosis"],
        secondary_diagnoses=document["secondary_diagnoses"],
        condition_at_discharge=document["condition_at_discharge"],
        instructions=document["instructions"],
        follow_up_plan=document["follow_up_plan"],
        final_medication_list=_final_medication_list(
            reconciliation_document
        ),
        destination=document["destination"],
        discharged_at=now,
        record_status=ClinicalRecordStatus.FINAL.value,
        record_version=1,
        finalized_by_staff_id=actor.id,
        finalized_at=now,
        latest_amendment_id=None,
    )

    db.session.add(summary)
    db.session.flush()

    closed_plans, closed_tasks = _close_inpatient_nursing(
        admission=admission,
        actor=actor,
        clinic_id=clinic_id,
        now_db=now_db,
    )

    cancelled_emar = _cancel_pending_emar(
        admission=admission,
        actor=actor,
        clinic_id=clinic_id,
        now=now,
    )

    old_status = admission.status.value

    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = now_db

    normalized_reason = _optional_text(
        reason,
        "reason",
        255,
    )

    if normalized_reason is not None:
        admission.reason = normalized_reason

    bed.status = BedStatus.AVAILABLE

    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="DischargeSummary",
        entity_id=summary.id,
        clinic_id=clinic_id,
        user_id=actor.user_id,
        description="Finalized clinical discharge summary",
        new_value={
            "admission_id": admission.id,
            "medication_reconciliation_id": reconciliation.id,
            "record_status": summary.record_status,
            "record_version": summary.record_version,
            "destination": summary.destination,
        },
    )

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="admission",
        entity_id=admission.id,
        clinic_id=clinic_id,
        user_id=actor.user_id,
        description="Completed clinical discharge",
        old_value={"status": old_status},
        new_value={
            "status": admission.status.value,
            "discharged_at": admission.discharged_at.isoformat(),
            "discharge_summary_id": summary.id,
            "closed_care_plans": closed_plans,
            "closed_care_tasks": closed_tasks,
            "cancelled_pending_emar": cancelled_emar,
        },
    )

    return admission, summary


def _get_summary(
    summary_id: int,
    *,
    clinic_id: int,
    lock: bool = False,
) -> DischargeSummary:
    stmt = select(DischargeSummary).where(
        DischargeSummary.id == _positive_id(
            summary_id,
            "summary_id",
        ),
        DischargeSummary.clinic_id == clinic_id,
    )

    if lock:
        stmt = stmt.with_for_update()

    summary = db.session.execute(
        stmt
    ).scalar_one_or_none()

    if summary is None:
        raise NotFoundError("Discharge summary not found")

    return summary


def get_discharge_summary_for_admission(
    admission_id: int,
    *,
    clinic_id: int,
) -> DischargeSummary:
    summary = _existing_summary(
        admission_id=_positive_id(
            admission_id,
            "admission_id",
        ),
        clinic_id=clinic_id,
    )

    if summary is None:
        raise NotFoundError("Discharge summary not found")

    return summary


def _original_document(summary: DischargeSummary) -> dict:
    return {
        "primary_diagnosis": summary.primary_diagnosis,
        "secondary_diagnoses": list(
            summary.secondary_diagnoses or []
        ),
        "condition_at_discharge": summary.condition_at_discharge,
        "instructions": summary.instructions,
        "follow_up_plan": summary.follow_up_plan,
        "final_medication_list": list(
            summary.final_medication_list or []
        ),
        "destination": summary.destination,
    }


def get_discharge_summary_effective_document(
    summary: DischargeSummary,
) -> dict:
    try:
        status = ClinicalRecordStatus(
            summary.record_status
        )
    except (TypeError, ValueError) as exc:
        raise ConflictError(
            "Discharge summary has invalid clinical record status"
        ) from exc

    if status != ClinicalRecordStatus.AMENDED:
        return _original_document(summary)

    amendment = summary.latest_amendment

    if amendment is None:
        raise ConflictError(
            "Amended discharge summary has no latest amendment"
        )

    if (
        amendment.source_record_type
        != ClinicalRecordType.DISCHARGE_SUMMARY.value
        or amendment.source_record_id != summary.id
        or amendment.clinic_id != summary.clinic_id
        or amendment.patient_id != summary.patient_id
        or amendment.new_version != summary.record_version
    ):
        raise ConflictError(
            "Discharge summary amendment linkage is invalid"
        )

    return dict(amendment.new_content)


@transactional
def amend_discharge_summary(
    summary_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    actor_user_id: int,
    expected_version: int,
    reason: str,
    document: dict,
):
    summary = _get_summary(
        summary_id,
        clinic_id=clinic_id,
        lock=True,
    )

    actor = _active_doctor(
        staff_id=actor_staff_id,
        actor_user_id=actor_user_id,
        clinic_id=clinic_id,
    )

    if summary.discharged_by_staff_id != actor.id:
        raise ConflictError(
            "Only the discharging doctor can amend "
            "the discharge summary"
        )

    expected_version = _positive_id(
        expected_version,
        "expected_version",
    )

    if summary.record_version != expected_version:
        raise ConflictError(
            "Discharge summary record version mismatch"
        )

    current_status = ClinicalRecordStatus(
        summary.record_status
    )

    clinical_record_service.validate_amendment_transition(
        current_status
    )

    if (
        not isinstance(document, dict)
        or set(document) != _SUMMARY_DOCUMENT_KEYS
    ):
        raise ValidationError(
            "Discharge summary amendment must contain "
            "the complete clinical document"
        )

    normalized = _normalize_summary_document(
        primary_diagnosis=document["primary_diagnosis"],
        secondary_diagnoses=document["secondary_diagnoses"],
        condition_at_discharge=document["condition_at_discharge"],
        instructions=document["instructions"],
        follow_up_plan=document["follow_up_plan"],
        destination=document["destination"],
    )

    reconciliation = get_finalized_reconciliation_for_event(
        clinic_id=summary.clinic_id,
        admission_id=summary.admission_id,
        event_type=MedicationReconciliationEvent.DISCHARGE,
    )

    reconciliation_document = (
        get_medication_reconciliation_effective_document(
            reconciliation
        )
    )

    normalized["final_medication_list"] = (
        _final_medication_list(
            reconciliation_document
        )
    )

    effective_before = get_discharge_summary_effective_document(
        summary
    )

    if normalized == effective_before:
        raise ValidationError(
            "Discharge summary amendment must change "
            "the effective clinical document"
        )

    amendment = clinical_record_service.append_clinical_record_amendment(
        clinic_id=summary.clinic_id,
        patient_id=summary.patient_id,
        source_record_type=ClinicalRecordType.DISCHARGE_SUMMARY,
        source_record_id=summary.id,
        current_status=current_status,
        previous_version=summary.record_version,
        amended_by_staff_id=actor.id,
        reason=reason,
        new_content=normalized,
    )

    old_status = summary.record_status
    old_version = summary.record_version

    summary.record_status = ClinicalRecordStatus.AMENDED.value
    summary.record_version = amendment.new_version
    summary.latest_amendment_id = amendment.id
    summary.latest_amendment = amendment

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="DischargeSummary",
        entity_id=summary.id,
        clinic_id=summary.clinic_id,
        user_id=actor.user_id,
        description="Discharge summary amended",
        old_value={
            "record_status": old_status,
            "record_version": old_version,
        },
        new_value={
            "record_status": summary.record_status,
            "record_version": summary.record_version,
            "latest_amendment_id": amendment.id,
            "reason": amendment.reason,
            "new_content_sha256": amendment.new_content_sha256,
        },
    )

    return summary, amendment


def get_discharge_summary_amendments(
    summary_id: int,
    *,
    clinic_id: int,
):
    summary = _get_summary(
        summary_id,
        clinic_id=clinic_id,
    )

    return clinical_record_service.get_clinical_record_amendments(
        clinic_id=summary.clinic_id,
        source_record_type=ClinicalRecordType.DISCHARGE_SUMMARY,
        source_record_id=summary.id,
    )
