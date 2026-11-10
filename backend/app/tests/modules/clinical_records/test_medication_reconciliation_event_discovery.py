"""P23G.S00: admission-scoped, role-gated medication reconciliation discovery.

These tests use the existing isolated Flask `testing` database; no production or
real-development database should be touched by this focused suite.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from app.core.enums.medication_reconciliation_enums import MedicationReconciliationEvent
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import BedStatus
from app.extensions import db
from app.modules.medication_reconciliation.services import (
    medication_reconciliation_service as service,
)
from app.modules.ward.models.ward_model import Bed, WardTransfer
from app.tests.modules.nursing.test_nursing_service_invariants import _setup_admission


PREFIX = "/api/v1/medication-reconciliations"


@pytest.fixture()
def scenario(clinic, make_patient, make_staff, monkeypatch):
    # Prevent audit side effects; clinical entity + auth checks still use DB.
    monkeypatch.setattr(service, "create_audit_log", lambda **_kwargs: None)
    patient, nurse, doctor, ward, bed, admission = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    pharmacist = make_staff(clinic, role=Role.PHARMACIST)
    return {
        "clinic": clinic,
        "patient": patient,
        "nurse": nurse,
        "doctor": doctor,
        "pharmacist": pharmacist,
        "ward": ward,
        "bed": bed,
        "admission": admission,
    }


def _create(scenario, event="admission", transfer_id=None):
    return service.create_medication_reconciliation(
        clinic_id=scenario["clinic"].id,
        admission_id=scenario["admission"].id,
        event_type=MedicationReconciliationEvent(event),
        ward_transfer_id=transfer_id,
        actor_staff_id=scenario["doctor"].id,
        no_medications_reported=True,
        items=[],
    )


def _headers(auth_headers_for, actor, role):
    return auth_headers_for(actor.user, role=role)


def test_additive_admission_discovery_routes_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert f"{PREFIX}/admissions/<int:admission_id>" in rules
    assert (
        f"{PREFIX}/admissions/<int:admission_id>/events/<event_type>"
        in rules
    )


@pytest.mark.parametrize("suffix", ["/admissions/7", "/admissions/7/events/admission"])
def test_both_discovery_routes_reject_unauthenticated_requests(client, suffix):
    response = client.get(PREFIX + suffix)
    assert response.status_code in (401, 422)


def test_list_empty_admission_is_explicit_not_a_fabricated_reconciliation(
    scenario, client, auth_headers_for
):
    aid = scenario["admission"].id
    response = client.get(
        f"{PREFIX}/admissions/{aid}",
        headers=_headers(auth_headers_for, scenario["nurse"], Role.NURSE),
    )
    assert response.status_code == 200
    assert response.get_json() == {"items": [], "total": 0, "page": 1, "per_page": 50}


def test_list_returns_paginated_real_records_and_filters_by_event(
    scenario, client, auth_headers_for
):
    a = _create(scenario, "admission")
    d = _create(scenario, "discharge")
    aid = scenario["admission"].id
    headers = _headers(auth_headers_for, scenario["doctor"], Role.DOCTOR)
    response = client.get(f"{PREFIX}/admissions/{aid}?per_page=1&page=1", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 2
    assert payload["page"] == 1 and payload["per_page"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] in {a.id, d.id}

    filtered = client.get(
        f"{PREFIX}/admissions/{aid}?event_type=discharge", headers=headers
    )
    assert filtered.status_code == 200
    assert filtered.get_json()["total"] == 1
    assert filtered.get_json()["items"][0]["id"] == d.id
    assert filtered.get_json()["items"][0]["event_identity"] == f"discharge:{aid}"
    assert filtered.get_json()["items"][0]["record_status"] == "draft"


def test_event_lookup_reuses_existing_authoritative_identity_and_envelope(
    scenario, client, auth_headers_for
):
    record = _create(scenario)
    aid = scenario["admission"].id
    response = client.get(
        f"{PREFIX}/admissions/{aid}/events/admission",
        headers=_headers(auth_headers_for, scenario["pharmacist"], Role.PHARMACIST),
    )
    assert response.status_code == 200
    payload = response.get_json()["reconciliation"]
    assert payload["id"] == record.id
    assert payload["patient_id"] == scenario["patient"].id
    assert payload["event_type"] == "admission"
    assert payload["no_medications_reported"] is True


def test_event_lookup_transfer_is_bound_to_matching_ward_transfer(
    scenario, client, auth_headers_for
):
    bed = Bed(
        ward_id=scenario["ward"].id,
        bed_number="P23G-S00-SECOND",
        status=BedStatus.AVAILABLE,
    )
    db.session.add(bed)
    db.session.flush()
    transfer = WardTransfer(
        admission_id=scenario["admission"].id,
        from_bed_id=scenario["bed"].id,
        to_bed_id=bed.id,
        reason="Clinical handoff",
        transferred_at=datetime.now(timezone.utc),
    )
    db.session.add(transfer)
    db.session.flush()
    record = _create(scenario, "transfer", transfer_id=transfer.id)
    aid = scenario["admission"].id
    headers = _headers(auth_headers_for, scenario["doctor"], Role.DOCTOR)
    url = f"{PREFIX}/admissions/{aid}/events/transfer"
    found = client.get(f"{url}?ward_transfer_id={transfer.id}", headers=headers)
    assert found.status_code == 200
    assert found.get_json()["reconciliation"]["id"] == record.id
    missing_ref = client.get(url, headers=headers)
    assert missing_ref.status_code == 422
    wrong_context = client.get(
        f"{PREFIX}/admissions/{aid}/events/admission?ward_transfer_id={transfer.id}",
        headers=headers,
    )
    assert wrong_context.status_code == 422


@pytest.mark.parametrize("query", [
    "page=0",
    "page=1&page=2",
    "per_page=101",
    "event_type=other",
    "clinic_id=1",
])
def test_list_rejects_invalid_or_ambiguous_queries(
    scenario, client, auth_headers_for, query
):
    aid = scenario["admission"].id
    response = client.get(
        f"{PREFIX}/admissions/{aid}?{query}",
        headers=_headers(auth_headers_for, scenario["doctor"], Role.DOCTOR),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("query", [
    "ward_transfer_id=-1",
    "ward_transfer_id=1&ward_transfer_id=2",
    "patient_id=1",
])
def test_event_lookup_rejects_untrusted_query_inputs(
    scenario, client, auth_headers_for, query
):
    aid = scenario["admission"].id
    response = client.get(
        f"{PREFIX}/admissions/{aid}/events/admission?{query}",
        headers=_headers(auth_headers_for, scenario["nurse"], Role.NURSE),
    )
    assert response.status_code == 422


def test_missing_event_returns_not_found_not_an_empty_fake_record(
    scenario, client, auth_headers_for
):
    aid = scenario["admission"].id
    response = client.get(
        f"{PREFIX}/admissions/{aid}/events/discharge",
        headers=_headers(auth_headers_for, scenario["nurse"], Role.NURSE),
    )
    assert response.status_code == 404


def test_invalid_event_path_rejected(scenario, client, auth_headers_for):
    aid = scenario["admission"].id
    response = client.get(
        f"{PREFIX}/admissions/{aid}/events/home",
        headers=_headers(auth_headers_for, scenario["doctor"], Role.DOCTOR),
    )
    assert response.status_code == 422


def test_nurse_can_read_but_cannot_create_or_change_records(
    scenario, client, auth_headers_for
):
    aid = scenario["admission"].id
    headers = _headers(auth_headers_for, scenario["nurse"], Role.NURSE)
    assert client.get(f"{PREFIX}/admissions/{aid}", headers=headers).status_code == 200
    denied = client.post(
        PREFIX,
        headers=headers,
        json={"admission_id": aid, "event_type": "admission", "items": []},
    )
    assert denied.status_code == 403


def test_patient_role_denied_discovery(scenario, client, auth_headers_for, make_user):
    patient_user = make_user(scenario["clinic"], role=Role.PATIENT)
    aid = scenario["admission"].id
    headers = auth_headers_for(patient_user, role=Role.PATIENT)
    assert client.get(f"{PREFIX}/admissions/{aid}", headers=headers).status_code == 403
    assert (
        client.get(f"{PREFIX}/admissions/{aid}/events/admission", headers=headers).status_code
        == 403
    )


def test_foreign_clinic_admission_not_discoverable(
    scenario, client, auth_headers_for, make_clinic, make_patient, make_staff
):
    other = make_clinic()
    _patient, _nurse, _doctor, _ward, _bed, foreign_admission = _setup_admission(
        clinic=other,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    headers = _headers(auth_headers_for, scenario["doctor"], Role.DOCTOR)
    aid = foreign_admission.id
    assert client.get(f"{PREFIX}/admissions/{aid}", headers=headers).status_code == 404
    assert (
        client.get(f"{PREFIX}/admissions/{aid}/events/admission", headers=headers).status_code
        == 404
    )


def test_event_lookup_is_get_only(scenario, app):
    # No event discovery route accepts POST, PATCH or performs a mutation.
    rules = [
        rule for rule in app.url_map.iter_rules()
        if rule.rule.startswith(f"{PREFIX}/admissions/")
    ]
    assert len(rules) == 2
    assert all("GET" in rule.methods for rule in rules)
    assert all("POST" not in rule.methods and "PATCH" not in rule.methods for rule in rules)
