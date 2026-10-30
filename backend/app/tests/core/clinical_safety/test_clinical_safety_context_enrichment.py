from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from app.core.clinical_safety.services.clinical_safety_context_service import (
    build_clinical_safety_context,
)
from app.core.clinical_safety.services.rule_evaluator import (
    evaluate_rule,
)
from app.core.enums.clinical_safety_enums import (
    ClinicalRuleAction,
    ClinicalRuleSeverity,
    ClinicalRuleType,
)
from app.core.enums.consultation_enums import (
    ConsultationStatus,
)
from app.core.enums.lab_enums import (
    LabOrderStatus,
)
from app.core.enums.role_enums import (
    Role,
)
from app.modules.consultation.models.consultation_model import (
    Consultation,
)
from app.modules.lab.models.lab_model import (
    LabOrder,
    LabOrderItem,
    LabTest,
)


def _activate(
    clinic,
    patient,
):
    patient.clinic_id = clinic.id
    patient.is_active = True


def _staff(
    make_authenticated_staff,
    clinic,
):
    staff, _ = (
        make_authenticated_staff(
            clinic,
            Role.DOCTOR,
        )
    )

    return staff


def _rule(
    *,
    rule_id: int,
    code: str,
    rule_type: ClinicalRuleType,
    configuration: dict,
):
    return {
        "id": rule_id,
        "rule_code": code,
        "version": 1,
        "severity": (
            ClinicalRuleSeverity.HIGH
        ),
        "action": (
            ClinicalRuleAction.BLOCK
        ),
        "rule_type": rule_type,
        "priority": 10,
        "is_hard_rule": True,
        "conditions": {},
        "configuration": configuration,
    }


def _completed_lab(
    db,
    clinic,
    patient,
    staff,
    *,
    name: str,
    value: str,
    code: str | None = None,
    unit: str | None = None,
    resulted_at: datetime,
    status=LabOrderStatus.COMPLETED,
):
    test = LabTest(
        clinic_id=clinic.id,
        name=name,
        code=code,
        unit=unit,
    )

    db.session.add(test)
    db.session.flush()

    order = LabOrder(
        clinic_id=clinic.id,
        patient_id=patient.id,
        ordered_by_id=staff.id,
        status=status,
    )

    db.session.add(order)
    db.session.flush()

    item = LabOrderItem(
        order_id=order.id,
        test_id=test.id,
        result_value=value,
        resulted_at=resulted_at,
    )

    db.session.add(item)
    db.session.flush()

    return (
        test,
        order,
        item,
    )


def test_completed_consultation_icd10_codes_enrich_context(
    db,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    db.session.add_all(
        [
            Consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                status=ConsultationStatus.COMPLETED,
                icd10_code="e11.9",
            ),
            Consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                status=ConsultationStatus.COMPLETED,
                icd10_code=" E11.9 ",
            ),
            Consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                status=ConsultationStatus.COMPLETED,
                icd10_code="I10",
            ),
            Consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                status=ConsultationStatus.IN_PROGRESS,
                icd10_code="J45.909",
            ),
        ]
    )

    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    assert set(
        context["diagnosis_codes"]
    ) == {
        "E11.9",
        "I10",
    }


def test_diagnosis_codes_work_with_existing_rule_evaluator(
    db,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    db.session.add(
        Consultation(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
            status=ConsultationStatus.COMPLETED,
            icd10_code="I10",
        )
    )

    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    result = evaluate_rule(
        _rule(
            rule_id=1,
            code="diagnosis-i10",
            rule_type=(
                ClinicalRuleType.DIAGNOSIS_CONFLICT
            ),
            configuration={
                "diagnosis_codes": [
                    "i10"
                ],
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_same_lab_identity_uses_latest_result_only(
    db,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    now = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    test = LabTest(
        clinic_id=clinic.id,
        name="ALT",
        code="ALT",
        unit="U/L",
    )

    db.session.add(test)
    db.session.flush()

    for value, delta in (
        ("35", 2),
        ("92", 1),
    ):
        order = LabOrder(
            clinic_id=clinic.id,
            patient_id=patient.id,
            ordered_by_id=staff.id,
            status=LabOrderStatus.COMPLETED,
        )

        db.session.add(order)
        db.session.flush()

        db.session.add(
            LabOrderItem(
                order_id=order.id,
                test_id=test.id,
                result_value=value,
                resulted_at=now
                - timedelta(
                    hours=delta
                ),
            )
        )

    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        evaluation_at=now,
    ).to_dict()

    alt = [
        item
        for item in context["labs"]
        if item["code"] == "ALT"
    ]

    assert len(alt) == 1
    assert alt[0]["value"] == 92.0


def test_noncompleted_and_future_labs_are_excluded(
    db,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    now = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    _completed_lab(
        db,
        clinic,
        patient,
        staff,
        name="Sodium",
        code="NA",
        value="140",
        resulted_at=now
        - timedelta(
            minutes=10
        ),
    )

    _completed_lab(
        db,
        clinic,
        patient,
        staff,
        name="Potassium",
        code="K",
        value="4.5",
        resulted_at=now
        - timedelta(
            minutes=5
        ),
        status=LabOrderStatus.IN_PROGRESS,
    )

    _completed_lab(
        db,
        clinic,
        patient,
        staff,
        name="Calcium",
        code="CA",
        value="9.2",
        resulted_at=now
        + timedelta(
            minutes=5
        ),
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        evaluation_at=now,
    ).to_dict()

    codes = {
        item["code"]
        for item in context["labs"]
    }

    assert codes == {
        "NA"
    }


def test_lab_context_works_with_existing_lab_conflict_rule(
    db,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    now = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    _completed_lab(
        db,
        clinic,
        patient,
        staff,
        name="Potassium",
        code="K",
        unit="mmol/L",
        value="6.1",
        resulted_at=now,
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        evaluation_at=now,
    ).to_dict()

    result = evaluate_rule(
        _rule(
            rule_id=2,
            code="high-potassium",
            rule_type=(
                ClinicalRuleType.LAB_CONFLICT
            ),
            configuration={
                "measure": "Potassium",
                "operator": "gt",
                "threshold": 5.5,
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_renal_function_is_derived_only_from_explicit_lab_aliases(
    db,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    now = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    _completed_lab(
        db,
        clinic,
        patient,
        staff,
        name="Creatinine",
        code="CREATININE",
        value="2.4",
        resulted_at=now,
    )

    _completed_lab(
        db,
        clinic,
        patient,
        staff,
        name="Random Other Test",
        code="OTHER",
        value="999",
        resulted_at=now,
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        evaluation_at=now,
    ).to_dict()

    assert (
        context["renal_function"]
        == {
            "creatinine": 2.4,
        }
    )

    result = evaluate_rule(
        _rule(
            rule_id=3,
            code="renal-creatinine",
            rule_type=(
                ClinicalRuleType.RENAL_FUNCTION
            ),
            configuration={
                "measure": "creatinine",
                "operator": "gt",
                "threshold": 2.0,
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_hepatic_function_is_derived_only_from_explicit_lab_aliases(
    db,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    now = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    _completed_lab(
        db,
        clinic,
        patient,
        staff,
        name="ALT",
        code="ALT",
        value="95",
        resulted_at=now,
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        evaluation_at=now,
    ).to_dict()

    assert (
        context["hepatic_function"]
        == {
            "alt": 95.0,
        }
    )

    result = evaluate_rule(
        _rule(
            rule_id=4,
            code="hepatic-alt",
            rule_type=(
                ClinicalRuleType.HEPATIC_FUNCTION
            ),
            configuration={
                "measure": "alt",
                "operator": "gt",
                "threshold": 80,
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_unavailable_structured_sources_remain_explicitly_empty(
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    assert (
        context["pregnancy_status"]
        is None
    )

    assert (
        context["contraindication_codes"]
        == []
    )
