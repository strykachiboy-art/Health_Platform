from __future__ import annotations

import inspect

import pytest

from app.core.enums.clinical_record_enums import ClinicalRecordStatus
from app.core.enums.emar_enums import MedicationAdministrationStatus
from app.core.enums.medication_reconciliation_enums import (
    MedicationReconciliationEvent,
)
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import IdempotencyRecord
from app.extensions import db
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.emar.services import emar_service
from app.modules.medication_reconciliation.services import (
    medication_reconciliation_service as med_recon_service,
)
from app.modules.ward.models.discharge_summary_model import DischargeSummary
from app.modules.ward.services import discharge_service, ward_service
from app.tests.modules.emar.test_emar_service_invariants import (
    _base_context,
    _schedule,
)
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _setup_admission,
)


def _ledger(clinic_id: int, user_id: int, operation: str, key: str):
    return db.session.execute(
        db.select(IdempotencyRecord).where(
            IdempotencyRecord.clinic_id == clinic_id,
            IdempotencyRecord.user_id == user_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == key,
        )
    ).scalar_one()


def test_prn_administration_replay_returns_same_clinical_action(
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
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
        is_prn=True,
    )

    key = "gate18-prn-replay"

    first = emar_service.administer_prn_medication(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        prescription_item_id=item.id,
        actor_staff_id=actor.id,
        notes="Breakthrough symptom",
        idempotency_key=key,
        idempotency_user_id=actor.user_id,
    )

    second = emar_service.administer_prn_medication(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        prescription_item_id=item.id,
        actor_staff_id=actor.id,
        notes="Breakthrough symptom",
        idempotency_key=key,
        idempotency_user_id=actor.user_id,
    )

    assert second.id == first.id
    assert second.status == MedicationAdministrationStatus.ADMINISTERED

    rows = list(
        db.session.execute(
            db.select(MedicationAdministration).where(
                MedicationAdministration.clinic_id == clinic.id,
                MedicationAdministration.admission_id == admission.id,
                MedicationAdministration.prescription_item_id == item.id,
            )
        ).scalars()
    )
    assert [row.id for row in rows] == [first.id]

    ledger = _ledger(
        clinic.id,
        actor.user_id,
        "emar.prn_administer",
        key,
    )
    assert ledger.entity_type == "MedicationAdministration"
    assert ledger.entity_id == first.id


@pytest.mark.parametrize(
    ("service_name", "operation", "expected_status"),
    (
        (
            "hold_medication",
            "emar.hold",
            MedicationAdministrationStatus.HELD,
        ),
        (
            "record_medication_refusal",
            "emar.refuse",
            MedicationAdministrationStatus.REFUSED,
        ),
        (
            "mark_medication_missed",
            "emar.miss",
            MedicationAdministrationStatus.MISSED,
        ),
    ),
)
def test_retry_sensitive_terminal_emar_replay_is_same_action(
    service_name,
    operation,
    expected_status,
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
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    key = f"gate18-{service_name}"
    service = getattr(emar_service, service_name)

    kwargs = {
        "administration_id": record.id,
        "clinic_id": clinic.id,
        "actor_staff_id": actor.id,
        "reason": "Gate 18 retry proof",
        "idempotency_key": key,
        "idempotency_user_id": actor.user_id,
    }

    first = service(**kwargs)
    second = service(**kwargs)

    assert second.id == first.id == record.id
    assert second.status == expected_status

    ledger = _ledger(
        clinic.id,
        actor.user_id,
        operation,
        key,
    )
    assert ledger.entity_type == "MedicationAdministration"
    assert ledger.entity_id == record.id


def test_retry_sensitive_emar_same_key_different_payload_conflicts(
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
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    key = "gate18-hold-conflict"

    emar_service.hold_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        reason="First reason",
        idempotency_key=key,
        idempotency_user_id=actor.user_id,
    )

    with pytest.raises(
        ConflictError,
        match="different request payload",
    ):
        emar_service.hold_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            reason="Different reason",
            idempotency_key=key,
            idempotency_user_id=actor.user_id,
        )


def test_medication_reconciliation_finalization_replays_same_result(
    clinic,
    make_patient,
    make_staff,
):
    (
        _patient,
        _nurse,
        doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    record = med_recon_service.create_medication_reconciliation(
        clinic_id=clinic.id,
        admission_id=admission.id,
        event_type=MedicationReconciliationEvent.ADMISSION,
        actor_staff_id=doctor.id,
        no_medications_reported=True,
        items=[],
    )

    key = "gate18-medrec-finalize"

    first = med_recon_service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=doctor.id,
        expected_version=1,
        idempotency_key=key,
        idempotency_user_id=doctor.user_id,
    )
    second = med_recon_service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=doctor.id,
        expected_version=1,
        idempotency_key=key,
        idempotency_user_id=doctor.user_id,
    )

    assert first.id == second.id == record.id
    assert second.record_status == ClinicalRecordStatus.FINAL.value

    ledger = _ledger(
        clinic.id,
        doctor.user_id,
        "medication_reconciliation.finalize",
        key,
    )
    assert ledger.entity_type == "MedicationReconciliation"
    assert ledger.entity_id == record.id


def test_medication_reconciliation_finalization_payload_conflict(
    clinic,
    make_patient,
    make_staff,
):
    (
        _patient,
        _nurse,
        doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    record = med_recon_service.create_medication_reconciliation(
        clinic_id=clinic.id,
        admission_id=admission.id,
        event_type=MedicationReconciliationEvent.ADMISSION,
        actor_staff_id=doctor.id,
        no_medications_reported=True,
        items=[],
    )

    key = "gate18-medrec-conflict"

    med_recon_service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=doctor.id,
        expected_version=1,
        idempotency_key=key,
        idempotency_user_id=doctor.user_id,
    )

    with pytest.raises(
        ConflictError,
        match="different request payload",
    ):
        med_recon_service.finalize_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=doctor.id,
            expected_version=2,
            idempotency_key=key,
            idempotency_user_id=doctor.user_id,
        )


def _discharge_payload():
    return {
        "reason": "Gate 18 discharge",
        "primary_diagnosis": "Resolved acute illness",
        "secondary_diagnoses": ["Hypertension"],
        "condition_at_discharge": "Stable",
        "instructions": "Return for worsening symptoms",
        "follow_up_plan": "Clinic review in seven days",
        "destination": "Home",
    }


def _prepare_discharge(
    *,
    clinic,
    make_patient,
    make_staff,
):
    (
        patient,
        _nurse,
        doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    reconciliation = med_recon_service.create_medication_reconciliation(
        clinic_id=clinic.id,
        admission_id=admission.id,
        event_type=MedicationReconciliationEvent.DISCHARGE,
        actor_staff_id=doctor.id,
        no_medications_reported=True,
        items=[],
    )

    med_recon_service.finalize_medication_reconciliation(
        reconciliation.id,
        clinic_id=clinic.id,
        actor_staff_id=doctor.id,
        expected_version=1,
    )

    return patient, doctor, admission


def test_discharge_finalization_replay_returns_same_summary(
    clinic,
    make_patient,
    make_staff,
):
    _patient, doctor, admission = _prepare_discharge(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    key = "gate18-discharge-replay"
    payload = _discharge_payload()

    first = ward_service.discharge_patient(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_user_id=doctor.user_id,
        actor_staff_id=doctor.id,
        idempotency_key=key,
        idempotency_user_id=doctor.user_id,
        **payload,
    )
    second = ward_service.discharge_patient(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_user_id=doctor.user_id,
        actor_staff_id=doctor.id,
        idempotency_key=key,
        idempotency_user_id=doctor.user_id,
        **payload,
    )

    assert first.id == second.id == admission.id

    summaries = list(
        db.session.execute(
            db.select(DischargeSummary).where(
                DischargeSummary.clinic_id == clinic.id,
                DischargeSummary.admission_id == admission.id,
            )
        ).scalars()
    )
    assert len(summaries) == 1

    ledger = _ledger(
        clinic.id,
        doctor.user_id,
        "discharge.finalize",
        key,
    )
    assert ledger.entity_type == "DischargeSummary"
    assert ledger.entity_id == summaries[0].id


def test_discharge_finalization_same_key_different_payload_conflicts(
    clinic,
    make_patient,
    make_staff,
):
    _patient, doctor, admission = _prepare_discharge(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    key = "gate18-discharge-conflict"
    payload = _discharge_payload()

    ward_service.discharge_patient(
        admission_id=admission.id,
        clinic_id=clinic.id,
        actor_user_id=doctor.user_id,
        actor_staff_id=doctor.id,
        idempotency_key=key,
        idempotency_user_id=doctor.user_id,
        **payload,
    )

    with pytest.raises(
        ConflictError,
        match="different request payload",
    ):
        ward_service.discharge_patient(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_user_id=doctor.user_id,
            actor_staff_id=doctor.id,
            idempotency_key=key,
            idempotency_user_id=doctor.user_id,
            **{
                **payload,
                "destination": "Rehabilitation facility",
            },
        )


def test_gate18_routes_require_idempotency_key():
    from app.modules.emar.routes import emar_routes
    from app.modules.medication_reconciliation.routes import (
        medication_reconciliation_route as medrec_routes,
    )
    from app.modules.ward.routes import ward_route

    for handler in (
        emar_routes.administer_prn_medication_route,
        emar_routes.hold_medication_route,
        emar_routes.record_medication_refusal_route,
        emar_routes.mark_medication_missed_route,
        medrec_routes.finalize_reconciliation_route,
        ward_route.discharge_patient_route,
    ):
        source = inspect.getsource(inspect.unwrap(handler))
        assert "_required_idempotency_key()" in source
        assert "idempotency_user_id" in source
