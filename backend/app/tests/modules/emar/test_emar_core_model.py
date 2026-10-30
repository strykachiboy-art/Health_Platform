from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

from sqlalchemy import (
    CheckConstraint,
    UniqueConstraint,
)

from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.core.enums.prescription_enums import (
    MedicationOrderPriority,
    MedicationOrderType,
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import (
    AdmissionStatus,
)
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.ward.models.ward_model import (
    Admission,
)


def _staff(
    make_authenticated_staff,
    clinic,
):
    staff, _ = make_authenticated_staff(
        clinic,
        Role.NURSE,
    )
    return staff


def _inpatient_order(
    *,
    db,
    clinic,
    patient,
    staff,
    drug,
    is_prn=False,
):
    prescription = Prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        status=PrescriptionStatus.ACTIVE,
    )

    db.session.add(prescription)
    db.session.flush()

    item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dose_value=500,
        dose_unit="mg",
        route_code="oral",
        is_prn=is_prn,
        prn_reason=(
            "PRN symptom relief"
            if is_prn
            else None
        ),
        order_priority=(
            MedicationOrderPriority.ROUTINE
        ),
        order_type=(
            MedicationOrderType.INPATIENT
        ),
        ordered_at=datetime.now(
            timezone.utc
        ),
    )

    db.session.add(item)
    db.session.flush()

    return prescription, item


def _admission(
    *,
    db,
    clinic,
    patient,
    staff,
    make_ward,
    make_bed,
):
    ward = make_ward(clinic)
    bed = make_bed(ward)

    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=staff.id,
        status=AdmissionStatus.ADMITTED,
    )

    db.session.add(admission)
    db.session.flush()

    return admission


def test_emar_status_contract_matches_roadmap():
    assert [
        member.value
        for member in MedicationAdministrationStatus
    ] == [
        "scheduled",
        "due",
        "administered",
        "held",
        "refused",
        "missed",
        "cancelled",
    ]


def test_emar_has_required_roadmap_fields():
    table = MedicationAdministration.__table__

    required = {
        "clinic_id",
        "patient_id",
        "admission_id",
        "prescription_id",
        "prescription_item_id",
        "scheduled_at",
        "dose_value",
        "dose_unit",
        "route_code",
        "status",
        "administered_at",
        "administered_by_staff_id",
        "held_at",
        "held_by_staff_id",
        "hold_reason",
        "refused_at",
        "refused_recorded_by_staff_id",
        "refusal_reason",
        "missed_at",
        "missed_reason",
        "cancelled_at",
        "cancel_reason",
        "source_order_version",
        "idempotency_key",
        "created_at",
        "updated_at",
    }

    assert required.issubset(
        set(table.c.keys())
    )


def test_emar_database_invariants_are_declared():
    constraints = (
        MedicationAdministration
        .__table__
        .constraints
    )

    check_names = {
        constraint.name
        for constraint in constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    for name in (
        "ck_med_admin_dose_positive",
        "ck_med_admin_source_order_version_positive",
        "ck_med_admin_scheduled_requires_time",
        "ck_med_admin_administered_requires_actor_time",
        "ck_med_admin_held_requires_evidence",
        "ck_med_admin_refused_requires_evidence",
        "ck_med_admin_missed_requires_evidence",
        "ck_med_admin_cancelled_requires_evidence",
    ):
        assert name in check_names


def test_emar_uniqueness_contracts_are_declared():
    constraints = (
        MedicationAdministration
        .__table__
        .constraints
    )

    unique_names = {
        constraint.name
        for constraint in constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "uq_med_admin_admission_item_scheduled"
        in unique_names
    )
    assert (
        "uq_med_admin_clinic_idempotency_key"
        in unique_names
    )


def test_scheduled_record_links_admission_and_full_order(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    staff = _staff(
        make_authenticated_staff,
        clinic,
    )
    drug = make_drug(
        clinic,
        is_active=True,
    )

    prescription, item = _inpatient_order(
        db=db,
        clinic=clinic,
        patient=patient,
        staff=staff,
        drug=drug,
    )

    admission = _admission(
        db=db,
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    scheduled_at = datetime(
        2026,
        10,
        30,
        9,
        0,
        tzinfo=timezone.utc,
    )

    record = MedicationAdministration(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        prescription_id=prescription.id,
        prescription_item_id=item.id,
        scheduled_at=scheduled_at,
        source_order_version=1,
    )

    db.session.add(record)
    db.session.flush()

    assert record.id is not None
    assert (
        record.status
        == MedicationAdministrationStatus.SCHEDULED
    )
    assert (
        record.prescription_id
        == prescription.id
    )
    assert (
        record.prescription_item_id
        == item.id
    )
    assert (
        record.source_order_version
        == 1
    )


def test_administered_record_requires_and_captures_actor_time(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    staff = _staff(
        make_authenticated_staff,
        clinic,
    )
    drug = make_drug(
        clinic,
        is_active=True,
    )

    prescription, item = _inpatient_order(
        db=db,
        clinic=clinic,
        patient=patient,
        staff=staff,
        drug=drug,
    )

    admission = _admission(
        db=db,
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    administered_at = datetime(
        2026,
        10,
        30,
        9,
        2,
        tzinfo=timezone.utc,
    )

    record = MedicationAdministration(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        prescription_id=prescription.id,
        prescription_item_id=item.id,
        status=(
            MedicationAdministrationStatus
            .ADMINISTERED
        ),
        administered_at=administered_at,
        administered_by_staff_id=staff.id,
        dose_value=500,
        dose_unit="mg",
        route_code="oral",
        source_order_version=1,
    )

    db.session.add(record)
    db.session.flush()

    assert record.id is not None
    assert (
        record.administered_by_staff_id
        == staff.id
    )
    assert (
        record.administered_at
        == administered_at
    )


def test_prn_administered_record_allows_null_scheduled_time(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    staff = _staff(
        make_authenticated_staff,
        clinic,
    )
    drug = make_drug(
        clinic,
        is_active=True,
    )

    prescription, item = _inpatient_order(
        db=db,
        clinic=clinic,
        patient=patient,
        staff=staff,
        drug=drug,
        is_prn=True,
    )

    admission = _admission(
        db=db,
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    record = MedicationAdministration(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        prescription_id=prescription.id,
        prescription_item_id=item.id,
        status=(
            MedicationAdministrationStatus
            .ADMINISTERED
        ),
        scheduled_at=None,
        administered_at=datetime.now(
            timezone.utc
        ),
        administered_by_staff_id=staff.id,
        dose_value=250,
        dose_unit="mg",
        route_code="oral",
        source_order_version=1,
        idempotency_key="prn-test-1",
    )

    db.session.add(record)
    db.session.flush()

    assert record.id is not None
    assert record.scheduled_at is None
    assert (
        record.idempotency_key
        == "prn-test-1"
    )


def test_emar_query_indexes_cover_patient_admission_and_order():
    index_names = {
        index.name
        for index
        in MedicationAdministration
        .__table__
        .indexes
    }

    assert (
        "ix_med_admin_clinic_patient_scheduled"
        in index_names
    )
    assert (
        "ix_med_admin_admission_status_scheduled"
        in index_names
    )
    assert (
        "ix_med_admin_prescription_item_scheduled"
        in index_names
    )
