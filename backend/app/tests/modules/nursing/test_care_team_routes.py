"""CT1C: real Flask routing, authorization and CT1B2 delegation contracts."""

from __future__ import annotations

import pytest

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
from app.extensions import db
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.services import care_team_assignment_service
from app.modules.ward.models.ward_model import Admission, Bed, Ward


def _case(clinic, make_patient, make_staff):
    patient = make_patient(clinic)
    admin = make_staff(clinic, role=Role.ADMIN)
    doctor = make_staff(clinic, role=Role.DOCTOR, first_name="Dr", last_name="One")
    nurse = make_staff(clinic, role=Role.NURSE, first_name="Nurse", last_name="One")
    ward = Ward(
        clinic_id=clinic.id,
        name="CT1C Ward",
        ward_type=WardType.GENERAL,
        capacity=4,
    )
    db.session.add(ward)
    db.session.flush()
    bed = Bed(ward_id=ward.id, bed_number="CT1C-001", status=BedStatus.OCCUPIED)
    db.session.add(bed)
    db.session.flush()
    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=doctor.id,
        status=AdmissionStatus.ADMITTED,
    )
    db.session.add(admission)
    db.session.commit()
    return patient, admin, doctor, nurse, admission


def _url(admission):
    return f"/api/v1/nursing/admissions/{admission.id}/care-team"


def _headers(auth_headers_for, staff):
    return auth_headers_for(staff.user, role=staff.user.role)


def _assign(client, admission, headers, staff, kind, current=None):
    return client.post(
        _url(admission) + "/assignments",
        headers=headers,
        json={
            "assignment_kind": kind,
            "staff_id": staff.id,
            "assignment_reason": "Shift coverage",
            "expected_current_assignment_id": current,
        },
    )


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch):
    # CT1B2 has separate tests for the audit payload and rollback semantics.
    monkeypatch.setattr(
        care_team_assignment_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )


def test_routes_registered_on_existing_nursing_blueprint(app):
    paths = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()}
    prefix = "/api/v1/nursing/admissions/<int:admission_id>/care-team"
    assert prefix in paths
    assert prefix + "/assignment-history" in paths
    assert prefix + "/assignments" in paths
    assert prefix + "/end-assignment" in paths
    assert paths[prefix].startswith("nursing.")


def test_unauthenticated_reads_and_writes_are_denied(
    client, clinic, make_patient, make_staff,
):
    _patient, _admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    assert client.get(_url(admission)).status_code == 401
    assert client.get(_url(admission) + "/assignment-history").status_code == 401
    assert client.post(
        _url(admission) + "/assignments",
        json={"assignment_kind": "primary_doctor", "staff_id": doctor.id,
              "assignment_reason": "Initial"},
    ).status_code == 401


def test_unassigned_response_is_explicit_and_never_inferred(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    patient, admin, doctor, nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    result = client.get(_url(admission), headers=_headers(auth_headers_for, nurse))
    assert result.status_code == 200
    body = result.get_json()
    assert body["patient_id"] == patient.id
    assert body["admission_id"] == admission.id
    assert body["responsibility"]["primary_doctor"] is None
    assert body["responsibility"]["primary_nurse"] is None
    assert body["responsibility"]["confirmed"] is False
    assert body["authoritative_assignment_available"] is True
    assert body["admission_active"] is True


def test_admin_assigns_both_roles_and_clinicians_can_read(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    patient, admin, doctor, nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    admin_headers = _headers(auth_headers_for, admin)
    res_d = _assign(client, admission, admin_headers, doctor, "primary_doctor")
    res_n = _assign(client, admission, admin_headers, nurse, "primary_nurse")
    assert res_d.status_code == 201
    assert res_n.status_code == 201
    doc = res_d.get_json()["assignment"]
    assert doc["staff_id"] == doctor.id
    assert doc["staff_display_name"] == "Dr One"
    assert doc["assignment_kind"] == "primary_doctor"
    for reader in (admin, doctor, nurse):
        result = client.get(
            _url(admission), headers=_headers(auth_headers_for, reader)
        )
        assert result.status_code == 200
        responsibility = result.get_json()["responsibility"]
        assert responsibility["confirmed"] is True
        assert responsibility["primary_doctor_staff_id"] == doctor.id
        assert responsibility["primary_nurse_staff_id"] == nurse.id
    history = client.get(
        _url(admission) + "/assignment-history",
        headers=_headers(auth_headers_for, doctor),
    )
    assert history.status_code == 200
    assert history.get_json()["total"] == 2
    assert all(row["ended_at"] is None for row in history.get_json()["items"])


def test_nurse_and_doctor_cannot_assign_or_end_ownership(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    patient, admin, doctor, nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    for person in (doctor, nurse):
        headers = _headers(auth_headers_for, person)
        assert _assign(
            client, admission, headers, doctor, "primary_doctor"
        ).status_code == 403
        assert client.post(
            _url(admission) + "/end-assignment",
            headers=headers,
            json={"assignment_kind": "primary_doctor",
                  "expected_current_assignment_id": 1,
                  "end_reason": "Change"},
        ).status_code == 403
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_strict_payload_validation_rejects_unknown_and_string_ids(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    _patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    headers = _headers(auth_headers_for, admin)
    path = _url(admission) + "/assignments"
    for payload in (
        {"assignment_kind": "primary_doctor", "staff_id": str(doctor.id),
         "assignment_reason": "A"},
        {"assignment_kind": "consultant", "staff_id": doctor.id,
         "assignment_reason": "A"},
        {"assignment_kind": "primary_doctor", "staff_id": doctor.id,
         "assignment_reason": "A", "is_super_admin": True},
        {"assignment_kind": "primary_doctor", "staff_id": doctor.id,
         "assignment_reason": " "},
    ):
        assert client.post(path, headers=headers, json=payload).status_code == 422
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_invalid_clinician_role_rejected_by_service(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    _patient, admin, _doctor, nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    assert _assign(
        client, admission, _headers(auth_headers_for, admin),
        nurse, "primary_doctor"
    ).status_code == 422
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_stale_replace_is_denied_and_successful_replace_retains_history(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    _patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    replacement = make_staff(clinic, role=Role.DOCTOR)
    admin_headers = _headers(auth_headers_for, admin)
    first = _assign(client, admission, admin_headers, doctor, "primary_doctor")
    assert first.status_code == 201
    first_id = first.get_json()["assignment"]["assignment_id"]
    no_version = _assign(
        client, admission, admin_headers, replacement, "primary_doctor"
    )
    assert no_version.status_code == 409
    second = _assign(
        client, admission, admin_headers, replacement,
        "primary_doctor", current=first_id,
    )
    assert second.status_code == 200
    stale = _assign(
        client, admission, admin_headers, doctor,
        "primary_doctor", current=first_id,
    )
    assert stale.status_code == 409
    history = client.get(
        _url(admission) + "/assignment-history", headers=admin_headers
    )
    items = history.get_json()["items"]
    assert len(items) == 2
    assert sum(row["ended_at"] is None for row in items) == 1
    current = client.get(_url(admission), headers=admin_headers).get_json()
    assert current["responsibility"]["primary_doctor_staff_id"] == replacement.id


def test_explicit_end_keeps_history_and_removes_current_owner(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    _patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    headers = _headers(auth_headers_for, admin)
    first = _assign(client, admission, headers, doctor, "primary_doctor")
    first_id = first.get_json()["assignment"]["assignment_id"]
    ended = client.post(
        _url(admission) + "/end-assignment",
        headers=headers,
        json={"assignment_kind": "primary_doctor",
              "expected_current_assignment_id": first_id,
              "end_reason": "Reassigned ward"},
    )
    assert ended.status_code == 200
    assert ended.get_json()["assignment"]["ended_at"] is not None
    assert client.post(
        _url(admission) + "/end-assignment",
        headers=headers,
        json={"assignment_kind": "primary_doctor",
              "expected_current_assignment_id": first_id,
              "end_reason": "Retry"},
    ).status_code == 409
    current = client.get(_url(admission), headers=headers).get_json()
    assert current["responsibility"]["primary_doctor"] is None
    history = client.get(
        _url(admission) + "/assignment-history", headers=headers,
    ).get_json()
    assert history["total"] == 1
    assert history["items"][0]["end_reason"] == "Reassigned ward"


def test_cross_tenant_context_is_not_disclosed(
    client, clinic, make_clinic, make_patient, make_staff, auth_headers_for,
):
    _patient, admin, doctor, nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    foreign = make_clinic(name="CT1C Other Clinic")
    foreign_admin = make_staff(foreign, role=Role.ADMIN)
    foreign_doctor = make_staff(foreign, role=Role.DOCTOR)
    headers = _headers(auth_headers_for, foreign_admin)
    assert client.get(_url(admission), headers=headers).status_code == 404
    assert client.get(
        _url(admission) + "/assignment-history", headers=headers
    ).status_code == 404
    assert _assign(
        client, admission, headers, foreign_doctor, "primary_doctor"
    ).status_code == 404


def test_inactive_admin_staff_cannot_create_assignment(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    _patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    admin.status = StaffStatus.SUSPENDED
    db.session.commit()
    assert _assign(
        client, admission, _headers(auth_headers_for, admin),
        doctor, "primary_doctor"
    ).status_code == 403


def test_discharged_admission_not_reported_as_current_responsibility(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    _patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    headers = _headers(auth_headers_for, admin)
    assert _assign(
        client, admission, headers, doctor, "primary_doctor"
    ).status_code == 201
    admission.status = AdmissionStatus.DISCHARGED
    db.session.commit()
    response = client.get(_url(admission), headers=headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["admission_active"] is False
    assert body["responsibility"]["primary_doctor"] is None
    assert body["responsibility"]["confirmed"] is False
    assert client.get(
        _url(admission) + "/assignment-history", headers=headers
    ).get_json()["total"] == 1
    assert _assign(
        client, admission, headers, doctor, "primary_doctor"
    ).status_code == 409


def test_non_admin_with_admin_claim_cannot_mutate(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    _patient, _admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    headers = auth_headers_for(doctor.user, role=Role.ADMIN)
    assert _assign(
        client, admission, headers, doctor, "primary_doctor"
    ).status_code == 422
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_no_new_chat_conversation_created_by_assignment_route(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    from app.modules.chat.models.conversation_model import Conversation
    _patient, admin, doctor, _nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    assert _assign(
        client, admission, _headers(auth_headers_for, admin),
        doctor, "primary_doctor"
    ).status_code == 201
    assert db.session.query(Conversation).count() == 0


def test_inactive_primary_clinician_makes_responsibility_unconfirmed(
    client, clinic, make_patient, make_staff, auth_headers_for,
):
    _patient, admin, doctor, nurse, admission = _case(
        clinic, make_patient, make_staff,
    )
    headers = _headers(auth_headers_for, admin)
    assert _assign(
        client, admission, headers, doctor, "primary_doctor"
    ).status_code == 201
    assert _assign(
        client, admission, headers, nurse, "primary_nurse"
    ).status_code == 201
    doctor.status = StaffStatus.SUSPENDED
    db.session.commit()
    result = client.get(_url(admission), headers=headers)
    assert result.status_code == 200
    body = result.get_json()["responsibility"]
    assert body["primary_doctor"]["staff_active"] is False
    assert body["primary_doctor_confirmed"] is False
    assert body["primary_nurse_confirmed"] is True
    assert body["confirmed"] is False
