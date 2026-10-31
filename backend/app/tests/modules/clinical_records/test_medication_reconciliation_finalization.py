from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)
from app.core.enums.medication_reconciliation_enums import (
    MedicationReconciliationDecision,
    MedicationReconciliationEvent,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import BedStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.clinical_records.services import (
    clinical_record_service,
)
from app.modules.medication_reconciliation.schemas.medication_reconciliation_schema import (
    MedicationReconciliationAmendmentSchema,
)
from app.modules.medication_reconciliation.services import (
    medication_reconciliation_service as service,
)
from app.modules.ward.models.ward_model import (
    Bed,
    WardTransfer,
)
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
        service,
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

    pharmacist = make_staff(
        clinic,
        role=Role.PHARMACIST,
    )

    second_bed = Bed(
        ward_id=ward.id,
        bed_number="MEDREC-2",
        status=BedStatus.AVAILABLE,
    )
    db.session.add(second_bed)
    db.session.flush()

    transfer = WardTransfer(
        admission_id=admission.id,
        from_bed_id=bed.id,
        to_bed_id=second_bed.id,
        reason="Step-down transfer",
        transferred_at=datetime.now(
            timezone.utc
        ),
    )
    db.session.add(transfer)
    db.session.flush()

    db.session.commit()

    return {
        "patient": patient,
        "nurse": nurse,
        "doctor": doctor,
        "pharmacist": pharmacist,
        "ward": ward,
        "bed": bed,
        "admission": admission,
        "transfer": transfer,
        "audits": audits,
    }


def _item(
    *,
    decision=MedicationReconciliationDecision.CONTINUE,
    medication_name="Metformin",
    **overrides,
):
    payload = {
        "source_prescription_item_id": None,
        "medication_name": medication_name,
        "decision": decision,
        "replacement_medication_name": None,
        "dose_text": "500 mg",
        "route_text": "oral",
        "frequency_text": "twice daily",
        "instructions": "Take with food",
        "rationale": "Reviewed",
    }

    payload.update(overrides)

    if (
        decision
        == MedicationReconciliationDecision.REPLACE
        and payload[
            "replacement_medication_name"
        ] is None
    ):
        payload[
            "replacement_medication_name"
        ] = "Alternative medicine"

    return payload


def _create(
    ctx,
    clinic,
    *,
    event=MedicationReconciliationEvent.ADMISSION,
    actor_key="doctor",
    **overrides,
):
    payload = {
        "clinic_id": clinic.id,
        "admission_id": ctx["admission"].id,
        "event_type": event,
        "actor_staff_id": ctx[actor_key].id,
        "ward_transfer_id": (
            ctx["transfer"].id
            if event
            == MedicationReconciliationEvent.TRANSFER
            else None
        ),
        "no_medications_reported": False,
        "notes": "Medication history reviewed",
        "items": [
            _item()
        ],
    }

    payload.update(overrides)

    return service.create_medication_reconciliation(
        **payload
    )


def test_event_taxonomy_is_exact():
    assert {
        item.value
        for item in MedicationReconciliationEvent
    } == {
        "admission",
        "transfer",
        "discharge",
    }


def test_decision_taxonomy_is_exact():
    assert {
        item.value
        for item in MedicationReconciliationDecision
    } == {
        "continue",
        "hold",
        "stop",
        "replace",
        "new",
        "verify",
        "unknown",
    }


def test_blueprint_is_registered(app):
    rules = {
        rule.rule
        for rule in app.url_map.iter_rules()
    }

    expected = {
        "/api/v1/medication-reconciliations",
        (
            "/api/v1/medication-reconciliations/"
            "<int:reconciliation_id>"
        ),
        (
            "/api/v1/medication-reconciliations/"
            "<int:reconciliation_id>/finalize"
        ),
        (
            "/api/v1/medication-reconciliations/"
            "<int:reconciliation_id>/amend"
        ),
    }

    assert expected.issubset(rules)


def test_admission_reconciliation_has_stable_event_identity(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
    )

    assert (
        record.event_type
        == MedicationReconciliationEvent.ADMISSION.value
    )
    assert (
        record.event_identity
        == f"admission:{ctx['admission'].id}"
    )
    assert record.ward_transfer_id is None
    assert (
        record.record_status
        == ClinicalRecordStatus.DRAFT.value
    )
    assert record.record_version == 1


def test_transfer_reconciliation_requires_and_links_transfer(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
        event=MedicationReconciliationEvent.TRANSFER,
    )

    assert (
        record.event_identity
        == f"transfer:{ctx['transfer'].id}"
    )
    assert (
        record.ward_transfer_id
        == ctx["transfer"].id
    )

    with pytest.raises(
        ValidationError,
        match="ward_transfer_id",
    ):
        service.create_medication_reconciliation(
            clinic_id=clinic.id,
            admission_id=ctx["admission"].id,
            event_type=(
                MedicationReconciliationEvent.TRANSFER
            ),
            actor_staff_id=ctx["doctor"].id,
            ward_transfer_id=None,
            items=[_item()],
        )


def test_discharge_reconciliation_identity_is_admission_scoped(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
        event=MedicationReconciliationEvent.DISCHARGE,
    )

    assert (
        record.event_identity
        == f"discharge:{ctx['admission'].id}"
    )


def test_duplicate_event_is_rejected(
    ctx,
    clinic,
):
    _create(
        ctx,
        clinic,
    )

    with pytest.raises(
        ConflictError,
        match="already exists",
    ):
        _create(
            ctx,
            clinic,
        )


def test_draft_can_hold_verify_and_unknown_decisions(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
        items=[
            _item(
                decision=(
                    MedicationReconciliationDecision.VERIFY
                ),
            ),
            _item(
                medication_name="Unknown tablet",
                decision=(
                    MedicationReconciliationDecision.UNKNOWN
                ),
            ),
        ],
    )

    assert {
        item.decision
        for item in record.items
    } == {
        "verify",
        "unknown",
    }


def test_finalize_rejects_unresolved_decisions(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
        items=[
            _item(
                decision=(
                    MedicationReconciliationDecision.VERIFY
                ),
            ),
        ],
    )

    with pytest.raises(
        ConflictError,
        match="unresolved",
    ):
        service.finalize_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            expected_version=1,
        )


def test_finalize_requires_items_or_explicit_no_medications(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
        items=[],
        no_medications_reported=False,
    )

    with pytest.raises(
        ValidationError,
        match="at least one medication",
    ):
        service.finalize_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            expected_version=1,
        )


def test_explicit_no_medications_can_finalize(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
        items=[],
        no_medications_reported=True,
    )

    result = (
        service.finalize_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            expected_version=1,
        )
    )

    assert (
        result.record_status
        == ClinicalRecordStatus.FINAL.value
    )
    assert result.finalized_at is not None
    assert (
        result.finalized_by_staff_id
        == ctx["doctor"].id
    )


def test_resolved_reconciliation_finalizes_version_one(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
        items=[
            _item(
                decision=(
                    MedicationReconciliationDecision.CONTINUE
                ),
            ),
            _item(
                medication_name="Warfarin",
                decision=(
                    MedicationReconciliationDecision.HOLD
                ),
            ),
            _item(
                medication_name="Old antibiotic",
                decision=(
                    MedicationReconciliationDecision.STOP
                ),
            ),
            _item(
                medication_name="Old ACE inhibitor",
                decision=(
                    MedicationReconciliationDecision.REPLACE
                ),
                replacement_medication_name="New ACE inhibitor",
            ),
            _item(
                medication_name="New analgesic",
                decision=(
                    MedicationReconciliationDecision.NEW
                ),
            ),
        ],
    )

    result = (
        service.finalize_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            expected_version=1,
        )
    )

    assert (
        result.record_status
        == ClinicalRecordStatus.FINAL.value
    )
    assert result.record_version == 1


def test_finalized_record_cannot_be_silently_edited(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
    )

    service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=1,
    )

    with pytest.raises(
        ConflictError,
        match="immutable",
    ):
        service.update_medication_reconciliation_draft(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            no_medications_reported=False,
            notes="Silent overwrite",
            items=[_item()],
        )


def test_first_amendment_preserves_original_items(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
        items=[
            _item(
                medication_name="Original medicine",
            ),
        ],
    )

    service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=1,
    )

    original_name = (
        record.items[0].medication_name
    )

    document = (
        service
        .get_medication_reconciliation_effective_document(
            record
        )
    )
    document["items"][0][
        "medication_name"
    ] = "Corrected medicine"

    record, amendment = (
        service.amend_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            expected_version=1,
            reason="Correct medication name",
            document=document,
        )
    )

    assert (
        record.items[0].medication_name
        == original_name
    )
    assert (
        record.record_status
        == ClinicalRecordStatus.AMENDED.value
    )
    assert record.record_version == 2
    assert (
        amendment.source_record_type
        == ClinicalRecordType.MEDICATION_RECONCILIATION.value
    )
    assert amendment.new_version == 2


def test_second_amendment_links_previous(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
    )

    service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=1,
    )

    first_doc = (
        service
        .get_medication_reconciliation_effective_document(
            record
        )
    )
    first_doc["notes"] = "First amendment"

    record, first = (
        service.amend_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            expected_version=1,
            reason="First",
            document=first_doc,
        )
    )

    second_doc = (
        service
        .get_medication_reconciliation_effective_document(
            record
        )
    )
    second_doc["notes"] = "Second amendment"

    record, second = (
        service.amend_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            expected_version=2,
            reason="Second",
            document=second_doc,
        )
    )

    assert record.record_version == 3
    assert (
        second.previous_amendment_id
        == first.id
    )
    assert second.previous_version == 2
    assert second.new_version == 3


def test_stale_amendment_version_is_rejected(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
    )

    service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=1,
    )

    document = (
        service
        .get_medication_reconciliation_effective_document(
            record
        )
    )
    document["notes"] = "First amendment"

    service.amend_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=1,
        reason="First",
        document=document,
    )

    with pytest.raises(
        ConflictError,
        match="version mismatch",
    ):
        service.amend_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            expected_version=1,
            reason="Stale",
            document=document,
        )


def test_amendment_cannot_reintroduce_unresolved_decision(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
    )

    service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=1,
    )

    document = (
        service
        .get_medication_reconciliation_effective_document(
            record
        )
    )
    document["items"][0][
        "decision"
    ] = "unknown"

    with pytest.raises(
        ConflictError,
        match="unresolved",
    ):
        service.amend_medication_reconciliation(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            expected_version=1,
            reason="Invalid unresolved amendment",
            document=document,
        )


def test_nurse_cannot_write_reconciliation_service(
    ctx,
    clinic,
):
    with pytest.raises(
        ConflictError,
        match="doctor or pharmacist",
    ):
        _create(
            ctx,
            clinic,
            actor_key="nurse",
        )


def test_cross_tenant_admission_is_not_visible(
    ctx,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
):
    other = make_clinic()

    (
        _patient,
        _nurse,
        other_doctor,
        _ward,
        _bed,
        other_admission,
    ) = _setup_admission(
        clinic=other,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(
        NotFoundError,
        match="Admission not found",
    ):
        service.create_medication_reconciliation(
            clinic_id=clinic.id,
            admission_id=other_admission.id,
            event_type=(
                MedicationReconciliationEvent.ADMISSION
            ),
            actor_staff_id=ctx["doctor"].id,
            items=[_item()],
        )


def test_finalized_event_lookup_is_gate12_ready(
    ctx,
    clinic,
):
    record = _create(
        ctx,
        clinic,
        event=MedicationReconciliationEvent.DISCHARGE,
    )

    with pytest.raises(
        ConflictError,
        match="not finalized",
    ):
        (
            service
            .get_finalized_reconciliation_for_event(
                clinic_id=clinic.id,
                admission_id=ctx["admission"].id,
                event_type=(
                    MedicationReconciliationEvent.DISCHARGE
                ),
            )
        )

    service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=1,
    )

    result = (
        service
        .get_finalized_reconciliation_for_event(
            clinic_id=clinic.id,
            admission_id=ctx["admission"].id,
            event_type=(
                MedicationReconciliationEvent.DISCHARGE
            ),
        )
    )

    assert result.id == record.id


def test_amendment_schema_forbids_extra_fields():
    with pytest.raises(
        PydanticValidationError,
    ):
        (
            MedicationReconciliationAmendmentSchema
            .model_validate(
                {
                    "expected_version": 1,
                    "reason": "Correction",
                    "document": {
                        "no_medications_reported": True,
                        "notes": None,
                        "items": [],
                    },
                    "silent_overwrite": True,
                }
            )
        )


def test_nurse_cannot_create_reconciliation_route(
    ctx,
    clinic,
    client,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/medication-reconciliations",
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
        json={
            "admission_id": (
                ctx["admission"].id
            ),
            "event_type": "admission",
            "no_medications_reported": True,
            "items": [],
        },
    )

    assert response.status_code == 403


def test_doctor_can_create_reconciliation_route(
    ctx,
    clinic,
    client,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/medication-reconciliations",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "admission_id": (
                ctx["admission"].id
            ),
            "event_type": "admission",
            "no_medications_reported": True,
            "items": [],
        },
    )

    assert response.status_code == 201

    body = response.get_json()

    assert (
        body["reconciliation"]["event_type"]
        == "admission"
    )
    assert (
        body["reconciliation"]["record_status"]
        == "draft"
    )
