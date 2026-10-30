from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.clinical_safety.models.clinical_alert_model import (
    ClinicalAlert,
)
from app.core.clinical_safety.models.clinical_rule_model import (
    ClinicalRule,
)
from app.core.enums.clinical_safety_enums import (
    ClinicalAlertStatus,
    ClinicalRuleAction,
    ClinicalRuleScope,
    ClinicalRuleSeverity,
    ClinicalRuleType,
)
from app.core.enums.consultation_enums import ConsultationStatus
from app.core.enums.emar_enums import MedicationAdministrationStatus
from app.core.enums.lab_enums import (
    LabOrderStatus,
    LabResultFlag,
)
from app.core.enums.patient_enums import (
    AllergyClinicalStatus,
    AllergySeverity,
    AllergyType,
    AllergyVerificationStatus,
)
from app.core.enums.prescription_enums import PrescriptionStatus
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import (
    BedStatus,
    WardType,
)
from app.core.exceptions import NotFoundError
from app.extensions import db
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.lab.models.lab_model import (
    LabOrder,
    LabOrderItem,
    LabTest,
)
from app.modules.nursing.services import nursing_service
from app.modules.patient.models.patient_model import PatientAllergy
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.ward.models.ward_model import (
    Bed,
    Ward,
)

from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
    _setup_admission,
)


def _context(
    *,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    monkeypatch.setattr(
        nursing_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    (
        patient,
        outgoing,
        doctor,
        ward,
        bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    incoming = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    start = _db_now()

    handoff = nursing_service.create_handoff(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_staff_id=outgoing.id,
        incoming_staff_id=incoming.id,
        shift_start_at=start,
        shift_end_at=(
            start + timedelta(hours=8)
        ),
        situation="Stable",
        background="Inpatient care",
        assessment="No acute distress",
        recommendation="Continue plan",
        safety_concerns="Falls risk",
        pending_actions="Review labs",
    )

    return {
        "patient": patient,
        "outgoing": outgoing,
        "incoming": incoming,
        "doctor": doctor,
        "ward": ward,
        "bed": bed,
        "admission": admission,
        "handoff": handoff,
    }


def test_summary_resolves_live_patient_admission_ward_bed(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    first = nursing_service.get_handoff_summary(
        ctx["handoff"].id,
        clinic_id=clinic.id,
    )

    assert first["patient"]["id"] == ctx["patient"].id
    assert first["admission"]["bed_id"] == ctx["bed"].id
    assert first["handoff"].situation == "Stable"

    new_ward = Ward(
        clinic_id=clinic.id,
        name="Gate 9 Transfer Ward",
        ward_type=WardType.GENERAL,
        capacity=2,
    )
    db.session.add(new_ward)
    db.session.flush()

    new_bed = Bed(
        ward_id=new_ward.id,
        bed_number="G9-NEW",
        status=BedStatus.OCCUPIED,
    )
    db.session.add(new_bed)
    db.session.flush()

    ctx["admission"].bed_id = new_bed.id
    db.session.commit()

    second = nursing_service.get_handoff_summary(
        ctx["handoff"].id,
        clinic_id=clinic.id,
    )

    assert second["admission"]["bed_id"] == new_bed.id
    assert second["admission"]["ward_id"] == new_ward.id


def test_summary_resolves_diagnoses_and_active_allergies(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    consultation = Consultation(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        staff_id=ctx["doctor"].id,
        icd10_code="J18.9",
        status=ConsultationStatus.COMPLETED,
        diagnosis="Community acquired pneumonia",
        started_at=_db_now(),
        ended_at=_db_now(),
    )

    active = PatientAllergy(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        allergen_name="Penicillin",
        allergen_code="RXNORM:7980",
        allergen_type=AllergyType.DRUG.value,
        reaction="Urticaria",
        severity=AllergySeverity.SEVERE.value,
        clinical_status=AllergyClinicalStatus.ACTIVE.value,
        verification_status=(
            AllergyVerificationStatus.CONFIRMED.value
        ),
        recorded_by_id=ctx["outgoing"].id,
    )

    resolved = PatientAllergy(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        allergen_name="Resolved allergy",
        allergen_type=AllergyType.OTHER.value,
        severity=AllergySeverity.MILD.value,
        clinical_status=AllergyClinicalStatus.RESOLVED.value,
        verification_status=(
            AllergyVerificationStatus.CONFIRMED.value
        ),
        recorded_by_id=ctx["outgoing"].id,
    )

    db.session.add_all(
        [consultation, active, resolved]
    )
    db.session.commit()

    summary = nursing_service.get_handoff_summary(
        ctx["handoff"].id,
        clinic_id=clinic.id,
    )

    assert [
        item["diagnosis"]
        for item in summary["active_diagnoses"]
    ] == ["Community acquired pneumonia"]

    assert [
        item["allergen_name"]
        for item in summary["allergies"]
    ] == ["Penicillin"]


def test_summary_resolves_active_medication_orders_and_due_emar(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    drug = Drug(
        clinic_id=clinic.id,
        name="Gate 9 Ceftriaxone",
        generic_name="ceftriaxone",
        is_active=True,
    )
    db.session.add(drug)
    db.session.flush()

    prescription = Prescription(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        prescribed_by_id=ctx["doctor"].id,
        status=PrescriptionStatus.ACTIVE,
    )
    db.session.add(prescription)
    db.session.flush()

    item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dose_value=1,
        dose_unit="g",
        route_code="iv",
        frequency_code="q24h",
        order_priority="urgent",
        order_type="inpatient",
        ordered_at=_db_now(),
    )
    db.session.add(item)
    db.session.flush()

    due = MedicationAdministration(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        prescription_id=prescription.id,
        prescription_item_id=item.id,
        scheduled_at=(
            ctx["handoff"].shift_start_at
            + timedelta(hours=1)
        ),
        dose_value=1,
        dose_unit="g",
        route_code="iv",
        status=MedicationAdministrationStatus.SCHEDULED,
        source_order_version=1,
    )

    later = MedicationAdministration(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        prescription_id=prescription.id,
        prescription_item_id=item.id,
        scheduled_at=(
            ctx["handoff"].shift_end_at
            + timedelta(hours=2)
        ),
        dose_value=1,
        dose_unit="g",
        route_code="iv",
        status=MedicationAdministrationStatus.SCHEDULED,
        source_order_version=1,
    )

    db.session.add_all([due, later])
    db.session.commit()

    summary = nursing_service.get_handoff_summary(
        ctx["handoff"].id,
        clinic_id=clinic.id,
    )

    assert len(summary["active_medication_orders"]) == 1
    assert (
        summary["active_medication_orders"][0]["drug_name"]
        == "Gate 9 Ceftriaxone"
    )
    assert [
        row["id"]
        for row in summary["due_medication_administrations"]
    ] == [due.id]


def test_summary_resolves_pending_labs_results(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    lab_test = LabTest(
        clinic_id=clinic.id,
        name="Gate 9 Potassium",
        code="G9-K",
        unit="mmol/L",
        is_active=True,
    )
    db.session.add(lab_test)
    db.session.flush()

    order = LabOrder(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        ordered_by_id=ctx["doctor"].id,
        status=LabOrderStatus.IN_PROGRESS,
    )
    db.session.add(order)
    db.session.flush()

    item = LabOrderItem(
        order_id=order.id,
        test_id=lab_test.id,
        result_value="6.2",
        flag=LabResultFlag.CRITICAL,
        resulted_at=_db_now(),
    )
    db.session.add(item)
    db.session.commit()

    summary = nursing_service.get_handoff_summary(
        ctx["handoff"].id,
        clinic_id=clinic.id,
    )

    assert len(summary["pending_lab_orders"]) == 1
    lab = summary["pending_lab_orders"][0]
    assert lab["id"] == order.id
    assert lab["results"][0]["test_name"] == "Gate 9 Potassium"
    assert lab["results"][0]["flag"] == "critical"


def test_summary_resolves_care_priority_tasks_and_signed_notes(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    plan = nursing_service.create_care_plan(
        admission_id=ctx["admission"].id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["outgoing"].id,
        title="Falls prevention",
        problem="High falls risk",
        goal="No inpatient falls",
    )

    task = nursing_service.create_care_task(
        care_plan_id=plan.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["outgoing"].id,
        description="Hourly safety round",
        priority="urgent",
        assigned_to_staff_id=ctx["incoming"].id,
    )

    signed = nursing_service.create_nursing_note(
        admission_id=ctx["admission"].id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["outgoing"].id,
        note_text="Patient mobilized with assistance.",
    )

    nursing_service.sign_nursing_note(
        signed.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["outgoing"].id,
    )

    nursing_service.create_nursing_note(
        admission_id=ctx["admission"].id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["outgoing"].id,
        note_text="Draft should not hand off.",
    )

    summary = nursing_service.get_handoff_summary(
        ctx["handoff"].id,
        clinic_id=clinic.id,
    )

    priority = summary["care_plan_priorities"][0]
    assert priority["highest_open_task_priority"] == "urgent"
    assert priority["open_task_count"] == 1
    assert [row["id"] for row in summary["tasks"]] == [task.id]
    assert [
        row["id"]
        for row in summary["recent_significant_notes"]
    ] == [signed.id]


def test_summary_resolves_only_unresolved_critical_alerts(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    rule = ClinicalRule(
        clinic_id=clinic.id,
        rule_code="G9-ALERT",
        name="Gate 9 critical alert rule",
        scope=ClinicalRuleScope.CLINIC,
        rule_type=ClinicalRuleType.ALLERGY_CONFLICT,
        severity=ClinicalRuleSeverity.HIGH,
        action=ClinicalRuleAction.ALERT,
        conditions={},
        configuration={},
        enabled=True,
        version=1,
    )
    db.session.add(rule)
    db.session.flush()

    high_open = ClinicalAlert(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        rule_id=rule.id,
        rule_version=1,
        severity=ClinicalRuleSeverity.HIGH,
        action=ClinicalRuleAction.ALERT,
        status=ClinicalAlertStatus.OPEN,
        title="High allergy risk",
        message="Avoid triggering medication.",
        context={},
        source_type="prescription",
    )

    low_open = ClinicalAlert(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        rule_id=rule.id,
        rule_version=1,
        severity=ClinicalRuleSeverity.LOW,
        action=ClinicalRuleAction.ALERT,
        status=ClinicalAlertStatus.OPEN,
        title="Low alert",
        message="Not critical for handoff.",
        context={},
        source_type="prescription",
    )

    resolved = ClinicalAlert(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        rule_id=rule.id,
        rule_version=1,
        severity=ClinicalRuleSeverity.CRITICAL,
        action=ClinicalRuleAction.BLOCK,
        status=ClinicalAlertStatus.RESOLVED,
        title="Resolved critical alert",
        message="Already resolved.",
        context={},
        source_type="prescription",
    )

    db.session.add_all(
        [high_open, low_open, resolved]
    )
    db.session.commit()

    summary = nursing_service.get_handoff_summary(
        ctx["handoff"].id,
        clinic_id=clinic.id,
    )

    assert [
        alert["id"]
        for alert in summary["critical_alerts"]
    ] == [high_open.id]


def test_summary_is_live_not_snapshot(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    before = nursing_service.get_handoff_summary(
        ctx["handoff"].id,
        clinic_id=clinic.id,
    )
    assert before["active_diagnoses"] == []

    db.session.add(
        Consultation(
            clinic_id=clinic.id,
            patient_id=ctx["patient"].id,
            staff_id=ctx["doctor"].id,
            status=ConsultationStatus.COMPLETED,
            diagnosis="New diagnosis after handoff creation",
            started_at=_db_now(),
            ended_at=_db_now(),
        )
    )
    db.session.commit()

    after = nursing_service.get_handoff_summary(
        ctx["handoff"].id,
        clinic_id=clinic.id,
    )

    assert [
        row["diagnosis"]
        for row in after["active_diagnoses"]
    ] == ["New diagnosis after handoff creation"]


def test_summary_is_tenant_scoped(
    monkeypatch,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    other = make_clinic(
        name="Gate 9 Foreign Clinic",
    )

    with pytest.raises(NotFoundError):
        nursing_service.get_handoff_summary(
            ctx["handoff"].id,
            clinic_id=other.id,
        )


@pytest.mark.parametrize(
    "role",
    (
        Role.NURSE,
        Role.DOCTOR,
        Role.ADMIN,
    ),
)
def test_summary_route_allows_view_roles(
    role,
    monkeypatch,
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    viewer = make_staff(
        clinic=clinic,
        role=role,
    )

    response = client.get(
        (
            "/api/v1/nursing/handoffs/"
            f"{ctx['handoff'].id}/summary"
        ),
        headers=auth_headers_for(
            viewer.user,
            role=role,
        ),
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["handoff"]["id"] == ctx["handoff"].id
    assert payload["patient"]["id"] == ctx["patient"].id


def test_summary_route_requires_authentication(client):
    response = client.get(
        "/api/v1/nursing/handoffs/1/summary"
    )
    assert response.status_code in (401, 422)
