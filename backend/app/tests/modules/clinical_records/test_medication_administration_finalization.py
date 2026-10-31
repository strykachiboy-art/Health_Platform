from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError as PydanticValidationError

import app.modules.emar.services.emar_service as emar_service
from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)
from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.extensions import db
from app.modules.clinical_records.services import (
    clinical_record_service,
)
from app.modules.emar.schemas.emar_schema import (
    MedicationAdministrationAmendmentSchema,
    MedicationAdministrationResponseSchema,
)
from app.tests.modules.emar.test_emar_service_invariants import (
    _base_context,
    _schedule,
)


@pytest.fixture()
def ctx(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    audits = []

    def capture(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        capture,
    )
    monkeypatch.setattr(
        clinical_record_service,
        "create_audit_log",
        capture,
    )

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

    db.session.commit()

    return {
        "actor": actor,
        "admission": admission,
        "prescription": prescription,
        "item": item,
        "audits": audits,
    }


def _scheduled(
    ctx,
    clinic,
    patient,
):
    return _schedule(
        clinic=clinic,
        patient=patient,
        admission=ctx["admission"],
        item=ctx["item"],
        actor=ctx["actor"],
        scheduled_at=datetime.now(timezone.utc),
    )


def _terminal_document(
    record,
    **overrides,
):
    payload = (
        emar_service
        .get_medication_administration_effective_document(
            record
        )
    )
    payload.update(overrides)
    return payload


def test_scheduled_and_due_records_remain_draft(
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    assert (
        record.record_status
        == ClinicalRecordStatus.DRAFT.value
    )
    assert record.record_version == 1
    assert record.finalized_at is None

    record = (
        emar_service
        .mark_medication_administration_due(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["actor"].id,
        )
    )

    assert (
        record.status
        == MedicationAdministrationStatus.DUE
    )
    assert (
        record.record_status
        == ClinicalRecordStatus.DRAFT.value
    )
    assert record.finalized_at is None


@pytest.mark.parametrize(
    "operation,status",
    [
        ("hold", MedicationAdministrationStatus.HELD),
        ("refuse", MedicationAdministrationStatus.REFUSED),
        ("miss", MedicationAdministrationStatus.MISSED),
        ("cancel", MedicationAdministrationStatus.CANCELLED),
    ],
)
def test_nonadministration_terminal_outcomes_auto_finalize(
    operation,
    status,
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    if operation == "hold":
        record = emar_service.hold_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["actor"].id,
            reason="Clinical hold",
        )
    elif operation == "refuse":
        record = emar_service.record_medication_refusal(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["actor"].id,
            reason="Patient declined",
        )
    elif operation == "miss":
        record = emar_service.mark_medication_missed(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["actor"].id,
            reason="Dose unavailable",
        )
    else:
        record = (
            emar_service
            .cancel_medication_administration(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=ctx["actor"].id,
                reason="Order stopped",
            )
        )

    assert record.status == status
    assert (
        record.record_status
        == ClinicalRecordStatus.FINAL.value
    )
    assert record.record_version == 1
    assert record.finalized_at is not None
    assert (
        record.finalized_by_staff_id
        == ctx["actor"].id
    )
    assert record.latest_amendment_id is None


def test_prn_administration_is_final_immediately(
    ctx,
    clinic,
    patient,
):
    ctx["item"].is_prn = True
    ctx["item"].prn_reason = "Breakthrough symptom"
    db.session.flush()

    record = emar_service.administer_prn_medication(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=ctx["admission"].id,
        prescription_item_id=ctx["item"].id,
        actor_staff_id=ctx["actor"].id,
        notes="PRN dose given",
    )

    assert (
        record.status
        == MedicationAdministrationStatus.ADMINISTERED
    )
    assert (
        record.record_status
        == ClinicalRecordStatus.FINAL.value
    )
    assert record.record_version == 1
    assert record.finalized_at == record.administered_at
    assert (
        record.finalized_by_staff_id
        == ctx["actor"].id
    )


def test_first_amendment_preserves_original_terminal_evidence(
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    record = emar_service.mark_medication_missed(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["actor"].id,
        reason="Original missed reason",
    )

    original_reason = record.missed_reason
    original_time = record.missed_at

    corrected = _terminal_document(
        record,
        missed_reason="Corrected missed reason",
    )

    record, amendment = (
        emar_service
        .amend_medication_administration(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["actor"].id,
            expected_version=1,
            reason="Correct documentation wording",
            document=corrected,
        )
    )

    assert record.missed_reason == original_reason
    assert record.missed_at == original_time

    assert (
        record.record_status
        == ClinicalRecordStatus.AMENDED.value
    )
    assert record.record_version == 2
    assert record.latest_amendment_id == amendment.id

    assert amendment.previous_version == 1
    assert amendment.new_version == 2
    assert (
        amendment.source_record_type
        == ClinicalRecordType.MEDICATION_ADMINISTRATION.value
    )
    assert (
        amendment.new_content["missed_reason"]
        == "Corrected missed reason"
    )


def test_terminal_outcome_cannot_be_changed_by_amendment(
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    record = emar_service.mark_medication_missed(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["actor"].id,
        reason="Missed",
    )

    corrected = _terminal_document(
        record,
        status="cancelled",
    )

    with pytest.raises(
        ValidationError,
        match="terminal outcome",
    ):
        (
            emar_service
            .amend_medication_administration(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=ctx["actor"].id,
                expected_version=1,
                reason="Invalid state rewrite",
                document=corrected,
            )
        )


def test_draft_administration_cannot_be_amended(
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    with pytest.raises(
        ConflictError,
        match="Only final or amended",
    ):
        (
            emar_service
            .amend_medication_administration(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=ctx["actor"].id,
                expected_version=1,
                reason="Invalid draft correction",
                document=_terminal_document(
                    record,
                    notes="Invalid",
                ),
            )
        )


def test_stale_amendment_version_is_rejected(
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    record = emar_service.mark_medication_missed(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["actor"].id,
        reason="Missed",
    )

    record, _ = (
        emar_service
        .amend_medication_administration(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["actor"].id,
            expected_version=1,
            reason="First correction",
            document=_terminal_document(
                record,
                missed_reason="First correction",
            ),
        )
    )

    with pytest.raises(
        ConflictError,
        match="record version mismatch",
    ):
        (
            emar_service
            .amend_medication_administration(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=ctx["actor"].id,
                expected_version=1,
                reason="Stale correction",
                document=_terminal_document(
                    record,
                    missed_reason="Stale correction",
                ),
            )
        )


def test_noop_amendment_is_rejected(
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    record = emar_service.mark_medication_missed(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["actor"].id,
        reason="Missed",
    )

    with pytest.raises(
        ValidationError,
        match="must change",
    ):
        (
            emar_service
            .amend_medication_administration(
                administration_id=record.id,
                clinic_id=clinic.id,
                actor_staff_id=ctx["actor"].id,
                expected_version=1,
                reason="No-op",
                document=_terminal_document(
                    record
                ),
            )
        )


def test_effective_document_reads_latest_amendment(
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    record = emar_service.mark_medication_missed(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["actor"].id,
        reason="Original",
    )

    record, amendment = (
        emar_service
        .amend_medication_administration(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["actor"].id,
            expected_version=1,
            reason="Correction",
            document=_terminal_document(
                record,
                missed_reason="Effective correction",
            ),
        )
    )

    effective = (
        emar_service
        .get_medication_administration_effective_document(
            record
        )
    )

    assert (
        effective["missed_reason"]
        == "Effective correction"
    )
    assert effective == amendment.new_content


def test_amendment_schema_forbids_extra_fields():
    with pytest.raises(
        PydanticValidationError,
    ):
        (
            MedicationAdministrationAmendmentSchema
            .model_validate(
                {
                    "expected_version": 1,
                    "reason": "Correction",
                    "document": {
                        "status": "missed",
                    },
                    "silent_overwrite": True,
                }
            )
        )


def test_response_schema_exposes_record_lifecycle(
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    record = emar_service.mark_medication_missed(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["actor"].id,
        reason="Missed",
    )

    response = (
        MedicationAdministrationResponseSchema
        .model_validate(
            record
        )
    )

    assert (
        response.record_status
        == ClinicalRecordStatus.FINAL
    )
    assert response.record_version == 1
    assert response.finalized_at is not None


def test_amendment_history_is_version_ordered(
    ctx,
    clinic,
    patient,
):
    record = _scheduled(
        ctx,
        clinic,
        patient,
    )

    record = emar_service.mark_medication_missed(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["actor"].id,
        reason="Original",
    )

    record, first = (
        emar_service
        .amend_medication_administration(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["actor"].id,
            expected_version=1,
            reason="First",
            document=_terminal_document(
                record,
                missed_reason="First",
            ),
        )
    )

    record, second = (
        emar_service
        .amend_medication_administration(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["actor"].id,
            expected_version=2,
            reason="Second",
            document=_terminal_document(
                record,
                missed_reason="Second",
            ),
        )
    )

    rows = (
        emar_service
        .get_medication_administration_amendments(
            record.id,
            clinic_id=clinic.id,
        )
    )

    assert [row.id for row in rows] == [
        first.id,
        second.id,
    ]
