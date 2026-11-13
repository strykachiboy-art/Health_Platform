"""Surgery B only: focused HTTP boundary tests (no prior suite reruns)."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.modules.surgery.routes import surgery_http_routes as route


@pytest.fixture
def actors(clinic, make_staff, auth_headers_for):
    doctor = make_staff(clinic, role=Role.DOCTOR)
    nurse = make_staff(clinic, role=Role.NURSE)
    admin = make_staff(clinic, role=Role.ADMIN)
    patient = make_staff(clinic, role=Role.PATIENT)
    return {
        "doctor": (doctor, auth_headers_for(doctor.user)),
        "nurse": (nurse, auth_headers_for(nurse.user)),
        "admin": (admin, auth_headers_for(admin.user)),
        "patient": (patient, auth_headers_for(patient.user)),
    }


def _book():
    return {
        "surgical_case_id": 7,
        "theatre_id": 4,
        "starts_at": "2030-01-11T10:00:00+00:00",
        "ends_at": "2030-01-11T12:00:00+00:00",
    }


def _row(clinic, **overrides):
    data = dict(
        id=18, clinic_id=clinic.id, surgical_case_id=7,
        theatre_id=4, status="scheduled", version=1,
        procedure_order_id=9, code="OR-1", is_active=True,
        starts_at=datetime(2030, 1, 11, 10), ends_at=datetime(2030, 1, 11, 12),
        staff_id=4, privilege_kind="surgeon", procedure_code="SURG-A",
        expires_at=datetime(2031, 1, 11), revoked_at=None,
        signed_document_sha256="ab" * 32,
        phase="sign_in", attested_at=datetime(2030, 1, 11),
        evidence_sha256="ef" * 32,
        assessment_id=6, sequence=1, observed_at=datetime(2030, 1, 11),
        content_sha256="cd" * 32, report_id=21, signer_kind="surgeon",
        document_sha256="cd" * 32,
    )
    return SimpleNamespace(**(data | overrides))


def _headers(actors, role="doctor"):
    return actors[role][1]


def test_registered_contracts_are_documentation_only(app):
    routes = {
        (r.rule, method)
        for r in app.url_map.iter_rules()
        if r.rule.startswith("/api/v1/surgery")
        for method in r.methods
        if method not in ("HEAD", "OPTIONS")
    }
    assert ("/api/v1/surgery/cases", "POST") in routes
    assert ("/api/v1/surgery/bookings", "POST") in routes
    assert ("/api/v1/surgery/cases/<int:case_id>/consents", "POST") in routes
    assert ("/api/v1/surgery/cases/<int:case_id>/checklists/<phase>", "POST") in routes
    assert ("/api/v1/surgery/cases/<int:case_id>/preanesthesia", "POST") in routes
    assert ("/api/v1/surgery/reports/<int:report_id>/sign", "POST") in routes
    assert ("/api/v1/dashboard/surgery", "GET") in {
        (r.rule, method) for r in app.url_map.iter_rules()
        for method in r.methods if method not in ("HEAD", "OPTIONS")
    }
    assert len(routes) == 25, routes
    forbidden_words = ("execute", "induction", "complete", "transfer", "emar", "sign_out", "discharge", "bill", "pacu")
    assert all(not any(word in path.lower() for word in forbidden_words)
               for path, _ in routes)
    assert all(method in ("POST", "PATCH", "GET") for _, method in routes)


def test_missing_auth_and_patient_role_cannot_mutate(client, actors, monkeypatch):
    called = []
    monkeypatch.setattr(route.booking, "schedule_theatre", lambda **kw: called.append(kw))
    assert client.post("/api/v1/surgery/bookings", json=_book()).status_code == 401
    assert client.post("/api/v1/surgery/bookings", json=_book(),
                       headers=_headers(actors, "patient")).status_code == 403
    assert called == []


def test_doctor_booking_uses_authenticated_staff_and_clinic(client, clinic, actors, monkeypatch):
    called = []
    def schedule(**kw):
        called.append(kw)
        return _row(clinic)
    monkeypatch.setattr(route.booking, "schedule_theatre", schedule)
    resp = client.post("/api/v1/surgery/bookings", json=_book(), headers=_headers(actors))
    assert resp.status_code == 201, resp.get_json()
    assert len(called) == 1
    assert called[0]["actor_staff_id"] == actors["doctor"][0].id
    assert called[0]["clinic_id"] == clinic.id
    assert called[0]["surgical_case_id"] == 7
    assert called[0]["starts_at"].tzinfo is not None
    value = resp.get_json()
    assert value["documentation_only"] is True
    assert value["operative_completion_authorized"] is False
    assert value["anesthesia_induction_authorized"] is False
    assert value["medication_administration_authorized"] is False
    assert "patient_id" not in str(value)
    assert resp.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("invalid", [
    {"expected_version": True, "starts_at": "2030-01-11T10:00:00Z", "ends_at": "2030-01-11T12:00:00Z"},
    {"expected_version": 1, "starts_at": "2030-01-11T10:00:00", "ends_at": "2030-01-11T12:00:00Z"},
    {"expected_version": 1, "starts_at": "2030-01-11T10:00:00Z", "ends_at": "2030-01-11T12:00:00Z", "operative_completion_authorized": True},
])
def test_reschedule_strict_input_rejected_before_service(client, actors, monkeypatch, invalid):
    calls = []
    monkeypatch.setattr(route.booking, "reschedule_theatre", lambda **kw: calls.append(kw))
    resp = client.patch("/api/v1/surgery/bookings/18", json=invalid,
                        headers=_headers(actors))
    assert resp.status_code == 422
    assert calls == []


def test_idempotency_header_rejected_without_claiming_safe_replay(client, actors, monkeypatch):
    called = []
    monkeypatch.setattr(route.booking, "schedule_theatre", lambda **kw: called.append(kw))
    h = dict(_headers(actors), **{"Idempotency-Key": "unverified-surgery-replay"})
    resp = client.post("/api/v1/surgery/bookings", json=_book(), headers=h)
    assert resp.status_code == 422
    assert called == []


def test_inactive_or_foreign_clinic_actor_rejected(client, clinic, make_clinic, actors,
                                                  auth_headers_for, monkeypatch):
    calls = []
    monkeypatch.setattr(route.booking, "schedule_theatre", lambda **kw: calls.append(kw))
    foreign = make_clinic(name="Surgery B foreign")
    actor = actors["doctor"][0]
    response = client.post("/api/v1/surgery/bookings", json=_book(),
        headers=auth_headers_for(actor.user, clinic_context_id=foreign.id))
    assert response.status_code in (403, 404, 422)
    assert calls == []
    actor.status = StaffStatus.SUSPENDED
    from app.extensions import db
    db.session.flush()
    suspended = client.post("/api/v1/surgery/bookings", json=_book(),
                            headers=_headers(actors))
    assert suspended.status_code == 403
    assert calls == []


def test_signout_not_exposed_and_phase_role_bound(client, clinic, actors, monkeypatch):
    called = []
    monkeypatch.setattr(route.checklist, "attest_checklist_phase", lambda **kw: called.append(kw) or _row(clinic))
    body = {"surgeon_staff_id": actors["doctor"][0].id,
            "anesthetist_staff_id": actors["doctor"][0].id + 20,
            "answers": {"patient_identity_confirmed": True}}
    path = "/api/v1/surgery/cases/7/checklists/"
    assert client.post(path + "sign_out", json=body,
                       headers=_headers(actors, "doctor")).status_code == 422
    assert client.post(path + "time_out", json=body,
                       headers=_headers(actors, "nurse")).status_code == 404
    assert client.post(path + "sign_in", json=body,
                       headers=_headers(actors, "doctor")).status_code == 404
    assert called == []
    good = client.post(path + "sign_in", json=body,
                       headers=_headers(actors, "nurse"))
    assert good.status_code == 201
    assert called[0]["actor_staff_id"] == actors["nurse"][0].id
    assert called[0]["phase"] == "sign_in"


def test_consent_bytes_transient_canonical_and_actor_bound(client, clinic, actors, monkeypatch):
    called = []
    def record(**kwargs):
        called.append(kwargs)
        return _row(clinic, status="recorded")
    monkeypatch.setattr(route.authority, "record_surgical_consent", record)
    data = {
        "signer_kind": "patient", "signer_name": "Test Signer",
        "signed_document_ref": "test-evidence-01",
        "signed_document_b64": base64.b64encode(b"test only - no PHI").decode("ascii"),
        "signed_at": "2025-01-01T10:00:00Z",
        "expires_at": "2030-01-01T10:00:00Z",
    }
    bad = dict(data, signed_document_b64="%%%")
    assert client.post("/api/v1/surgery/cases/7/consents", json=bad,
                       headers=_headers(actors)).status_code == 422
    assert not called
    resp = client.post("/api/v1/surgery/cases/7/consents", json=data,
                       headers=_headers(actors))
    assert resp.status_code == 201, resp.get_json()
    assert called[0]["signed_document_bytes"] == b"test only - no PHI"
    assert called[0]["doctor_staff_id"] == actors["doctor"][0].id
    assert "Test Signer" not in str(resp.get_json())
    assert "test only - no PHI" not in str(resp.get_json())
    assert "signed_document_b64" not in str(resp.get_json())


def test_only_admin_may_record_professional_privilege(client, clinic, actors, monkeypatch):
    called = []
    monkeypatch.setattr(route.authority, "grant_surgical_privilege",
                        lambda **kw: called.append(kw) or _row(clinic))
    data = {
        "staff_id": actors["doctor"][0].id,
        "privilege_kind": "surgeon", "procedure_code": "SURG-A",
        "license_reference": "TEST-LICENSE", "credential_document_ref": "evidence-test",
        "credential_document_b64": base64.b64encode(b"no real credentials").decode(),
        "verification_source": "mock source; not real registry", "expires_at": "2031-01-01T10:00:00Z",
    }
    assert client.post("/api/v1/surgery/privileges", json=data,
                       headers=_headers(actors)).status_code == 403
    resp = client.post("/api/v1/surgery/privileges", json=data,
                       headers=_headers(actors, "admin"))
    assert resp.status_code == 201
    assert len(called) == 1
    assert called[0]["admin_staff_id"] == actors["admin"][0].id
    assert called[0]["credential_document_bytes"] == b"no real credentials"
    assert "TEST-LICENSE" not in str(resp.get_json())


def test_preanesthesia_actor_is_not_request_supplied(client, clinic, actors, monkeypatch):
    called = []
    monkeypatch.setattr(route.anesthesia, "record_preanesthesia_assessment",
                        lambda **kw: called.append(kw) or _row(clinic, status="draft"))
    data = {
        "surgeon_staff_id": actors["doctor"][0].id + 1,
        "asa_class": 2, "technique": "regional", "airway_assessment": "routine",
        "airway_plan": "test airway plan", "allergy_status": "none_known",
        "fasting_confirmed": True, "fasting_notes": "test confirmed",
        "risk_and_comorbidity_notes": "test risk", "plan_summary": "test plan",
        "monitoring_plan": "test monitoring", "contingency_plan": "test contingency",
    }
    path = "/api/v1/surgery/cases/7/preanesthesia"
    spoof = dict(data, anesthetist_staff_id=999)
    assert client.post(path, json=spoof, headers=_headers(actors)).status_code == 422
    good = client.post(path, json=data, headers=_headers(actors))
    assert good.status_code == 201, good.get_json()
    assert called[0]["actor_staff_id"] == actors["doctor"][0].id
    assert called[0]["anesthetist_staff_id"] == actors["doctor"][0].id


def test_report_signing_requires_service_and_strict_version(client, clinic, actors, monkeypatch):
    called = []
    monkeypatch.setattr(route.reports, "sign_operative_report",
                        lambda **kw: called.append(kw) or _row(clinic))
    path = "/api/v1/surgery/reports/21/sign"
    assert client.post(path, json={"expected_version": True,
            "signer_kind": "surgeon", "attestation_note": "test"},
            headers=_headers(actors)).status_code == 422
    assert not called
    resp = client.post(path, json={"expected_version": 1,
        "signer_kind": "surgeon", "attestation_note": "test"},
        headers=_headers(actors))
    assert resp.status_code == 201
    assert called[0]["report_id"] == 21
    assert called[0]["actor_staff_id"] == actors["doctor"][0].id
    assert called[0]["expected_version"] == 1
    assert resp.get_json()["operative_completion_authorized"] is False


def test_reconciliation_get_requires_case_scope_and_hides_narratives(client, clinic,
                                                                     actors, monkeypatch):
    called = []
    monkeypatch.setattr(route.reports, "get_operative_report",
                        lambda **kw: _row(clinic, status="surgeon_signed", id=21))
    def scope(**kwargs):
        called.append(kwargs)
        return {"case_id": kwargs["case_id"]}
    monkeypatch.setattr(route.read, "get_surgery_case_read_model", scope)
    path = "/api/v1/surgery/reports/21"
    assert client.get(path, headers=_headers(actors, "patient")).status_code == 403
    assert called == []
    resp = client.get(path, headers=_headers(actors))
    assert resp.status_code == 200
    assert called[0]["actor_user_id"] == actors["doctor"][0].user.id
    assert called[0]["clinic_id"] == clinic.id
    assert "procedure_narrative" not in str(resp.get_json())
    assert resp.headers["Cache-Control"] == "no-store"


def test_reconciliation_status_lists_require_case_scope(client, clinic, actors, monkeypatch):
    calls = []
    def scope(**kw):
        calls.append(kw)
        return {"case_id": kw["case_id"]}
    monkeypatch.setattr(route.read, "get_surgery_case_read_model", scope)
    for path in (
        "/api/v1/surgery/cases/7/bookings",
        "/api/v1/surgery/cases/7/consents",
    ):
        no_session = client.get(path)
        assert no_session.status_code == 401
        not_staff = client.get(path, headers=_headers(actors, "patient"))
        assert not_staff.status_code == 403
        resp = client.get(path, headers=_headers(actors))
        assert resp.status_code == 200
        assert resp.get_json()["data"]["items"] == []
        assert resp.get_json()["data"]["has_more"] is False
        assert resp.headers["Cache-Control"] == "no-store"
    assert len(calls) == 2
    assert all(x["actor_user_id"] == actors["doctor"][0].user.id for x in calls)
    assert all(x["clinic_id"] == clinic.id for x in calls)


def test_checklist_reconciliation_does_not_enable_signout(client, clinic, actors, monkeypatch):
    calls = []
    monkeypatch.setattr(route.read, "get_surgery_case_read_model", lambda **kw: calls.append(kw) or {})
    monkeypatch.setattr(route.checklist, "get_checklist_phase", lambda **kw: _row(clinic))
    path = "/api/v1/surgery/cases/7/checklists/"
    assert client.get(path + "sign_out", headers=_headers(actors)).status_code == 422
    assert calls == []
    resp = client.get(path + "sign_in", headers=_headers(actors))
    assert resp.status_code == 200
    assert resp.get_json()["data"]["phase"] == "sign_in"
    assert len(calls) == 1


def test_report_signatures_reconciles_after_scope_check(client, clinic, actors, monkeypatch):
    calls = []
    monkeypatch.setattr(route.read, "get_surgery_case_read_model", lambda **kw: calls.append(kw) or {})
    monkeypatch.setattr(route.reports, "get_operative_report", lambda **kw: _row(clinic, id=21))
    monkeypatch.setattr(route.reports, "list_operative_signatures", lambda **kw: [
        _row(clinic, id=8, report_id=21, signer_kind="surgeon", signed_at=datetime(2030, 1, 11))
    ])
    resp = client.get("/api/v1/surgery/reports/21/signatures", headers=_headers(actors))
    assert resp.status_code == 200
    item = resp.get_json()["data"]["items"][0]
    assert item["signer_kind"] == "surgeon"
    assert item["report_id"] == 21
    assert "attestation_note" not in str(resp.get_json())
    assert "procedure_narrative" not in str(resp.get_json())
    assert len(calls) == 1
