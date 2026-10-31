from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)
from app.core.enums.nursing_enums import (
    NursingNoteStatus,
    NursingNoteType,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.extensions import db
from app.modules.clinical_records.services import (
    clinical_record_service,
)
from app.modules.nursing.schemas.nursing_schema import (
    NursingNoteAmendmentSchema,
    NursingNoteDocumentSchema,
    NursingNoteResponseSchema,
)
from app.modules.nursing.services import nursing_service
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
    _setup_admission,
)


@pytest.fixture()
def nursing_ctx(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    audits = []

    def capture(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(
        nursing_service,
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

    other_nurse = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    db.session.commit()

    return {
        "patient": patient,
        "nurse": nurse,
        "other_nurse": other_nurse,
        "doctor": doctor,
        "ward": ward,
        "bed": bed,
        "admission": admission,
        "audits": audits,
    }


def _create_note(
    ctx,
    clinic,
    **overrides,
):
    payload = {
        "admission_id": ctx["admission"].id,
        "clinic_id": clinic.id,
        "actor_staff_id": ctx["nurse"].id,
        "note_text": "Patient resting comfortably",
        "note_type": NursingNoteType.PROGRESS,
        "observed_at": _db_now(),
    }

    payload.update(
        overrides
    )

    return nursing_service.create_nursing_note(
        **payload
    )


def _sign(
    note,
    ctx,
    clinic,
    **overrides,
):
    payload = {
        "note_id": note.id,
        "clinic_id": clinic.id,
        "actor_staff_id": ctx["nurse"].id,
    }

    payload.update(
        overrides
    )

    return nursing_service.sign_nursing_note(
        **payload
    )


def _document(
    note,
    **overrides,
):
    payload = (
        nursing_service
        .get_nursing_note_effective_document(
            note
        )
    )

    payload.update(
        overrides
    )

    return payload


def test_new_nursing_note_record_defaults_to_draft_version_one(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    assert note.status == NursingNoteStatus.DRAFT
    assert (
        note.record_status
        == ClinicalRecordStatus.DRAFT.value
    )
    assert note.record_version == 1
    assert note.finalized_by_staff_id is None
    assert note.signed_at is None
    assert note.latest_amendment_id is None


def test_existing_sign_action_is_gate11_finalization(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    result = _sign(
        note,
        nursing_ctx,
        clinic,
    )

    assert result.status == NursingNoteStatus.SIGNED
    assert (
        result.record_status
        == ClinicalRecordStatus.FINAL.value
    )
    assert result.record_version == 1
    assert (
        result.finalized_by_staff_id
        == nursing_ctx["nurse"].id
    )
    assert result.signed_at is not None
    assert result.latest_amendment_id is None


def test_signed_note_remains_immutable_through_existing_patch_service(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    with pytest.raises(
        ConflictError,
        match="immutable",
    ):
        nursing_service.update_nursing_note_draft(
            note.id,
            clinic_id=clinic.id,
            actor_staff_id=nursing_ctx["nurse"].id,
            note_text="Silent overwrite",
        )

    assert (
        note.note_text
        == "Patient resting comfortably"
    )


def test_only_note_author_can_sign(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    with pytest.raises(
        ConflictError,
        match="Only the note author can sign",
    ):
        _sign(
            note,
            nursing_ctx,
            clinic,
            actor_staff_id=(
                nursing_ctx["other_nurse"].id
            ),
        )


def test_first_amendment_preserves_original_row_and_creates_version_two(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
        note_text="Initial observation",
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    original_text = note.note_text
    original_type = note.note_type
    original_observed_at = note.observed_at

    corrected = _document(
        note,
        note_text="Corrected observation",
    )

    result, amendment = (
        nursing_service.amend_nursing_note(
            note.id,
            clinic_id=clinic.id,
            actor_staff_id=nursing_ctx["nurse"].id,
            expected_version=1,
            reason="Correct transcription",
            document=corrected,
        )
    )

    assert result.note_text == original_text
    assert result.note_type == original_type
    assert result.observed_at == original_observed_at

    assert (
        result.record_status
        == ClinicalRecordStatus.AMENDED.value
    )
    assert result.record_version == 2
    assert result.latest_amendment_id == amendment.id

    assert amendment.previous_version == 1
    assert amendment.new_version == 2
    assert amendment.previous_amendment_id is None
    assert (
        amendment.source_record_type
        == ClinicalRecordType.NURSING_NOTE.value
    )
    assert (
        amendment.new_content["note_text"]
        == "Corrected observation"
    )


def test_second_amendment_is_contiguous_and_links_previous(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    first_document = _document(
        note,
        note_text="First correction",
    )

    note, first = nursing_service.amend_nursing_note(
        note.id,
        clinic_id=clinic.id,
        actor_staff_id=nursing_ctx["nurse"].id,
        expected_version=1,
        reason="First correction",
        document=first_document,
    )

    second_document = _document(
        note,
        note_text="Second correction",
    )

    note, second = nursing_service.amend_nursing_note(
        note.id,
        clinic_id=clinic.id,
        actor_staff_id=nursing_ctx["nurse"].id,
        expected_version=2,
        reason="Second correction",
        document=second_document,
    )

    assert note.record_version == 3
    assert second.previous_version == 2
    assert second.new_version == 3
    assert second.previous_amendment_id == first.id


def test_stale_amendment_version_is_rejected(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    nursing_service.amend_nursing_note(
        note.id,
        clinic_id=clinic.id,
        actor_staff_id=nursing_ctx["nurse"].id,
        expected_version=1,
        reason="First correction",
        document=_document(
            note,
            note_text="First correction",
        ),
    )

    with pytest.raises(
        ConflictError,
        match="record version mismatch",
    ):
        nursing_service.amend_nursing_note(
            note.id,
            clinic_id=clinic.id,
            actor_staff_id=nursing_ctx["nurse"].id,
            expected_version=1,
            reason="Stale correction",
            document=_document(
                note,
                note_text="Stale correction",
            ),
        )


def test_draft_note_cannot_be_amended(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    with pytest.raises(
        ConflictError,
        match="Only final or amended",
    ):
        nursing_service.amend_nursing_note(
            note.id,
            clinic_id=clinic.id,
            actor_staff_id=nursing_ctx["nurse"].id,
            expected_version=1,
            reason="Invalid",
            document=_document(
                note,
                note_text="Invalid correction",
            ),
        )


def test_non_author_cannot_amend(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    with pytest.raises(
        ConflictError,
        match="Only the note author can amend",
    ):
        nursing_service.amend_nursing_note(
            note.id,
            clinic_id=clinic.id,
            actor_staff_id=nursing_ctx["other_nurse"].id,
            expected_version=1,
            reason="Unauthorized",
            document=_document(
                note,
                note_text="Unauthorized correction",
            ),
        )


def test_noop_amendment_is_rejected(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    with pytest.raises(
        ValidationError,
        match="must change",
    ):
        nursing_service.amend_nursing_note(
            note.id,
            clinic_id=clinic.id,
            actor_staff_id=nursing_ctx["nurse"].id,
            expected_version=1,
            reason="No-op",
            document=_document(
                note
            ),
        )


def test_effective_document_uses_latest_amendment(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
        note_text="Original text",
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    note, amendment = nursing_service.amend_nursing_note(
        note.id,
        clinic_id=clinic.id,
        actor_staff_id=nursing_ctx["nurse"].id,
        expected_version=1,
        reason="Correction",
        document=_document(
            note,
            note_text="Corrected text",
        ),
    )

    effective = (
        nursing_service
        .get_nursing_note_effective_document(
            note
        )
    )

    assert effective["note_text"] == "Corrected text"
    assert (
        effective["note_type"]
        == note.note_type.value
    )
    assert (
        amendment.new_content
        == effective
    )


def test_amendment_history_is_version_ordered(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    note, first = nursing_service.amend_nursing_note(
        note.id,
        clinic_id=clinic.id,
        actor_staff_id=nursing_ctx["nurse"].id,
        expected_version=1,
        reason="First",
        document=_document(
            note,
            note_text="First",
        ),
    )

    note, second = nursing_service.amend_nursing_note(
        note.id,
        clinic_id=clinic.id,
        actor_staff_id=nursing_ctx["nurse"].id,
        expected_version=2,
        reason="Second",
        document=_document(
            note,
            note_text="Second",
        ),
    )

    rows = (
        nursing_service
        .get_nursing_note_amendments(
            note.id,
            clinic_id=clinic.id,
        )
    )

    assert [
        row.id
        for row in rows
    ] == [
        first.id,
        second.id,
    ]


def test_document_schema_requires_complete_document():
    with pytest.raises(
        PydanticValidationError,
    ):
        NursingNoteDocumentSchema.model_validate(
            {
                "note_type": "progress",
                "note_text": "Missing observed_at",
            }
        )


def test_amendment_schema_forbids_extra_fields():
    valid = NursingNoteAmendmentSchema.model_validate(
        {
            "expected_version": 1,
            "reason": "Correction",
            "document": {
                "note_type": "progress",
                "note_text": "Corrected",
                "observed_at": _db_now(),
            },
        }
    )

    assert valid.expected_version == 1

    with pytest.raises(
        PydanticValidationError,
    ):
        NursingNoteAmendmentSchema.model_validate(
            {
                "expected_version": 1,
                "reason": "Correction",
                "document": {
                    "note_type": "progress",
                    "note_text": "Corrected",
                    "observed_at": _db_now(),
                },
                "silent_overwrite": True,
            }
        )


def test_response_schema_exposes_record_finalization_state(
    nursing_ctx,
    clinic,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    response = (
        NursingNoteResponseSchema
        .model_validate(
            note
        )
    )

    assert (
        response.record_status
        == ClinicalRecordStatus.FINAL
    )
    assert response.record_version == 1
    assert (
        response.finalized_by_staff_id
        == nursing_ctx["nurse"].id
    )


def test_route_amendment_returns_effective_corrected_note(
    nursing_ctx,
    clinic,
    client,
    auth_headers_for,
):
    note = _create_note(
        nursing_ctx,
        clinic,
        note_text="Original route text",
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    response = client.post(
        f"/api/v1/nursing/notes/{note.id}/amend",
        headers=auth_headers_for(
            nursing_ctx["nurse"].user,
            role=Role.NURSE,
        ),
        json={
            "expected_version": 1,
            "reason": "Route correction",
            "document": {
                "note_type": "progress",
                "note_text": "Corrected route text",
                "observed_at": (
                    note.observed_at.isoformat()
                ),
            },
        },
    )

    assert response.status_code == 200

    body = response.get_json()

    assert (
        body["note"]["note_text"]
        == "Corrected route text"
    )
    assert (
        body["note"]["record_status"]
        == "amended"
    )
    assert (
        body["amendment"]["new_version"]
        == 2
    )


def test_doctor_cannot_amend_nursing_note_route(
    nursing_ctx,
    clinic,
    client,
    auth_headers_for,
):
    note = _create_note(
        nursing_ctx,
        clinic,
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    response = client.post(
        f"/api/v1/nursing/notes/{note.id}/amend",
        headers=auth_headers_for(
            nursing_ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "expected_version": 1,
            "reason": "Unauthorized",
            "document": {
                "note_type": "progress",
                "note_text": "Unauthorized",
                "observed_at": (
                    note.observed_at.isoformat()
                ),
            },
        },
    )

    assert response.status_code == 403


def test_get_route_reads_latest_effective_amendment(
    nursing_ctx,
    clinic,
    client,
    auth_headers_for,
):
    note = _create_note(
        nursing_ctx,
        clinic,
        note_text="Original GET text",
    )

    _sign(
        note,
        nursing_ctx,
        clinic,
    )

    nursing_service.amend_nursing_note(
        note.id,
        clinic_id=clinic.id,
        actor_staff_id=nursing_ctx["nurse"].id,
        expected_version=1,
        reason="Correction",
        document=_document(
            note,
            note_text="Effective GET text",
        ),
    )

    response = client.get(
        f"/api/v1/nursing/notes/{note.id}",
        headers=auth_headers_for(
            nursing_ctx["nurse"].user,
            role=Role.NURSE,
        ),
    )

    assert response.status_code == 200

    assert (
        response.get_json()["note_text"]
        == "Effective GET text"
    )
