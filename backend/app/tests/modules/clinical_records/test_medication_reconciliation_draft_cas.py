"""P23G.S03.B: Atomic optimistic guard for a complete medication draft update.

Only a fresh, clinic-scoped, author-controlled draft may be replaced. Uses
existing SQLite in-memory pytest fixtures plus isolated Redis JWT checking.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.medication_reconciliation.schemas.medication_reconciliation_schema import (
    MedicationReconciliationUpdateSchema,
)
from app.modules.medication_reconciliation.services import (
    medication_reconciliation_service as service,
)
from app.tests.modules.clinical_records.test_medication_reconciliation_finalization import (
    _create,
    _item,
    ctx,
)

BASE = "/api/v1/medication-reconciliations"


def _update_body(timestamp, *, notes="Updated history"):
    return {
        "expected_updated_at": timestamp,
        "no_medications_reported": False,
        "notes": notes,
        "items": [
            {
                **_item(),
                "decision": "continue",
                "medication_name": "Metformin",
            }
        ],
    }


def _doc_timestamp(record):
    stamp = record.updated_at
    assert isinstance(stamp, datetime)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.isoformat()


def _headers(auth_headers_for, actor):
    return auth_headers_for(actor.user, role=actor.user.role)


def test_get_exposes_aware_server_timestamp_as_draft_precondition(
    ctx, clinic, client, auth_headers_for
):
    record = _create(ctx, clinic)
    response = client.get(
        f"{BASE}/{record.id}",
        headers=_headers(auth_headers_for, ctx["doctor"]),
    )
    assert response.status_code == 200
    latest = response.get_json()["reconciliation"]
    stamp = datetime.fromisoformat(latest["updated_at"].replace("Z", "+00:00"))
    assert stamp.tzinfo is not None
    assert stamp.utcoffset() is not None


def test_patch_requires_timestamp_for_clinical_draft(ctx, clinic, client, auth_headers_for):
    record = _create(ctx, clinic)
    body = _update_body(_doc_timestamp(record))
    del body["expected_updated_at"]
    reply = client.patch(f"{BASE}/{record.id}", headers=_headers(auth_headers_for, ctx["doctor"]), json=body)
    assert reply.status_code == 422
    db.session.refresh(record)
    assert record.notes == "Medication history reviewed"


@pytest.mark.parametrize("bad", ["2026-11-10T10:00:00", "not-a-date", 1731220000, None])
def test_patch_rejects_invalid_or_naive_timestamp(
    ctx, clinic, client, auth_headers_for, bad
):
    record = _create(ctx, clinic)
    reply = client.patch(
        f"{BASE}/{record.id}",
        headers=_headers(auth_headers_for, ctx["doctor"]),
        json=_update_body(bad),
    )
    assert reply.status_code == 422


def test_patch_stale_fails_conflict_and_preserves_accepted_document(
    ctx, clinic, client, auth_headers_for
):
    record = _create(ctx, clinic)
    original = _doc_timestamp(record)
    headers = _headers(auth_headers_for, ctx["doctor"])
    first = client.patch(
        f"{BASE}/{record.id}", headers=headers,
        json=_update_body(original, notes="First clinician review"),
    )
    assert first.status_code == 200, first.get_json()
    updated = first.get_json()["reconciliation"]
    assert updated["notes"] == "First clinician review"
    assert updated["record_status"] == "draft"
    assert updated["record_version"] == 1
    assert datetime.fromisoformat(updated["updated_at"].replace("Z", "+00:00")) > datetime.fromisoformat(original)

    stale = client.patch(
        f"{BASE}/{record.id}", headers=headers,
        json=_update_body(original, notes="Stale overwrite forbidden"),
    )
    assert stale.status_code == 409, stale.get_json()
    readback = client.get(f"{BASE}/{record.id}", headers=headers)
    assert readback.status_code == 200
    latest = readback.get_json()["reconciliation"]
    assert latest["notes"] == "First clinician review"
    assert latest["updated_at"] == updated["updated_at"]
    assert latest["items"] == updated["items"]


def test_repeated_fresh_draft_saves_advance_timestamp(ctx, clinic):
    record = _create(ctx, clinic)
    original = record.updated_at
    if original.tzinfo is None or original.utcoffset() is None:
        original = original.replace(tzinfo=timezone.utc)
    first = service.update_medication_reconciliation_draft(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        no_medications_reported=False,
        notes="Review 1",
        items=[_item()],
        expected_updated_at=original,
    )
    stamp1 = first.updated_at
    if stamp1.tzinfo is None or stamp1.utcoffset() is None:
        stamp1 = stamp1.replace(tzinfo=timezone.utc)
    second = service.update_medication_reconciliation_draft(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        no_medications_reported=False,
        notes="Review 2",
        items=[_item()],
        expected_updated_at=stamp1,
    )
    stamp2 = second.updated_at
    if stamp2.tzinfo is None or stamp2.utcoffset() is None:
        stamp2 = stamp2.replace(tzinfo=timezone.utc)
    assert stamp2 > stamp1
    assert second.record_version == 1
    assert second.notes == "Review 2"


def test_service_without_timestamp_fails_closed_for_draft(ctx, clinic):
    record = _create(ctx, clinic)
    with pytest.raises(ValidationError, match="expected_updated_at"):
        service.update_medication_reconciliation_draft(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            no_medications_reported=False,
            notes="Not allowed",
            items=[_item()],
        )


def test_author_only_even_when_precondition_is_current(ctx, clinic):
    record = _create(ctx, clinic)
    with pytest.raises(ConflictError, match="Only the reconciliation author"):
        service.update_medication_reconciliation_draft(
            record.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["pharmacist"].id,
            no_medications_reported=False,
            notes="Wrong author",
            items=[_item()],
            expected_updated_at=record.updated_at,
        )


def test_nurse_cannot_patch_even_with_timestamp(ctx, clinic, client, auth_headers_for):
    record = _create(ctx, clinic)
    reply = client.patch(
        f"{BASE}/{record.id}",
        headers=_headers(auth_headers_for, ctx["nurse"]),
        json=_update_body(_doc_timestamp(record)),
    )
    assert reply.status_code == 403


def test_finalized_draft_cannot_be_modified(ctx, clinic, client, auth_headers_for):
    record = _create(ctx, clinic)
    timestamp = _doc_timestamp(record)
    service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=1,
    )
    reply = client.patch(
        f"{BASE}/{record.id}",
        headers=_headers(auth_headers_for, ctx["doctor"]),
        json=_update_body(timestamp),
    )
    assert reply.status_code == 409


def test_invalid_extra_field_fails_closed_before_mutation(ctx, clinic, client, auth_headers_for):
    record = _create(ctx, clinic)
    body = _update_body(_doc_timestamp(record))
    body["clinic_id"] = clinic.id
    reply = client.patch(
        f"{BASE}/{record.id}",
        headers=_headers(auth_headers_for, ctx["doctor"]),
        json=body,
    )
    assert reply.status_code == 422


def test_direct_service_tenant_guard_precedes_timestamp(ctx, clinic, make_clinic):
    record = _create(ctx, clinic)
    other = make_clinic()
    with pytest.raises(NotFoundError, match="Medication reconciliation not found"):
        service.update_medication_reconciliation_draft(
            record.id,
            clinic_id=other.id,
            actor_staff_id=ctx["doctor"].id,
            no_medications_reported=False,
            notes="Cross clinic",
            items=[_item()],
            expected_updated_at=record.updated_at,
        )


def test_request_schema_requires_aware_time_and_forbids_unknown_fields():
    good = _update_body(datetime.now(timezone.utc).isoformat())
    assert MedicationReconciliationUpdateSchema.model_validate(good).expected_updated_at.tzinfo is not None
    with pytest.raises(PydanticValidationError):
        MedicationReconciliationUpdateSchema.model_validate({**good, "expected_updated_at": "2026-11-10T10:00:00"})
    with pytest.raises(PydanticValidationError):
        MedicationReconciliationUpdateSchema.model_validate({**good, "extra_client_override": True})
