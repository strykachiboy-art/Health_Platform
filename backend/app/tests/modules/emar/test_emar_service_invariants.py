from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import pytest

import app.modules.emar.services.emar_service as emar_service
from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.core.enums.prescription_enums import (
    MedicationOrderPriority,
    MedicationOrderType,
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.extensions import db as db_ext
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.ward.models.ward_model import Admission


def _actor(
    make_authenticated_staff,
    clinic,
    role=Role.NURSE,
):
    staff, _ = make_authenticated_staff(
        clinic,
        role,
    )
    return staff


def _admission(
    *,
    db,
    clinic,
    patient,
    actor,
    make_ward,
    make_bed,
):
    ward = make_ward(
        clinic,
    )
    bed = make_bed(
        ward,
    )

    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=actor.id,
        status=AdmissionStatus.ADMITTED,
    )

    db.session.add(admission)
    db.session.flush()

    return admission


def _order(
    *,
    db,
    clinic,
    patient,
    actor,
    drug,
    is_prn=False,
    status=PrescriptionStatus.ACTIVE,
    expires_at=None,
    order_type=MedicationOrderType.INPATIENT,
    dose_value=500,
    dose_unit="mg",
    route_code="oral",
    start_at=None,
    end_at=None,
):
    prescription = Prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=actor.id,
        status=status,
        expires_at=expires_at,
    )

    db.session.add(
        prescription
    )
    db.session.flush()

    item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dose_value=dose_value,
        dose_unit=dose_unit,
        route_code=route_code,
        is_prn=is_prn,
        prn_reason=(
            "Breakthrough symptom"
            if is_prn
            else None
        ),
        order_priority=(
            MedicationOrderPriority.ROUTINE
        ),
        order_type=order_type,
        start_at=start_at,
        end_at=end_at,
        ordered_at=datetime.now(
            timezone.utc
        ),
    )

    db.session.add(item)
    db.session.flush()

    return prescription, item


def _schedule(
    *,
    clinic,
    patient,
    admission,
    item,
    actor,
    scheduled_at=None,
):
    return (
        emar_service
        .schedule_medication_administration(
            clinic_id=clinic.id,
            patient_id=patient.id,
            admission_id=admission.id,
            prescription_item_id=item.id,
            scheduled_at=(
                scheduled_at
                or datetime.now(
                    timezone.utc
                )
            ),
            actor_staff_id=actor.id,
        )
    )


def _base_context(
    *,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    **order_kwargs,
):
    actor = _actor(
        make_authenticated_staff,
        clinic,
    )

    drug = make_drug(
        clinic,
        is_active=True,
    )

    admission = _admission(
        db=db,
        clinic=clinic,
        patient=patient,
        actor=actor,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    prescription, item = _order(
        db=db,
        clinic=clinic,
        patient=patient,
        actor=actor,
        drug=drug,
        **order_kwargs,
    )

    return (
        actor,
        drug,
        admission,
        prescription,
        item,
    )


def test_schedule_copies_structured_order_and_audits(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    audits = []

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **kwargs: audits.append(
            kwargs
        ),
    )

    scheduled_at = datetime.now(
        timezone.utc
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
        scheduled_at=scheduled_at,
    )

    assert record.id is not None
    assert (
        record.status
        == MedicationAdministrationStatus.SCHEDULED
    )
    assert (
        record.prescription_id
        == prescription.id
    )
    assert float(record.dose_value) == 500.0
    assert record.dose_unit == "mg"
    assert record.route_code == "oral"
    assert record.source_order_version == 1

    assert len(audits) == 1
    assert (
        audits[0]["entity_type"]
        == "MedicationAdministration"
    )


def test_cross_patient_order_is_denied(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    other_patient = Patient(
        clinic_id=clinic.id,
        first_name="Other",
        last_name="Patient",
        patient_number=(
            f"EMAR-OTHER-{patient.id}"
        ),
        is_active=True,
    )

    db.session.add(
        other_patient
    )
    db.session.flush()

    with pytest.raises(
        ValidationError,
        match="Admission does not belong to the patient",
    ):
        emar_service.schedule_medication_administration(
            clinic_id=clinic.id,
            patient_id=other_patient.id,
            admission_id=admission.id,
            prescription_item_id=item.id,
            scheduled_at=datetime.now(
                timezone.utc
            ),
            actor_staff_id=actor.id,
        )


def test_cross_clinic_emar_is_denied(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    other_clinic = Clinic(
        name=(
            f"Other Clinic {clinic.id}"
        )
    )

    db.session.add(
        other_clinic
    )
    db.session.flush()

    with pytest.raises(
        ValidationError,
        match="Patient does not belong to the clinic",
    ):
        emar_service.schedule_medication_administration(
            clinic_id=other_clinic.id,
            patient_id=patient.id,
            admission_id=admission.id,
            prescription_item_id=item.id,
            scheduled_at=datetime.now(
                timezone.utc
            ),
            actor_staff_id=actor.id,
        )


def test_inactive_admission_is_denied(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    admission.status = (
        AdmissionStatus.DISCHARGED
    )
    admission.discharged_at = (
        datetime.now(
            timezone.utc
        )
    )

    with pytest.raises(
        ConflictError,
        match="active admission",
    ):
        _schedule(
            clinic=clinic,
            patient=patient,
            admission=admission,
            item=item,
            actor=actor,
        )


@pytest.mark.parametrize(
    "status",
    [
        PrescriptionStatus.CANCELLED,
        PrescriptionStatus.COMPLETED,
        PrescriptionStatus.EXPIRED,
    ],
)
def test_nonactive_prescription_is_denied(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    status,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
        status=status,
    )

    with pytest.raises(
        ConflictError,
        match="is not active",
    ):
        _schedule(
            clinic=clinic,
            patient=patient,
            admission=admission,
            item=item,
            actor=actor,
        )


def test_expired_prescription_is_denied(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    now = datetime.now(
        timezone.utc
    )

    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
        expires_at=(
            now
            - timedelta(minutes=1)
        ),
    )

    with pytest.raises(
        ConflictError,
        match="has expired",
    ):
        _schedule(
            clinic=clinic,
            patient=patient,
            admission=admission,
            item=item,
            actor=actor,
            scheduled_at=now,
        )


def test_outpatient_order_is_denied(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
        order_type=(
            MedicationOrderType.OUTPATIENT
        ),
    )

    with pytest.raises(
        ValidationError,
        match="inpatient medication order",
    ):
        _schedule(
            clinic=clinic,
            patient=patient,
            admission=admission,
            item=item,
            actor=actor,
        )


def test_unstructured_order_is_denied(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
        route_code=None,
    )

    with pytest.raises(
        ValidationError,
        match="structured dose",
    ):
        _schedule(
            clinic=clinic,
            patient=patient,
            admission=admission,
            item=item,
            actor=actor,
        )


def test_unauthorized_role_is_denied(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        nurse_actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    receptionist = _actor(
        make_authenticated_staff,
        clinic,
        role=Role.RECEPTIONIST,
    )

    with pytest.raises(
        ValidationError,
        match="must have one of these roles",
    ):
        _schedule(
            clinic=clinic,
            patient=patient,
            admission=admission,
            item=item,
            actor=receptionist,
        )


def test_inactive_staff_is_denied(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    actor.status = (
        StaffStatus.SUSPENDED
    )

    with pytest.raises(
        ValidationError,
        match="must be active staff",
    ):
        _schedule(
            clinic=clinic,
            patient=patient,
            admission=admission,
            item=item,
            actor=actor,
        )


def test_hold_and_refusal_require_reasons(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    held = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    with pytest.raises(
        ValidationError,
        match="hold_reason is required",
    ):
        emar_service.hold_medication(
            administration_id=held.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            reason="   ",
        )

    refused = MedicationAdministration(
        clinic_id=held.clinic_id,
        patient_id=held.patient_id,
        admission_id=held.admission_id,
        prescription_id=held.prescription_id,
        prescription_item_id=held.prescription_item_id,
        scheduled_at=(
            held.scheduled_at
            + timedelta(hours=1)
        ),
        dose_value=held.dose_value,
        dose_unit=held.dose_unit,
        route_code=held.route_code,
        status=(
            MedicationAdministrationStatus
            .SCHEDULED
        ),
        source_order_version=1,
    )

    db.session.add(refused)
    db.session.flush()

    with pytest.raises(
        ValidationError,
        match="refusal_reason is required",
    ):
        (
            emar_service
            .record_medication_refusal(
                administration_id=refused.id,
                clinic_id=clinic.id,
                actor_staff_id=actor.id,
                reason="",
            )
        )


@pytest.mark.parametrize(
    (
        "transition",
        "expected_status",
        "reason_field",
    ),
    [
        (
            "hold",
            MedicationAdministrationStatus.HELD,
            "hold_reason",
        ),
        (
            "refuse",
            MedicationAdministrationStatus.REFUSED,
            "refusal_reason",
        ),
        (
            "miss",
            MedicationAdministrationStatus.MISSED,
            "missed_reason",
        ),
        (
            "cancel",
            MedicationAdministrationStatus.CANCELLED,
            "cancel_reason",
        ),
    ],
)
def test_terminal_nonadministration_transitions(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    transition,
    expected_status,
    reason_field,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    if transition == "hold":
        result = (
            emar_service
            .hold_medication(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=actor.id,
                reason="Clinical hold",
            )
        )
    elif transition == "refuse":
        result = (
            emar_service
            .record_medication_refusal(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=actor.id,
                reason="Patient refused",
            )
        )
    elif transition == "miss":
        result = (
            emar_service
            .mark_medication_missed(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=actor.id,
                reason="Dose window elapsed",
            )
        )
    else:
        result = (
            emar_service
            .cancel_medication_administration(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=actor.id,
                reason="Order withdrawn",
            )
        )

    assert result.status == expected_status
    assert getattr(
        result,
        reason_field,
    ) is not None


def test_scheduled_dose_can_be_marked_due_then_administered(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    due = (
        emar_service
        .mark_medication_administration_due(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
        )
    )

    assert (
        due.status
        == MedicationAdministrationStatus.DUE
    )

    administered = (
        emar_service
        .administer_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            notes="Given as ordered",
        )
    )

    assert (
        administered.status
        == MedicationAdministrationStatus.ADMINISTERED
    )
    assert (
        administered.administered_by_staff_id
        == actor.id
    )
    assert administered.administered_at is not None


def test_administer_after_discharge_is_denied(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    admission.status = (
        AdmissionStatus.DISCHARGED
    )
    admission.discharged_at = (
        datetime.now(
            timezone.utc
        )
    )

    with pytest.raises(
        ConflictError,
        match="active admission",
    ):
        (
            emar_service
            .administer_medication(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=actor.id,
            )
        )


def test_administer_after_order_cancelled_is_denied(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    prescription.status = (
        PrescriptionStatus.CANCELLED
    )

    with pytest.raises(
        ConflictError,
        match="is not active",
    ):
        (
            emar_service
            .administer_medication(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=actor.id,
            )
        )


def test_terminal_record_cannot_then_be_administered(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    (
        emar_service
        .hold_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            reason="Clinical hold",
        )
    )

    with pytest.raises(
        ConflictError,
        match="already held",
    ):
        (
            emar_service
            .administer_medication(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=actor.id,
            )
        )


def test_prn_administration_requires_prn_order(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    with pytest.raises(
        ValidationError,
        match="requires a PRN medication order",
    ):
        (
            emar_service
            .administer_prn_medication(
                clinic_id=clinic.id,
                patient_id=patient.id,
                admission_id=admission.id,
                prescription_item_id=item.id,
                actor_staff_id=actor.id,
            )
        )


def test_prn_order_can_be_administered_without_scheduled_time(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
        is_prn=True,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = (
        emar_service
        .administer_prn_medication(
            clinic_id=clinic.id,
            patient_id=patient.id,
            admission_id=admission.id,
            prescription_item_id=item.id,
            actor_staff_id=actor.id,
            notes="PRN dose",
        )
    )

    assert record.id is not None
    assert record.scheduled_at is None
    assert (
        record.status
        == MedicationAdministrationStatus.ADMINISTERED
    )
    assert (
        record.prescription_id
        == prescription.id
    )
