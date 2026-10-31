from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.enums.emar_enums import MedicationAdministrationStatus
from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskPriority,
    NursingCareTaskStatus,
    NursingHandoffStatus,
    NursingNoteStatus,
    NursingNoteType,
)
from app.core.enums.prescription_enums import (
    MedicationOrderPriority,
    MedicationOrderType,
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus, BedStatus
from app.modules.dashboard.services.hospital_dashboard_widget_service import (
    build_emar_aggregates,
    build_hospital_read_model,
    build_patient_order_aggregates,
)
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.medication_reconciliation.models.medication_reconciliation_model import (
    MedicationReconciliation,
)
from app.modules.nursing.models.nursing_model import (
    NursingCarePlan,
    NursingCareTask,
    NursingHandoff,
    NursingNote,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.ward.models.ward_model import Admission


def _create_admission(
    *,
    clinic,
    patient,
    staff,
    make_ward,
    make_bed,
    db_session,
):
    ward = make_ward(clinic)

    occupied_bed = make_bed(
        ward,
        status=BedStatus.OCCUPIED,
    )
    available_bed = make_bed(
        ward,
        status=BedStatus.AVAILABLE,
    )

    admission = Admission(
        patient_id=patient.id,
        bed_id=occupied_bed.id,
        admitted_by_id=staff.id,
        status=AdmissionStatus.ADMITTED,
    )

    db_session.add(admission)
    db_session.flush()

    return admission, occupied_bed, available_bed


def _create_inpatient_medication_order(
    *,
    clinic,
    patient,
    staff,
    drug,
    db_session,
):
    prescription = Prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        status=PrescriptionStatus.ACTIVE,
    )

    db_session.add(prescription)
    db_session.flush()

    item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dose_value=500,
        dose_unit="mg",
        route_code="oral",
        order_priority=MedicationOrderPriority.ROUTINE,
        order_type=MedicationOrderType.INPATIENT,
        ordered_at=datetime.now(timezone.utc),
    )

    db_session.add(item)
    db_session.flush()

    return prescription, item


def test_hospital_read_model_tracks_authoritative_ward_nursing_and_recon_state(
    clinic,
    make_staff,
    make_patient,
    make_ward,
    make_bed,
    db_session,
):
    now = datetime(
        2026,
        10,
        31,
        9,
        0,
        0,
    )

    outgoing = make_staff(
        clinic,
        role=Role.NURSE,
    )
    incoming = make_staff(
        clinic,
        role=Role.NURSE,
    )

    patient = make_patient(clinic)

    (
        admission,
        _occupied_bed,
        available_bed,
    ) = _create_admission(
        clinic=clinic,
        patient=patient,
        staff=outgoing,
        make_ward=make_ward,
        make_bed=make_bed,
        db_session=db_session,
    )

    care_plan = NursingCarePlan(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        created_by_staff_id=outgoing.id,
        title="D7 care plan",
        problem="Mobility limitation",
        goal="Safe mobility",
        status=NursingCarePlanStatus.ACTIVE,
    )
    db_session.add(care_plan)
    db_session.flush()

    urgent_task = NursingCareTask(
        care_plan_id=care_plan.id,
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        created_by_staff_id=outgoing.id,
        assigned_to_staff_id=outgoing.id,
        description="Urgent repositioning",
        priority=NursingCareTaskPriority.URGENT,
        status=NursingCareTaskStatus.PENDING,
        due_at=now - timedelta(minutes=30),
    )

    in_progress_task = NursingCareTask(
        care_plan_id=care_plan.id,
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        created_by_staff_id=outgoing.id,
        assigned_to_staff_id=outgoing.id,
        description="Routine observation",
        priority=NursingCareTaskPriority.ROUTINE,
        status=NursingCareTaskStatus.IN_PROGRESS,
        started_at=now - timedelta(minutes=15),
        due_at=now + timedelta(hours=1),
    )

    assessment = NursingNote(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        author_staff_id=outgoing.id,
        note_type=NursingNoteType.ASSESSMENT,
        status=NursingNoteStatus.DRAFT,
        note_text="D7 assessment",
        observed_at=now,
    )

    handoff = NursingHandoff(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        outgoing_staff_id=outgoing.id,
        incoming_staff_id=incoming.id,
        status=NursingHandoffStatus.READY,
        shift_start_at=now - timedelta(hours=8),
        shift_end_at=now,
        situation="Stable",
        background="D7",
        assessment="D7",
        recommendation="Continue plan",
        ready_at=now,
    )

    reconciliation = MedicationReconciliation(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        event_type="discharge",
        event_identity="d7-authoritative-discharge",
        created_by_staff_id=outgoing.id,
        record_status="draft",
    )

    db_session.add_all(
        [
            urgent_task,
            in_progress_task,
            assessment,
            handoff,
            reconciliation,
        ]
    )
    db_session.flush()

    before = build_hospital_read_model(
        clinic_id=clinic.id,
        now=now,
    )

    assert before["admissions"] == {
        "active_admissions": 1,
        "total_beds": 2,
        "occupied_beds": 1,
        "available_beds": 1,
    }

    assert before["nursing"] == {
        "active_care_plans": 1,
        "pending_tasks": 1,
        "in_progress_tasks": 1,
        "urgent_open_tasks": 1,
        "overdue_open_tasks": 1,
        "unsigned_assessments": 1,
        "ready_handoffs": 1,
    }

    assert before["medication_reconciliation"] == {
        "draft": 1,
        "final": 0,
        "amended": 0,
        "draft_discharge": 1,
        "finalized_discharge": 0,
    }

    # Change only authoritative domain rows.
    care_plan.status = NursingCarePlanStatus.ON_HOLD

    urgent_task.status = NursingCareTaskStatus.IN_PROGRESS
    urgent_task.started_at = now
    urgent_task.due_at = now + timedelta(hours=2)

    handoff.status = NursingHandoffStatus.DRAFT
    handoff.ready_at = None

    reconciliation.record_status = "final"
    reconciliation.finalized_by_staff_id = outgoing.id
    reconciliation.finalized_at = now

    available_bed.status = BedStatus.MAINTENANCE

    db_session.flush()

    after = build_hospital_read_model(
        clinic_id=clinic.id,
        now=now,
    )

    assert after["admissions"] == {
        "active_admissions": 1,
        "total_beds": 2,
        "occupied_beds": 1,
        "available_beds": 0,
    }

    assert after["nursing"] == {
        "active_care_plans": 0,
        "pending_tasks": 0,
        "in_progress_tasks": 2,
        "urgent_open_tasks": 1,
        "overdue_open_tasks": 0,
        "unsigned_assessments": 1,
        "ready_handoffs": 0,
    }

    assert after["medication_reconciliation"] == {
        "draft": 0,
        "final": 1,
        "amended": 0,
        "draft_discharge": 0,
        "finalized_discharge": 1,
    }


def test_emar_aggregate_tracks_authoritative_status_changes(
    clinic,
    make_staff,
    make_patient,
    make_drug,
    make_ward,
    make_bed,
    db_session,
):
    nurse = make_staff(
        clinic,
        role=Role.NURSE,
    )
    patient = make_patient(clinic)
    drug = make_drug(
        clinic,
        is_active=True,
    )

    prescription, item = _create_inpatient_medication_order(
        clinic=clinic,
        patient=patient,
        staff=nurse,
        drug=drug,
        db_session=db_session,
    )

    admission, _occupied, _available = _create_admission(
        clinic=clinic,
        patient=patient,
        staff=nurse,
        make_ward=make_ward,
        make_bed=make_bed,
        db_session=db_session,
    )

    scheduled_at = datetime(
        2026,
        10,
        31,
        10,
        0,
        tzinfo=timezone.utc,
    )

    administration = MedicationAdministration(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        prescription_id=prescription.id,
        prescription_item_id=item.id,
        scheduled_at=scheduled_at,
        source_order_version=1,
        status=MedicationAdministrationStatus.SCHEDULED,
    )

    db_session.add(administration)
    db_session.flush()

    scheduled = build_emar_aggregates(
        clinic_id=clinic.id,
    )

    assert scheduled == {
        "scheduled": 1,
        "due": 0,
        "administered": 0,
        "held": 0,
        "refused": 0,
        "missed": 0,
    }

    administration.status = MedicationAdministrationStatus.DUE
    db_session.flush()

    due = build_emar_aggregates(
        clinic_id=clinic.id,
    )

    assert due["scheduled"] == 0
    assert due["due"] == 1
    assert due["missed"] == 0

    administration.status = MedicationAdministrationStatus.MISSED
    administration.missed_at = datetime.now(timezone.utc)
    administration.missed_reason = "D7 authoritative state transition"
    db_session.flush()

    missed = build_emar_aggregates(
        clinic_id=clinic.id,
    )

    assert missed["scheduled"] == 0
    assert missed["due"] == 0
    assert missed["missed"] == 1


def test_patient_order_aggregate_tracks_unified_cpoe_source_state(
    clinic,
    make_staff,
    make_patient,
    make_drug,
    db_session,
):
    doctor = make_staff(
        clinic,
        role=Role.DOCTOR,
    )
    patient = make_patient(clinic)
    drug = make_drug(
        clinic,
        is_active=True,
    )

    prescription, _item = _create_inpatient_medication_order(
        clinic=clinic,
        patient=patient,
        staff=doctor,
        drug=drug,
        db_session=db_session,
    )

    active = build_patient_order_aggregates(
        clinic_id=clinic.id,
        patient_id=patient.id,
    )

    assert active["total"] == 1
    assert active["open"] == 1
    assert active["by_type"] == {
        "medication": 1,
    }
    assert active["by_status"] == {
        "active": 1,
    }

    prescription.status = PrescriptionStatus.COMPLETED
    db_session.flush()

    completed = build_patient_order_aggregates(
        clinic_id=clinic.id,
        patient_id=patient.id,
    )

    assert completed["total"] == 1
    assert completed["open"] == 0
    assert completed["by_type"] == {
        "medication": 1,
    }
    assert completed["by_status"] == {
        "completed": 1,
    }
