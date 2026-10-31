from __future__ import annotations

from datetime import datetime, timezone

import pytest

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
from app.core.enums.prescription_enums import MedicationOrderType
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus, BedStatus
from app.core.exceptions import ConflictError, NotFoundError
from app.extensions import db
from app.modules.clinical_records.services import clinical_record_service
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.emar.services import emar_service
from app.modules.medication_reconciliation.services import (
    medication_reconciliation_service as med_recon_service,
)
from app.modules.nursing.models.nursing_model import (
    NursingCarePlan,
    NursingCareTask,
)
from app.modules.ward.services import discharge_service, ward_service
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _setup_admission,
)


@pytest.fixture()
def ctx(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    audits = []

    def capture(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(
        discharge_service,
        "create_audit_log",
        capture,
    )
    monkeypatch.setattr(
        med_recon_service,
        "create_audit_log",
        capture,
    )
    monkeypatch.setattr(
        clinical_record_service,
        "create_audit_log",
        capture,
    )

    (
        patient,
        nurse,
        doctor,
        ward,
        bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    db.session.commit()

    return {
        "patient": patient,
        "nurse": nurse,
        "doctor": doctor,
        "ward": ward,
        "bed": bed,
        "admission": admission,
        "audits": audits,
    }


def _item(
    *,
    source_id=None,
    medication_name="Metformin",
    decision=MedicationReconciliationDecision.CONTINUE,
):
    return {
        "source_prescription_item_id": source_id,
        "medication_name": medication_name,
        "decision": decision,
        "replacement_medication_name": None,
        "dose_text": "500 mg",
        "route_text": "oral",
        "frequency_text": "twice daily",
        "instructions": "Take with food",
        "rationale": "Discharge review",
    }


def _reconcile(
    ctx,
    clinic,
    *,
    items=None,
    no_medications_reported=None,
):
    items = list(items or [])

    if no_medications_reported is None:
        no_medications_reported = not items

    record = med_recon_service.create_medication_reconciliation(
        clinic_id=clinic.id,
        admission_id=ctx["admission"].id,
        event_type=MedicationReconciliationEvent.DISCHARGE,
        actor_staff_id=ctx["doctor"].id,
        no_medications_reported=no_medications_reported,
        notes="Discharge medication review",
        items=items,
    )

    return med_recon_service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=1,
    )


def _payload():
    return {
        "reason": "Clinical discharge complete",
        "primary_diagnosis": "Resolved acute illness",
        "secondary_diagnoses": ["Hypertension"],
        "condition_at_discharge": "Stable and ambulatory",
        "instructions": "Return for worsening symptoms",
        "follow_up_plan": "Clinic follow-up in seven days",
        "destination": "Home",
    }


def _discharge(ctx, clinic, **overrides):
    payload = _payload()
    payload.update(overrides)

    return ward_service.discharge_patient(
        admission_id=ctx["admission"].id,
        clinic_id=clinic.id,
        actor_user_id=ctx["doctor"].user_id,
        actor_staff_id=ctx["doctor"].id,
        **payload,
    )


def test_discharge_requires_finalized_reconciliation(ctx, clinic):
    with pytest.raises(
        NotFoundError,
        match="Medication reconciliation not found",
    ):
        _discharge(ctx, clinic)

    assert ctx["admission"].status == AdmissionStatus.ADMITTED
    assert ctx["bed"].status == BedStatus.OCCUPIED


def test_draft_reconciliation_blocks_discharge(ctx, clinic):
    med_recon_service.create_medication_reconciliation(
        clinic_id=clinic.id,
        admission_id=ctx["admission"].id,
        event_type=MedicationReconciliationEvent.DISCHARGE,
        actor_staff_id=ctx["doctor"].id,
        no_medications_reported=True,
        items=[],
    )

    with pytest.raises(
        ConflictError,
        match="not finalized",
    ):
        _discharge(ctx, clinic)


def test_nurse_cannot_discharge(ctx, clinic):
    _reconcile(ctx, clinic)

    with pytest.raises(
        ConflictError,
        match="doctor role",
    ):
        ward_service.discharge_patient(
            admission_id=ctx["admission"].id,
            clinic_id=clinic.id,
            actor_user_id=ctx["nurse"].user_id,
            actor_staff_id=ctx["nurse"].id,
            **_payload(),
        )


def test_success_discharge_creates_final_summary_and_releases_bed(
    ctx,
    clinic,
):
    reconciliation = _reconcile(ctx, clinic)

    admission = _discharge(ctx, clinic)

    assert admission.status == AdmissionStatus.DISCHARGED
    assert admission.discharged_at is not None
    assert ctx["bed"].status == BedStatus.AVAILABLE

    summary = discharge_service.get_discharge_summary_for_admission(
        admission.id,
        clinic_id=clinic.id,
    )

    assert summary.medication_reconciliation_id == reconciliation.id
    assert summary.record_status == ClinicalRecordStatus.FINAL.value
    assert summary.record_version == 1
    assert summary.finalized_by_staff_id == ctx["doctor"].id
    assert summary.primary_diagnosis == "Resolved acute illness"
    assert summary.destination == "Home"


def test_active_inpatient_order_must_be_covered(
    ctx,
    clinic,
    make_drug,
    make_prescription,
    make_prescription_item,
):
    drug = make_drug(clinic)
    prescription = make_prescription(
        clinic,
        ctx["patient"],
        ctx["doctor"],
    )
    item = make_prescription_item(
        prescription,
        drug,
        order_type=MedicationOrderType.INPATIENT.value,
    )

    _reconcile(
        ctx,
        clinic,
        items=[],
        no_medications_reported=True,
    )

    with pytest.raises(
        ConflictError,
        match="every active inpatient",
    ):
        _discharge(ctx, clinic)

    assert item.id is not None


def test_reconciled_order_populates_final_medication_list(
    ctx,
    clinic,
    make_drug,
    make_prescription,
    make_prescription_item,
):
    drug = make_drug(
        clinic,
        name="Metformin",
    )
    prescription = make_prescription(
        clinic,
        ctx["patient"],
        ctx["doctor"],
    )
    item = make_prescription_item(
        prescription,
        drug,
        order_type=MedicationOrderType.INPATIENT.value,
    )

    _reconcile(
        ctx,
        clinic,
        items=[
            _item(
                source_id=item.id,
                medication_name="Metformin",
            ),
        ],
        no_medications_reported=False,
    )

    _discharge(ctx, clinic)

    summary = discharge_service.get_discharge_summary_for_admission(
        ctx["admission"].id,
        clinic_id=clinic.id,
    )

    assert len(summary.final_medication_list) == 1
    assert (
        summary.final_medication_list[0]["medication_name"]
        == "Metformin"
    )


def test_discharge_closes_nursing_and_pending_emar(
    ctx,
    clinic,
    make_drug,
    make_prescription,
    make_prescription_item,
):
    drug = make_drug(clinic)
    prescription = make_prescription(
        clinic,
        ctx["patient"],
        ctx["doctor"],
    )
    item = make_prescription_item(
        prescription,
        drug,
        order_type=MedicationOrderType.INPATIENT.value,
    )

    _reconcile(
        ctx,
        clinic,
        items=[_item(source_id=item.id)],
        no_medications_reported=False,
    )

    plan = NursingCarePlan(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        created_by_staff_id=ctx["nurse"].id,
        title="Inpatient care",
        problem="Acute care need",
        goal="Maintain stability",
        status=NursingCarePlanStatus.ACTIVE,
        version=1,
    )
    db.session.add(plan)
    db.session.flush()

    task = NursingCareTask(
        care_plan_id=plan.id,
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        created_by_staff_id=ctx["nurse"].id,
        description="Monitor patient",
        status=NursingCareTaskStatus.PENDING,
        version=1,
    )
    db.session.add(task)

    emar = MedicationAdministration(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        prescription_id=prescription.id,
        prescription_item_id=item.id,
        scheduled_at=datetime.now(timezone.utc),
        status=MedicationAdministrationStatus.SCHEDULED,
        record_status=ClinicalRecordStatus.DRAFT.value,
        record_version=1,
    )
    db.session.add(emar)
    db.session.flush()

    _discharge(ctx, clinic)

    assert task.status == NursingCareTaskStatus.CANCELLED
    assert task.cancellation_reason == "Admission discharged"
    assert plan.status == NursingCarePlanStatus.CANCELLED

    assert emar.status == MedicationAdministrationStatus.CANCELLED
    assert emar.record_status == ClinicalRecordStatus.FINAL.value
    assert emar.finalized_by_staff_id == ctx["doctor"].id


def test_post_discharge_emar_is_blocked(
    ctx,
    clinic,
    make_drug,
    make_prescription,
    make_prescription_item,
):
    drug = make_drug(clinic)
    prescription = make_prescription(
        clinic,
        ctx["patient"],
        ctx["doctor"],
    )
    item = make_prescription_item(
        prescription,
        drug,
        order_type=MedicationOrderType.INPATIENT.value,
        dose_value=1,
        dose_unit="tablet",
        route_code="oral",
    )

    _reconcile(
        ctx,
        clinic,
        items=[_item(source_id=item.id)],
        no_medications_reported=False,
    )

    _discharge(ctx, clinic)

    with pytest.raises(
        ConflictError,
        match="active admission",
    ):
        emar_service.schedule_medication_administration(
            clinic_id=clinic.id,
            patient_id=ctx["patient"].id,
            admission_id=ctx["admission"].id,
            prescription_item_id=item.id,
            scheduled_at=datetime.now(timezone.utc),
            actor_staff_id=ctx["doctor"].id,
        )


def test_summary_amendment_is_append_only(ctx, clinic):
    _reconcile(ctx, clinic)
    _discharge(ctx, clinic)

    summary = discharge_service.get_discharge_summary_for_admission(
        ctx["admission"].id,
        clinic_id=clinic.id,
    )
    original = summary.instructions

    document = {
        "primary_diagnosis": summary.primary_diagnosis,
        "secondary_diagnoses": list(summary.secondary_diagnoses),
        "condition_at_discharge": summary.condition_at_discharge,
        "instructions": "Corrected instructions",
        "follow_up_plan": summary.follow_up_plan,
        "destination": summary.destination,
    }

    summary, amendment = discharge_service.amend_discharge_summary(
        summary.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        actor_user_id=ctx["doctor"].user_id,
        expected_version=1,
        reason="Correct instructions",
        document=document,
    )

    assert summary.instructions == original
    assert summary.record_status == ClinicalRecordStatus.AMENDED.value
    assert summary.record_version == 2
    assert (
        amendment.source_record_type
        == ClinicalRecordType.DISCHARGE_SUMMARY.value
    )

    effective = discharge_service.get_discharge_summary_effective_document(
        summary
    )
    assert effective["instructions"] == "Corrected instructions"


def test_only_discharging_doctor_can_amend(
    ctx,
    clinic,
    make_staff,
):
    _reconcile(ctx, clinic)
    _discharge(ctx, clinic)

    summary = discharge_service.get_discharge_summary_for_admission(
        ctx["admission"].id,
        clinic_id=clinic.id,
    )

    other = make_staff(
        clinic,
        role=Role.DOCTOR,
    )

    document = {
        "primary_diagnosis": summary.primary_diagnosis,
        "secondary_diagnoses": list(summary.secondary_diagnoses),
        "condition_at_discharge": summary.condition_at_discharge,
        "instructions": "Changed",
        "follow_up_plan": summary.follow_up_plan,
        "destination": summary.destination,
    }

    with pytest.raises(
        ConflictError,
        match="discharging doctor",
    ):
        discharge_service.amend_discharge_summary(
            summary.id,
            clinic_id=clinic.id,
            actor_staff_id=other.id,
            actor_user_id=other.user_id,
            expected_version=1,
            reason="Unauthorized correction",
            document=document,
        )


def test_discharge_route_is_doctor_only(
    ctx,
    clinic,
    client,
    auth_headers_for,
):
    response = client.post(
        (
            "/api/v1/wards/admissions/"
            f"{ctx['admission'].id}/discharge"
        ),
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
        json=_payload(),
    )

    assert response.status_code == 403


def test_discharge_route_returns_summary(
    ctx,
    clinic,
    client,
    auth_headers_for,
):
    _reconcile(ctx, clinic)

    response = client.post(
        (
            "/api/v1/wards/admissions/"
            f"{ctx['admission'].id}/discharge"
        ),
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json=_payload(),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["admission"]["status"] == "discharged"
    assert body["discharge_summary"]["record_status"] == "final"
    assert (
        body["discharge_summary"]["primary_diagnosis"]
        == "Resolved acute illness"
    )


def test_duplicate_discharge_is_blocked(ctx, clinic):
    _reconcile(ctx, clinic)
    _discharge(ctx, clinic)

    with pytest.raises(
        ConflictError,
        match="active admission",
    ):
        _discharge(ctx, clinic)
