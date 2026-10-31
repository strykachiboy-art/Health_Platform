from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)
from app.core.enums.consultation_enums import (
    ConsultationStatus,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.modules.clinical_records.models.clinical_record_amendment_model import (
    ClinicalRecordAmendment,
)
from app.modules.clinical_records.services import (
    clinical_record_service,
)
from app.extensions import db
from app.modules.consultation.routes import consultation_route
from app.modules.consultation.schemas.consultation_schema import (
    ConsultationAmendmentSchema,
    ConsultationDocumentSchema,
    ConsultationFinalizeSchema,
)
from app.modules.consultation.services import consultation_service


def _document(
    consultation,
    **overrides,
):
    payload = {
        "icd10_code": consultation.icd10_code,
        "chief_complaint": consultation.chief_complaint,
        "symptoms": consultation.symptoms,
        "diagnosis": consultation.diagnosis,
        "treatment_plan": consultation.treatment_plan,
        "notes": consultation.notes,
        "voice_note_url": consultation.voice_note_url,
        "transcribed_text": consultation.transcribed_text,
    }

    payload.update(
        overrides
    )

    return payload


@pytest.fixture()
def audit_events(
    monkeypatch,
):
    events = []

    def capture(**kwargs):
        events.append(
            kwargs
        )

    monkeypatch.setattr(
        consultation_service,
        "create_audit_log",
        capture,
    )

    monkeypatch.setattr(
        clinical_record_service,
        "create_audit_log",
        capture,
    )

    return events


def _completed_consultation(
    *,
    clinic,
    patient,
    staff,
    make_consultation,
):
    return make_consultation(
        clinic,
        patient,
        staff,
        status=ConsultationStatus.COMPLETED,
        icd10_code="J20.9",
        chief_complaint="Cough",
        symptoms="Cough and fever",
        diagnosis="Acute bronchitis",
        treatment_plan="Hydration and rest",
        notes="Return if symptoms worsen",
    )


def test_new_consultation_record_defaults_are_draft_version_one(
    clinic,
    patient,
    staff,
    make_consultation,
):
    consultation = make_consultation(
        clinic,
        patient,
        staff,
    )

    assert (
        consultation.record_status
        == ClinicalRecordStatus.DRAFT.value
    )
    assert consultation.record_version == 1
    assert consultation.finalized_by_staff_id is None
    assert consultation.finalized_at is None
    assert consultation.latest_amendment_id is None


def test_completed_unsigned_consultation_remains_editable(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    result = (
        consultation_service.update_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            diagnosis="Updated before signature",
        )
    )

    assert result.diagnosis == "Updated before signature"
    assert (
        result.record_status
        == ClinicalRecordStatus.DRAFT.value
    )


def test_only_completed_consultation_can_be_finalized(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = make_consultation(
        clinic,
        patient,
        staff,
        status=ConsultationStatus.IN_PROGRESS,
    )

    with pytest.raises(
        ConflictError,
        match="must be completed before finalization",
    ):
        consultation_service.finalize_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            finalized_by_staff_id=staff.id,
            expected_version=1,
        )


def test_assigned_consultation_author_is_required_to_finalize(
    clinic,
    patient,
    staff,
    make_staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    other_staff = make_staff(
        clinic,
    )

    with pytest.raises(
        ConflictError,
        match="assigned consultation author",
    ):
        consultation_service.finalize_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            finalized_by_staff_id=other_staff.id,
            expected_version=1,
        )


def test_finalize_signs_version_one_without_mutating_clinical_content(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    original = _document(
        consultation
    )

    before = datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )

    result = (
        consultation_service.finalize_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            finalized_by_staff_id=staff.id,
            expected_version=1,
        )
    )

    assert (
        result.record_status
        == ClinicalRecordStatus.FINAL.value
    )
    assert result.record_version == 1
    assert result.finalized_by_staff_id == staff.id
    assert result.finalized_at is not None
    assert result.latest_amendment_id is None
    assert _document(result) == original

    finalized_at = result.finalized_at

    if finalized_at.tzinfo is not None:
        finalized_at = finalized_at.replace(
            tzinfo=None
        )

    assert finalized_at >= before

    assert any(
        event.get("description")
        == "Consultation clinical record finalized"
        for event in audit_events
    )


def test_finalize_rejects_stale_expected_version(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    with pytest.raises(
        ConflictError,
        match="record version mismatch",
    ):
        consultation_service.finalize_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            finalized_by_staff_id=staff.id,
            expected_version=2,
        )


def test_finalized_consultation_cannot_be_finalized_again(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    consultation_service.finalize_consultation_note(
        consultation_id=consultation.id,
        clinic_id=clinic.id,
        finalized_by_staff_id=staff.id,
        expected_version=1,
    )

    with pytest.raises(
        ConflictError,
        match="Only draft clinical records",
    ):
        consultation_service.finalize_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            finalized_by_staff_id=staff.id,
            expected_version=1,
        )


def test_patch_is_blocked_after_finalization(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    consultation_service.finalize_consultation_note(
        consultation_id=consultation.id,
        clinic_id=clinic.id,
        finalized_by_staff_id=staff.id,
        expected_version=1,
    )

    with pytest.raises(
        ConflictError,
        match="immutable",
    ):
        consultation_service.update_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            diagnosis="Silent overwrite attempt",
        )

    assert consultation.diagnosis == "Acute bronchitis"


def test_first_amendment_preserves_original_row_and_creates_version_two(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    consultation_service.finalize_consultation_note(
        consultation_id=consultation.id,
        clinic_id=clinic.id,
        finalized_by_staff_id=staff.id,
        expected_version=1,
    )

    corrected = _document(
        consultation,
        diagnosis="Viral bronchitis",
        notes="Diagnosis corrected after review",
    )

    result, amendment = (
        consultation_service.amend_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            amended_by_staff_id=staff.id,
            expected_version=1,
            reason="Correct diagnostic wording",
            document=corrected,
        )
    )

    assert result.diagnosis == "Acute bronchitis"
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
        == ClinicalRecordType.CONSULTATION_NOTE.value
    )
    assert amendment.new_content["diagnosis"] == "Viral bronchitis"

    effective = (
        consultation_service
        .get_consultation_effective_document(
            result
        )
    )

    assert effective["diagnosis"] == "Viral bronchitis"
    assert (
        effective["notes"]
        == "Diagnosis corrected after review"
    )


def test_second_amendment_is_contiguous_and_links_previous(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    consultation_service.finalize_consultation_note(
        consultation_id=consultation.id,
        clinic_id=clinic.id,
        finalized_by_staff_id=staff.id,
        expected_version=1,
    )

    first_document = _document(
        consultation,
        diagnosis="Viral bronchitis",
    )

    consultation, first = (
        consultation_service.amend_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            amended_by_staff_id=staff.id,
            expected_version=1,
            reason="First correction",
            document=first_document,
        )
    )

    second_document = (
        consultation_service
        .get_consultation_effective_document(
            consultation
        )
    )

    second_document["notes"] = (
        "Second amendment preserves first correction"
    )

    consultation, second = (
        consultation_service.amend_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            amended_by_staff_id=staff.id,
            expected_version=2,
            reason="Second correction",
            document=second_document,
        )
    )

    assert consultation.record_version == 3
    assert second.previous_version == 2
    assert second.new_version == 3
    assert second.previous_amendment_id == first.id
    assert consultation.diagnosis == "Acute bronchitis"

    effective = (
        consultation_service
        .get_consultation_effective_document(
            consultation
        )
    )

    assert effective["diagnosis"] == "Viral bronchitis"
    assert (
        effective["notes"]
        == "Second amendment preserves first correction"
    )


def test_amendment_rejects_stale_expected_version(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    consultation_service.finalize_consultation_note(
        consultation_id=consultation.id,
        clinic_id=clinic.id,
        finalized_by_staff_id=staff.id,
        expected_version=1,
    )

    corrected = _document(
        consultation,
        diagnosis="Viral bronchitis",
    )

    consultation_service.amend_consultation_note(
        consultation_id=consultation.id,
        clinic_id=clinic.id,
        amended_by_staff_id=staff.id,
        expected_version=1,
        reason="First correction",
        document=corrected,
    )

    with pytest.raises(
        ConflictError,
        match="record version mismatch",
    ):
        consultation_service.amend_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            amended_by_staff_id=staff.id,
            expected_version=1,
            reason="Stale correction",
            document=corrected,
        )


def test_draft_record_cannot_be_amended(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    with pytest.raises(
        ConflictError,
        match="Only final or amended clinical records",
    ):
        consultation_service.amend_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            amended_by_staff_id=staff.id,
            expected_version=1,
            reason="Invalid correction",
            document=_document(
                consultation,
                diagnosis="Invalid",
            ),
        )


def test_non_author_cannot_amend_consultation(
    clinic,
    patient,
    staff,
    make_staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    consultation_service.finalize_consultation_note(
        consultation_id=consultation.id,
        clinic_id=clinic.id,
        finalized_by_staff_id=staff.id,
        expected_version=1,
    )

    other_staff = make_staff(
        clinic,
    )

    with pytest.raises(
        ConflictError,
        match="assigned consultation author",
    ):
        consultation_service.amend_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            amended_by_staff_id=other_staff.id,
            expected_version=1,
            reason="Unauthorized correction",
            document=_document(
                consultation,
                diagnosis="Unauthorized",
            ),
        )


def test_noop_amendment_is_rejected(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    consultation_service.finalize_consultation_note(
        consultation_id=consultation.id,
        clinic_id=clinic.id,
        finalized_by_staff_id=staff.id,
        expected_version=1,
    )

    with pytest.raises(
        ValidationError,
        match="must change",
    ):
        consultation_service.amend_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            amended_by_staff_id=staff.id,
            expected_version=1,
            reason="No actual correction",
            document=_document(
                consultation
            ),
        )


def test_amendment_history_is_version_ordered(
    clinic,
    patient,
    staff,
    make_consultation,
    audit_events,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    consultation_service.finalize_consultation_note(
        consultation_id=consultation.id,
        clinic_id=clinic.id,
        finalized_by_staff_id=staff.id,
        expected_version=1,
    )

    first_document = _document(
        consultation,
        diagnosis="First correction",
    )

    consultation, first = (
        consultation_service.amend_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            amended_by_staff_id=staff.id,
            expected_version=1,
            reason="First correction",
            document=first_document,
        )
    )

    second_document = (
        consultation_service
        .get_consultation_effective_document(
            consultation
        )
    )

    second_document["diagnosis"] = "Second correction"

    consultation, second = (
        consultation_service.amend_consultation_note(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
            amended_by_staff_id=staff.id,
            expected_version=2,
            reason="Second correction",
            document=second_document,
        )
    )

    rows = (
        consultation_service
        .get_consultation_amendments(
            consultation_id=consultation.id,
            clinic_id=clinic.id,
        )
    )

    assert [row.id for row in rows] == [
        first.id,
        second.id,
    ]
    assert [row.new_version for row in rows] == [
        2,
        3,
    ]


def test_document_schema_requires_complete_document():
    payload = {
        "icd10_code": "J20.9",
        "chief_complaint": "Cough",
        "symptoms": "Cough",
        "diagnosis": "Bronchitis",
        "treatment_plan": "Rest",
        "notes": "Review",
        "voice_note_url": None,
    }

    with pytest.raises(
        PydanticValidationError,
    ):
        ConsultationDocumentSchema.model_validate(
            payload
        )


def test_amendment_schema_forbids_partial_or_extra_payload():
    document = {
        "icd10_code": "J20.9",
        "chief_complaint": "Cough",
        "symptoms": "Cough",
        "diagnosis": "Bronchitis",
        "treatment_plan": "Rest",
        "notes": "Review",
        "voice_note_url": None,
        "transcribed_text": None,
    }

    valid = ConsultationAmendmentSchema.model_validate(
        {
            "expected_version": 1,
            "reason": "Correction",
            "document": document,
        }
    )

    assert valid.expected_version == 1

    with pytest.raises(
        PydanticValidationError,
    ):
        ConsultationAmendmentSchema.model_validate(
            {
                "expected_version": 1,
                "reason": "Correction",
                "document": document,
                "silent_overwrite": True,
            }
        )


def test_finalize_schema_requires_positive_expected_version():
    with pytest.raises(
        PydanticValidationError,
    ):
        ConsultationFinalizeSchema.model_validate(
            {
                "expected_version": 0,
            }
        )


def test_finalize_route_is_doctor_only_and_forwards_author(
    app,
    clinic,
    patient,
    staff,
    make_consultation,
    auth_headers_for,
    monkeypatch,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    consultation.record_status = (
        ClinicalRecordStatus.FINAL.value
    )
    consultation.record_version = 1
    consultation.finalized_by_staff_id = staff.id
    consultation.finalized_at = datetime.now(
        timezone.utc
    )
    consultation.latest_amendment_id = None

    called = {}

    def fake_finalize(**kwargs):
        called.update(
            kwargs
        )
        return consultation

    monkeypatch.setattr(
        consultation_route,
        "finalize_consultation_note",
        fake_finalize,
    )

    doctor_headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    response = app.test_client().post(
        (
            f"/api/v1/consultations/"
            f"{consultation.id}/finalize"
        ),
        json={
            "expected_version": 1,
        },
        headers=doctor_headers,
    )

    assert response.status_code == 200
    assert called["finalized_by_staff_id"] == staff.id
    assert called["clinic_id"] == clinic.id
    assert called["expected_version"] == 1

    body = response.get_json()

    assert (
        body["data"]["record_status"]
        == ClinicalRecordStatus.FINAL.value
    )
    assert body["data"]["record_version"] == 1


def test_nurse_cannot_finalize_consultation_route(
    app,
    clinic,
    patient,
    staff,
    make_consultation,
    auth_headers_for,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    nurse_headers = auth_headers_for(
        staff.user,
        role=Role.NURSE,
    )

    response = app.test_client().post(
        (
            f"/api/v1/consultations/"
            f"{consultation.id}/finalize"
        ),
        json={
            "expected_version": 1,
        },
        headers=nurse_headers,
    )

    assert response.status_code == 403


def test_amend_route_forwards_full_document_and_returns_amendment(
    app,
    clinic,
    patient,
    staff,
    make_consultation,
    auth_headers_for,
    monkeypatch,
):
    consultation = _completed_consultation(
        clinic=clinic,
        patient=patient,
        staff=staff,
        make_consultation=make_consultation,
    )

    amendment = ClinicalRecordAmendment(
        clinic_id=clinic.id,
        patient_id=patient.id,
        source_record_type="consultation_note",
        source_record_id=consultation.id,
        previous_version=1,
        new_version=2,
        previous_amendment_id=None,
        amended_by_staff_id=staff.id,
        reason="Correction",
        new_content=_document(
            consultation,
            diagnosis="Corrected diagnosis",
        ),
        new_content_sha256="a" * 64,
        amended_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )

    db.session.add(amendment)
    db.session.flush()

    consultation.record_status = (
        ClinicalRecordStatus.AMENDED.value
    )
    consultation.record_version = 2
    consultation.finalized_by_staff_id = staff.id
    consultation.finalized_at = datetime.now(
        timezone.utc
    )
    consultation.latest_amendment_id = amendment.id
    consultation.latest_amendment = amendment

    called = {}

    def fake_amend(**kwargs):
        called.update(
            kwargs
        )
        return consultation, amendment

    monkeypatch.setattr(
        consultation_route,
        "amend_consultation_note",
        fake_amend,
    )

    headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )

    document = _document(
        consultation,
        diagnosis="Corrected diagnosis",
    )

    response = app.test_client().post(
        (
            f"/api/v1/consultations/"
            f"{consultation.id}/amend"
        ),
        json={
            "expected_version": 1,
            "reason": "Correction",
            "document": document,
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert called["amended_by_staff_id"] == staff.id
    assert called["expected_version"] == 1
    assert (
        called["document"]["diagnosis"]
        == "Corrected diagnosis"
    )

    body = response.get_json()

    assert (
        body["data"]["consultation"]["diagnosis"]
        == "Corrected diagnosis"
    )
    assert (
        body["data"]["amendment"]["new_version"]
        == 2
    )
