"""CE1-S11 ONLY: clinic-/team-scoped GET-only surgical documentation read APIs.

Only this new test file is selected; earlier Surgery and ProcedureOrder suites
are intentionally deferred. Source fixture imports do not select their tests.
"""
from __future__ import annotations

import pytest

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.surgery.models.surgical_case_model import SurgicalCase
from app.modules.surgery.models.surgical_clinical_reference_model import SurgicalClinicalReference
from app.modules.surgery.services import surgical_dashboard_read_service as svc
from app.tests.modules.surgery.test_surgical_authorization import scenario
from app.tests.modules.surgery.test_surgical_safety_checklist import ready
from app.tests.modules.surgery.test_anesthesia_foundation import anesthesia_ready
from app.tests.modules.surgery.test_intraoperative_evidence_foundation import ready_s06
from app.tests.modules.surgery.test_pacu_preparation_handoff import workspace as s08_workspace
from app.tests.modules.surgery.test_operative_report_signatures import ready_s09
from app.tests.modules.surgery.test_surgical_clinical_references import ready_s10, _ref

BASE = '/api/v1/dashboard/surgery'


def _headers(auth_headers_for, person):
    return auth_headers_for(person.user)


def _overview(client, auth_headers_for, person, query=''):
    return client.get(BASE + query, headers=_headers(auth_headers_for, person))


def _detail(client, auth_headers_for, person, case_id):
    return client.get(f'{BASE}/cases/{case_id}', headers=_headers(auth_headers_for, person))


def test_doctor_dashboard_exposes_documentation_not_execution(ready_s10, client, auth_headers_for):
    ctx = ready_s10
    response = _overview(client, auth_headers_for, ctx['doctor'])
    assert response.status_code == 200
    data = response.json['data']
    assert data['clinic_id'] == ctx['clinic'].id
    assert data['scope'] == 'assigned_cases' and data['total'] == 1
    assert data['metrics']['planned'] == 1
    assert data['metrics']['sealed_documentation'] == 1
    assert data['metrics']['reviewed_pacu_preparations'] == 1
    assert data['metrics']['dual_signed_reports'] == 1
    assert data['documentation_only'] is True
    assert data['operative_completion_authorized'] is False
    assert data['clinical_execution_authorized'] is False
    assert data['cases'][0]['case_id'] == ctx['case'].id
    assert data['cases'][0]['operative_report_status'] == 'dual_signed'


def test_surgeon_case_detail_is_correctly_scoped(ready_s10, client, auth_headers_for):
    ctx = ready_s10
    response = _detail(client, auth_headers_for, ctx['doctor'], ctx['case'].id)
    assert response.status_code == 200
    data = response.json['data']
    assert data['patient_id'] == ctx['patient'].id
    assert data['admission_id'] == ctx['admission'].id
    assert data['procedure_order_id'] == ctx['order'].id
    assert data['signature_roles'] == ['anesthetist', 'surgeon']
    assert data['material_trace_count'] == 0
    assert data['clinical_reference_count'] == 0
    assert data['operative_completion_authorized'] is False
    assert data['pacu_arrival_or_transfer_authorized'] is False
    assert data['sign_out_authorized'] is False
    assert data['medication_administration_authorized'] is False
    assert data['billing_clearance_authorized'] is False


def test_clinic_admin_can_view_clinic_dashboard(ready_s10, client, auth_headers_for):
    response = _overview(client, auth_headers_for, ready_s10['admin'])
    assert response.status_code == 200
    assert response.json['data']['scope'] == 'clinic'
    assert response.json['data']['total'] == 1


def test_clinic_admin_can_view_case_detail(ready_s10, client, auth_headers_for):
    response = _detail(client, auth_headers_for, ready_s10['admin'], ready_s10['case'].id)
    assert response.status_code == 200
    assert response.json['data']['case_id'] == ready_s10['case'].id


def test_assigned_counting_nurse_can_view(ready_s10, client, auth_headers_for):
    response = _overview(client, auth_headers_for, ready_s10['nurse'])
    assert response.status_code == 200
    assert response.json['data']['total'] == 1


def test_assigned_anesthetist_can_view(ready_s10, client, auth_headers_for):
    response = _detail(client, auth_headers_for, ready_s10['anesthetist'], ready_s10['case'].id)
    assert response.status_code == 200


def test_receiving_pacu_nurse_can_view(ready_s10, client, auth_headers_for):
    response = _detail(client, auth_headers_for, ready_s10['receiving'], ready_s10['case'].id)
    assert response.status_code == 200


def test_unassigned_same_clinic_doctor_sees_no_case(ready_s10, client, auth_headers_for):
    response = _overview(client, auth_headers_for, ready_s10['reviewer'])
    assert response.status_code == 200
    assert response.json['data']['total'] == 0
    assert response.json['data']['cases'] == []
    assert response.json['data']['metrics']['dual_signed_reports'] == 0


def test_unassigned_same_clinic_doctor_cannot_read_detail(ready_s10, client, auth_headers_for):
    response = _detail(client, auth_headers_for, ready_s10['reviewer'], ready_s10['case'].id)
    assert response.status_code == 404
    assert 'patient_id' not in response.json


def test_new_unassigned_clinic_nurse_cannot_read_detail(ready_s10, make_staff, client, auth_headers_for):
    outsider = make_staff(ready_s10['clinic'], role=Role.NURSE)
    response = _detail(client, auth_headers_for, outsider, ready_s10['case'].id)
    assert response.status_code == 404


def test_unauthenticated_dashboard_denied(client):
    response = client.get(BASE)
    assert response.status_code == 401


def test_unauthenticated_detail_denied(client):
    response = client.get(BASE + '/cases/1')
    assert response.status_code == 401


def test_patient_role_is_denied(ready_s10, make_user, client, auth_headers_for):
    patient_user = make_user(ready_s10['clinic'], role=Role.PATIENT)
    response = client.get(BASE, headers=auth_headers_for(patient_user))
    assert response.status_code == 403


def test_pharmacist_role_is_denied(ready_s10, make_staff, client, auth_headers_for):
    pharmacist = make_staff(ready_s10['clinic'], role=Role.PHARMACIST)
    response = _overview(client, auth_headers_for, pharmacist)
    assert response.status_code == 403


def test_receptionist_role_is_denied(ready_s10, make_staff, client, auth_headers_for):
    person = make_staff(ready_s10['clinic'], role=Role.RECEPTIONIST)
    assert _overview(client, auth_headers_for, person).status_code == 403


def test_lab_technician_role_is_denied(ready_s10, make_staff, client, auth_headers_for):
    person = make_staff(ready_s10['clinic'], role=Role.LAB_TECHNICIAN)
    assert _overview(client, auth_headers_for, person).status_code == 403


def test_suspended_clinician_access_denied(ready_s10, client, auth_headers_for):
    person = ready_s10['doctor']
    headers = _headers(auth_headers_for, person)
    person.status = StaffStatus.SUSPENDED
    db.session.flush()
    assert client.get(BASE, headers=headers).status_code == 403


def test_inactive_user_cannot_view(ready_s10, client, auth_headers_for):
    person = ready_s10['doctor']
    headers = _headers(auth_headers_for, person)
    person.user.is_active = False
    db.session.flush()
    assert client.get(BASE, headers=headers).status_code in (401, 403, 404)


def test_foreign_clinic_admin_sees_only_own_empty_dashboard(ready_s10, make_clinic, make_staff, client, auth_headers_for):
    foreign = make_clinic(name='S11 separate tenant')
    admin = make_staff(foreign, role=Role.ADMIN)
    response = _overview(client, auth_headers_for, admin)
    assert response.status_code == 200
    assert response.json['data']['clinic_id'] == foreign.id
    assert response.json['data']['total'] == 0


def test_foreign_clinic_admin_cannot_read_patient_case(ready_s10, make_clinic, make_staff, client, auth_headers_for):
    foreign = make_clinic(name='S11 foreign')
    admin = make_staff(foreign, role=Role.ADMIN)
    assert _detail(client, auth_headers_for, admin, ready_s10['case'].id).status_code == 404


def test_service_cross_clinic_user_context_is_rejected(ready_s10, make_clinic):
    foreign = make_clinic(name='S11 direct foreign context')
    with pytest.raises(NotFoundError):
        svc.get_surgery_case_read_model(actor_user_id=ready_s10['doctor'].user_id,
            clinic_id=foreign.id, case_id=ready_s10['case'].id)


def test_service_invalid_actor_and_case_ids_are_rejected(ready_s10):
    with pytest.raises(ValidationError):
        svc.get_surgery_case_read_model(actor_user_id=True,
            clinic_id=ready_s10['clinic'].id,case_id=ready_s10['case'].id)
    with pytest.raises(ValidationError):
        svc.get_surgery_case_read_model(actor_user_id=ready_s10['doctor'].user_id,
            clinic_id=ready_s10['clinic'].id,case_id=0)


def test_unknown_case_is_not_exposed(ready_s10, client, auth_headers_for):
    assert _detail(client, auth_headers_for, ready_s10['admin'], 999999).status_code == 404


@pytest.mark.parametrize('query', ('?limit=0','?limit=51','?limit=-1','?limit=9999'))
def test_invalid_dashboard_limit_rejected(ready_s10, client, auth_headers_for, query):
    assert _overview(client, auth_headers_for, ready_s10['doctor'], query).status_code == 422


@pytest.mark.parametrize('query', ('?offset=-1','?offset=5001','?offset=xyz','?offset=1.5'))
def test_invalid_dashboard_offset_rejected(ready_s10, client, auth_headers_for, query):
    assert _overview(client, auth_headers_for, ready_s10['doctor'], query).status_code == 422


def test_limit_one_and_offset_zero(ready_s10, client, auth_headers_for):
    response = _overview(client, auth_headers_for, ready_s10['doctor'], '?limit=1&offset=0')
    assert response.status_code == 200
    assert len(response.json['data']['cases']) == 1


def test_pagination_does_not_expose_extra_cases(ready_s10, client, auth_headers_for):
    response = _overview(client, auth_headers_for, ready_s10['doctor'], '?limit=1&offset=1')
    assert response.status_code == 200
    assert response.json['data']['total'] == 1
    assert response.json['data']['cases'] == []


def test_all_surgery_api_mutations_are_not_registered(ready_s10, client, auth_headers_for):
    h = _headers(auth_headers_for, ready_s10['doctor'])
    for path in (BASE, BASE + f"/cases/{ready_s10['case'].id}"):
        assert client.post(path, json={}, headers=h).status_code == 405
        assert client.patch(path, json={}, headers=h).status_code == 405
        assert client.delete(path, headers=h).status_code == 405


def test_dashboard_is_read_only_and_does_not_complete_surgery(ready_s10, client, auth_headers_for):
    ctx = ready_s10
    before = (ctx['case'].status, ctx['order'].status, ctx['admission'].status,
              ctx['report'].status, ctx['journal'].status, ctx['pacu_packet'].status)
    assert _overview(client, auth_headers_for, ctx['doctor']).status_code == 200
    assert _detail(client, auth_headers_for, ctx['doctor'], ctx['case'].id).status_code == 200
    assert before == (ctx['case'].status, ctx['order'].status, ctx['admission'].status,
                      ctx['report'].status, ctx['journal'].status, ctx['pacu_packet'].status)
    assert db.session.query(NursingHandoff).count() == 0
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_tampered_journal_fails_closed(ready_s10, client, auth_headers_for):
    ready_s10['journal'].source_sha256 = 'f' * 64
    db.session.flush()
    response = _detail(client, auth_headers_for, ready_s10['doctor'], ready_s10['case'].id)
    assert response.status_code == 409


def test_tampered_pacu_packet_fails_closed(ready_s10, client, auth_headers_for):
    ready_s10['pacu_packet'].content_sha256 = 'f' * 64
    db.session.flush()
    response = _detail(client, auth_headers_for, ready_s10['doctor'], ready_s10['case'].id)
    assert response.status_code == 409


def test_tampered_operative_report_fails_closed(ready_s10, client, auth_headers_for):
    ready_s10['report'].content_sha256 = 'f' * 64
    db.session.flush()
    response = _detail(client, auth_headers_for, ready_s10['doctor'], ready_s10['case'].id)
    assert response.status_code == 409


def test_clinical_reference_count_uses_existing_s10_source(ready_s10, make_invoice, client, auth_headers_for):
    ctx = ready_s10
    invoice = make_invoice(ctx['clinic'], ctx['patient'])
    _ref(ctx,'billing',invoice.id)
    response = _detail(client, auth_headers_for, ctx['doctor'], ctx['case'].id)
    assert response.status_code == 200
    assert response.json['data']['clinical_reference_count'] == 1
    assert db.session.query(SurgicalClinicalReference).count() == 1


def test_case_without_journal_displays_incomplete_documentation(scenario, client, auth_headers_for):
    response = _detail(client, auth_headers_for, scenario['doctor'], scenario['case'].id)
    assert response.status_code == 200
    data = response.json['data']
    assert data['journal_status'] == 'not_started'
    assert data['operative_report_status'] == 'not_started'
    assert data['material_trace_count'] == 0
    assert data['clinical_reference_count'] == 0
    assert data['operative_completion_authorized'] is False


def test_case_before_journal_is_visible_to_creator(scenario, client, auth_headers_for):
    response = _overview(client, auth_headers_for, scenario['doctor'])
    assert response.status_code == 200
    assert response.json['data']['total'] == 1
    assert response.json['data']['metrics']['dual_signed_reports'] == 0


def test_service_rejects_boolean_paging(ready_s10):
    ctx = ready_s10
    with pytest.raises(ValidationError):
        svc.get_surgery_dashboard(actor_user_id=ctx['doctor'].user_id,
            clinic_id=ctx['clinic'].id, limit=True)


def test_case_detail_is_pure_read_without_surgical_case_version_change(ready_s10, client, auth_headers_for):
    ctx = ready_s10
    before = ctx['case'].version
    assert _detail(client, auth_headers_for, ctx['doctor'], ctx['case'].id).status_code == 200
    assert ctx['case'].version == before
    assert db.session.query(SurgicalCase).count() == 1
