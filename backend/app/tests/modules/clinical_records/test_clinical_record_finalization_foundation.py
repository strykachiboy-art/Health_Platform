from __future__ import annotations

import hashlib
import json

import pytest

from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.clinical_records.schemas.clinical_record_schema import (
    ClinicalRecordAmendmentCreateSchema,
    ClinicalRecordAmendmentResponseSchema,
)
from app.modules.clinical_records.services import clinical_record_service
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _setup_admission,
)


def _context(
    *,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    audits = []

    monkeypatch.setattr(
        clinical_record_service,
        "create_audit_log",
        lambda **kwargs: audits.append(
            kwargs
        ),
    )

    (
        patient,
        nurse,
        doctor,
        _ward,
        _bed,
        _admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    return {
        "patient": patient,
        "nurse": nurse,
        "doctor": doctor,
        "audits": audits,
    }


def _append(
    ctx,
    clinic,
    **overrides,
):
    payload = {
        "clinic_id": clinic.id,
        "patient_id": ctx["patient"].id,
        "source_record_type": (
            ClinicalRecordType.CONSULTATION_NOTE
        ),
        "source_record_id": 101,
        "current_status": ClinicalRecordStatus.FINAL,
        "previous_version": 1,
        "amended_by_staff_id": ctx["doctor"].id,
        "reason": "Correct transcription error",
        "new_content": {
            "assessment": "Corrected assessment",
            "plan": "Continue current treatment",
        },
    }

    payload.update(
        overrides
    )

    return (
        clinical_record_service
        .append_clinical_record_amendment(
            **payload
        )
    )


def test_record_status_taxonomy_is_exact():
    assert {
        item.value
        for item in ClinicalRecordStatus
    } == {
        "draft",
        "final",
        "amended",
    }


def test_record_type_taxonomy_reserves_all_roadmap_records():
    assert {
        item.value
        for item in ClinicalRecordType
    } == {
        "consultation_note",
        "nursing_note",
        "medication_administration",
        "medication_reconciliation",
        "discharge_summary",
    }


def test_draft_can_transition_to_final():
    assert (
        clinical_record_service
        .validate_finalization_transition(
            ClinicalRecordStatus.DRAFT
        )
        == ClinicalRecordStatus.FINAL
    )


@pytest.mark.parametrize(
    "status",
    [
        ClinicalRecordStatus.FINAL,
        ClinicalRecordStatus.AMENDED,
    ],
)
def test_non_draft_cannot_be_finalized_again(
    status,
):
    with pytest.raises(
        ConflictError,
        match="Only draft",
    ):
        (
            clinical_record_service
            .validate_finalization_transition(
                status
            )
        )


def test_draft_cannot_be_amended():
    with pytest.raises(
        ConflictError,
        match="Only final or amended",
    ):
        (
            clinical_record_service
            .validate_amendment_transition(
                ClinicalRecordStatus.DRAFT
            )
        )


@pytest.mark.parametrize(
    "status",
    [
        ClinicalRecordStatus.FINAL,
        ClinicalRecordStatus.AMENDED,
    ],
)
def test_final_and_amended_records_can_receive_amendments(
    status,
):
    assert (
        clinical_record_service
        .validate_amendment_transition(
            status
        )
        == ClinicalRecordStatus.AMENDED
    )


def test_first_amendment_creates_version_two_and_audit(
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

    amendment = _append(
        ctx,
        clinic,
    )

    assert amendment.id is not None
    assert amendment.previous_version == 1
    assert amendment.new_version == 2
    assert amendment.previous_amendment_id is None
    assert (
        amendment.source_record_type
        == "consultation_note"
    )
    assert len(amendment.new_content_sha256) == 64

    expected_hash = hashlib.sha256(
        json.dumps(
            amendment.new_content,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    assert amendment.new_content_sha256 == expected_hash

    assert len(ctx["audits"]) == 1
    assert (
        ctx["audits"][0]["entity_type"]
        == "ClinicalRecordAmendment"
    )
    assert (
        ctx["audits"][0]["user_id"]
        == ctx["doctor"].user_id
    )


def test_second_amendment_links_previous_and_advances_version(
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

    first = _append(
        ctx,
        clinic,
    )

    second = _append(
        ctx,
        clinic,
        current_status=ClinicalRecordStatus.AMENDED,
        previous_version=2,
        reason="Correct medication spelling",
        new_content={
            "assessment": "Second correction",
        },
    )

    assert second.previous_version == 2
    assert second.new_version == 3
    assert second.previous_amendment_id == first.id


def test_stale_amendment_version_is_rejected(
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

    _append(
        ctx,
        clinic,
    )

    with pytest.raises(
        ConflictError,
        match="version chain is stale",
    ):
        _append(
            ctx,
            clinic,
            current_status=ClinicalRecordStatus.AMENDED,
            previous_version=1,
            new_content={
                "assessment": "Stale edit",
            },
        )


def test_first_amendment_must_reference_version_one(
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

    with pytest.raises(
        ConflictError,
        match="version 1",
    ):
        _append(
            ctx,
            clinic,
            previous_version=4,
        )


@pytest.mark.parametrize(
    "content",
    [
        {},
        [],
        "not-an-object",
    ],
)
def test_new_content_must_be_nonempty_object(
    content,
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

    with pytest.raises(
        ValidationError,
        match="new_content",
    ):
        _append(
            ctx,
            clinic,
            new_content=content,
        )


def test_reason_is_required(
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

    with pytest.raises(
        ValidationError,
        match="reason is required",
    ):
        _append(
            ctx,
            clinic,
            reason="   ",
        )


def test_patient_tenant_isolation_is_enforced(
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
        name="Gate 11 Foreign Clinic",
    )

    with pytest.raises(
        NotFoundError,
    ):
        (
            clinical_record_service
            .append_clinical_record_amendment(
                clinic_id=other.id,
                patient_id=ctx["patient"].id,
                source_record_type=(
                    ClinicalRecordType.CONSULTATION_NOTE
                ),
                source_record_id=101,
                current_status=(
                    ClinicalRecordStatus.FINAL
                ),
                previous_version=1,
                amended_by_staff_id=ctx["doctor"].id,
                reason="Should fail",
                new_content={
                    "assessment": "Foreign tenant",
                },
            )
        )


def test_amendments_are_returned_in_version_order(
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

    first = _append(
        ctx,
        clinic,
    )

    second = _append(
        ctx,
        clinic,
        current_status=ClinicalRecordStatus.AMENDED,
        previous_version=2,
        new_content={
            "assessment": "Version three",
        },
    )

    rows = (
        clinical_record_service
        .get_clinical_record_amendments(
            clinic_id=clinic.id,
            source_record_type=(
                ClinicalRecordType.CONSULTATION_NOTE
            ),
            source_record_id=101,
        )
    )

    assert [
        row.id
        for row in rows
    ] == [
        first.id,
        second.id,
    ]

    assert [
        row.new_version
        for row in rows
    ] == [
        2,
        3,
    ]


def test_amendment_schema_forbids_extra_fields():
    with pytest.raises(
        ValueError,
    ):
        (
            ClinicalRecordAmendmentCreateSchema
            .model_validate(
                {
                    "reason": "Correction",
                    "new_content": {
                        "note": "Corrected",
                    },
                    "silent_overwrite": True,
                }
            )
        )


def test_amendment_response_schema_reads_model(
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

    amendment = _append(
        ctx,
        clinic,
    )

    response = (
        ClinicalRecordAmendmentResponseSchema
        .model_validate(
            amendment
        )
    )

    assert (
        response.source_record_type
        == ClinicalRecordType.CONSULTATION_NOTE
    )
    assert response.new_version == 2
    assert response.previous_amendment_id is None


def test_nursing_note_record_type_uses_same_ledger(
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

    amendment = _append(
        ctx,
        clinic,
        source_record_type=(
            ClinicalRecordType.NURSING_NOTE
        ),
        source_record_id=222,
        amended_by_staff_id=ctx["nurse"].id,
    )

    assert (
        amendment.source_record_type
        == "nursing_note"
    )
    assert (
        amendment.amended_by_staff_id
        == ctx["nurse"].id
    )
