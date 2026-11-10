"""P23G.S04 route-level finalization and amendment contract proof.

Uses the existing disposable test database, auth fixtures, and medrec fixture.
No actual patient records or production database access.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.core.enums.role_enums import Role
from app.modules.medication_reconciliation.schemas.medication_reconciliation_schema import (
    MedicationReconciliationAmendmentSchema,
    MedicationReconciliationFinalizeSchema,
)
from app.tests.modules.clinical_records.test_medication_reconciliation_finalization import (
    _create,
    _item,
    ctx,
)

BASE = "/api/v1/medication-reconciliations"


def _headers(auth_headers_for, staff, *, role=Role.DOCTOR, key=None):
    values = dict(auth_headers_for(staff.user, role=role))
    if key is not None:
        values["Idempotency-Key"] = key
    return values


def _get(client, rid, headers):
    response = client.get(f"{BASE}/{rid}", headers=headers)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["reconciliation"]


def _finalize(client, rid, headers, readback):
    return client.post(
        f"{BASE}/{rid}/finalize",
        headers=headers,
        json={
            "expected_version": readback["record_version"],
            "expected_updated_at": readback["updated_at"],
        },
    )


def test_s04_finalization_schema_requires_aware_string_timestamp():
    from pydantic import ValidationError as SchemaValidationError

    for timestamp in (None, "2026-11-10T10:00:00", "not-a-date", 123, ""):
        with pytest.raises(SchemaValidationError):
            MedicationReconciliationFinalizeSchema.model_validate({
                "expected_version": 1,
                "expected_updated_at": timestamp,
            })
    parsed = MedicationReconciliationFinalizeSchema.model_validate({
        "expected_version": 1,
        "expected_updated_at": "2026-11-10T10:00:00Z",
    })
    assert isinstance(parsed.expected_updated_at, datetime)
    assert parsed.expected_updated_at.utcoffset() is not None


def test_s04_amendment_document_is_not_draft_cas_schema():
    valid = {
        "expected_version": 1,
        "reason": "Clinically justified correction",
        "document": {
            "no_medications_reported": True,
            "notes": "Correction",
            "items": [],
        },
    }
    parsed = MedicationReconciliationAmendmentSchema.model_validate(valid)
    assert set(parsed.document.model_dump()) == {
        "no_medications_reported", "notes", "items"
    }
    from pydantic import ValidationError as SchemaValidationError
    with pytest.raises(SchemaValidationError):
        MedicationReconciliationAmendmentSchema.model_validate({
            **valid,
            "document": {**valid["document"], "expected_updated_at": "2026-11-10T10:00:00Z"},
        })


def test_s04_finalization_authorized_then_authoritative_readback(
    ctx, clinic, client, auth_headers_for
):
    record = _create(ctx, clinic)
    headers = _headers(auth_headers_for, ctx["doctor"], key="s04-finalize-readback")
    before = _get(client, record.id, headers)
    assert before["record_status"] == "draft"
    response = _finalize(client, record.id, headers, before)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["reconciliation"]["record_status"] == "final"
    after = _get(client, record.id, headers)
    assert after["record_status"] == "final"
    assert after["record_version"] == 1
    assert after["finalized_by_staff_id"] == ctx["doctor"].id
    assert after["finalized_at"] is not None
    assert after["items"] == before["items"]


def test_s04_finalization_requires_idempotency_key_and_timestamp(
    ctx, clinic, client, auth_headers_for
):
    record = _create(ctx, clinic)
    normal = _headers(auth_headers_for, ctx["doctor"])
    before = _get(client, record.id, normal)
    missing_key = _finalize(client, record.id, normal, before)
    assert 400 <= missing_key.status_code < 500, missing_key.get_json()
    missing_cas = client.post(
        f"{BASE}/{record.id}/finalize",
        headers={**normal, "Idempotency-Key": "s04-missing-cas"},
        json={"expected_version": 1},
    )
    assert 400 <= missing_cas.status_code < 500, missing_cas.get_json()
    assert _get(client, record.id, normal)["record_status"] == "draft"


def test_s04_stale_review_cannot_finalize_same_author_changed_draft(
    ctx, clinic, client, auth_headers_for
):
    record = _create(ctx, clinic)
    headers = _headers(auth_headers_for, ctx["doctor"])
    reviewed = _get(client, record.id, headers)
    changed = client.patch(
        f"{BASE}/{record.id}", headers=headers,
        json={
            "expected_updated_at": reviewed["updated_at"],
            "no_medications_reported": False,
            "notes": "Updated after original review",
            "items": reviewed["items"],
        },
    )
    assert changed.status_code == 200, changed.get_json()
    assert changed.get_json()["reconciliation"]["updated_at"] != reviewed["updated_at"]
    stale = _finalize(
        client, record.id,
        {**headers, "Idempotency-Key": "s04-stale-finalization"},
        reviewed,
    )
    assert stale.status_code == 409, stale.get_json()
    authoritative = _get(client, record.id, headers)
    assert authoritative["record_status"] == "draft"
    assert authoritative["notes"] == "Updated after original review"
    assert authoritative["updated_at"] == changed.get_json()["reconciliation"]["updated_at"]


def test_s04_finalization_idempotent_same_operation_no_second_mutation(
    ctx, clinic, client, auth_headers_for
):
    record = _create(ctx, clinic)
    headers = _headers(auth_headers_for, ctx["doctor"], key="s04-exactly-once")
    reviewed = _get(client, record.id, headers)
    first = _finalize(client, record.id, headers, reviewed)
    assert first.status_code == 200, first.get_json()
    initial = _get(client, record.id, headers)
    second = _finalize(client, record.id, headers, reviewed)
    assert second.status_code == 200, second.get_json()
    after = _get(client, record.id, headers)
    assert after["record_status"] == "final"
    assert after["record_version"] == 1
    assert after["finalized_at"] == initial["finalized_at"]
    # Same idempotency key with a different precondition must be rejected.
    different = client.post(
        f"{BASE}/{record.id}/finalize", headers=headers,
        json={"expected_version": 1, "expected_updated_at": "2026-11-10T10:00:00Z"},
    )
    assert different.status_code == 409, different.get_json()


def test_s04_author_and_role_separation(ctx, clinic, client, auth_headers_for):
    record = _create(ctx, clinic)
    doctor = _headers(auth_headers_for, ctx["doctor"])
    before = _get(client, record.id, doctor)
    pharmacist = _headers(
        auth_headers_for, ctx["pharmacist"], role=Role.PHARMACIST,
        key="s04-nonauthor-finalize",
    )
    other_staff = _finalize(client, record.id, pharmacist, before)
    assert other_staff.status_code == 409, other_staff.get_json()
    nurse = _headers(auth_headers_for, ctx["nurse"], role=Role.NURSE, key="s04-nurse")
    forbidden = _finalize(client, record.id, nurse, before)
    assert forbidden.status_code == 403, forbidden.get_json()
    assert _get(client, record.id, doctor)["record_status"] == "draft"


def test_s04_amend_route_accepts_complete_document_and_history_readback(
    ctx, clinic, client, auth_headers_for
):
    record = _create(ctx, clinic)
    headers = _headers(auth_headers_for, ctx["doctor"], key="s04-amend-base")
    reviewed = _get(client, record.id, headers)
    assert _finalize(client, record.id, headers, reviewed).status_code == 200
    finalized = _get(client, record.id, headers)
    doc = {
        "no_medications_reported": finalized["no_medications_reported"],
        "notes": "Clinician correction after sign-off",
        "items": finalized["items"],
    }
    response = client.post(
        f"{BASE}/{record.id}/amend",
        headers=_headers(auth_headers_for, ctx["doctor"]),
        json={
            "expected_version": 1,
            "reason": "Correct clinical narrative",
            "document": doc,
        },
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["reconciliation"]["record_status"] == "amended"
    assert response.get_json()["amendment"]["new_version"] == 2
    latest = _get(client, record.id, headers)
    assert latest["record_status"] == "amended"
    assert latest["record_version"] == 2
    assert latest["notes"] == doc["notes"]
    history = client.get(f"{BASE}/{record.id}/amendments", headers=headers)
    assert history.status_code == 200, history.get_json()
    assert history.get_json()["total"] == 1
    assert history.get_json()["items"][0]["new_version"] == 2
    assert history.get_json()["items"][0]["reason"] == "Correct clinical narrative"


def test_s04_stale_amendment_is_rejected_and_latest_stays_authoritative(
    ctx, clinic, client, auth_headers_for
):
    record = _create(ctx, clinic)
    headers = _headers(auth_headers_for, ctx["doctor"], key="s04-amend-stale-base")
    reviewed = _get(client, record.id, headers)
    assert _finalize(client, record.id, headers, reviewed).status_code == 200
    final = _get(client, record.id, headers)
    body = {
        "expected_version": 1,
        "reason": "First correction",
        "document": {
            "no_medications_reported": final["no_medications_reported"],
            "notes": "First correction",
            "items": final["items"],
        },
    }
    first = client.post(f"{BASE}/{record.id}/amend", headers=headers, json=body)
    assert first.status_code == 200, first.get_json()
    stale = client.post(
        f"{BASE}/{record.id}/amend", headers=headers,
        json={**body, "reason": "Stale correction", "document": {**body["document"], "notes": "Stale"}},
    )
    assert stale.status_code == 409, stale.get_json()
    latest = _get(client, record.id, headers)
    assert latest["record_version"] == 2
    assert latest["notes"] == "First correction"


def test_s04_unresolved_medication_decisions_still_block_finalization(
    ctx, clinic, client, auth_headers_for
):
    record = _create(ctx, clinic, items=[_item(decision="verify")])
    headers = _headers(auth_headers_for, ctx["doctor"], key="s04-unresolved")
    reviewed = _get(client, record.id, headers)
    response = _finalize(client, record.id, headers, reviewed)
    assert response.status_code == 409, response.get_json()
    assert _get(client, record.id, headers)["record_status"] == "draft"
