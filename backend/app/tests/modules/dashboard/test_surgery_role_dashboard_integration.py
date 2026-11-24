"""S11V2 surgical widgets on existing dashboard contracts (no prior suite rerun)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.extensions import db
from app.modules.dashboard.schemas.surgery_dashboard_schema import SurgeryDashboardWidgetSchema
from app.modules.dashboard.services import dashboard_service
from app.modules.dashboard.services.surgery_dashboard_widget_service import (
    add_surgery_to_existing_dashboard, build_role_surgery_widget,
)
from app.modules.surgery.models.surgical_case_model import SurgicalCase
from app.modules.surgery.models.operative_report_model import SurgicalOperativeReport
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.tests.modules.surgery.test_surgical_authorization import scenario
from app.tests.modules.surgery.test_surgical_safety_checklist import ready
from app.tests.modules.surgery.test_anesthesia_foundation import anesthesia_ready
from app.tests.modules.surgery.test_intraoperative_evidence_foundation import ready_s06
from app.tests.modules.surgery.test_pacu_preparation_handoff import workspace as s08_workspace
from app.tests.modules.surgery.test_operative_report_signatures import ready_s09
from app.tests.modules.surgery.test_surgical_clinical_references import ready_s10, _ref


@pytest.mark.parametrize('key,view', (
    ('admin', 'management'), ('doctor', 'doctor'), ('nurse', 'nurse'),
))
def test_authorized_team_views_use_existing_case(ready_s10, key, view):
    ctx = ready_s10
    data = build_role_surgery_widget(ctx[key].user)
    assert data.view == view
    assert data.total_cases == 1
    assert data.planned == 1
    assert data.sealed_documentation == 1
    assert data.reviewed_pacu_preparations == 1
    assert data.dual_signed_reports == 1
    assert data.pending_signatures == 0
    assert len(data.cases) == 1
    assert data.cases[0].case_id == ctx['case'].id
    assert data.documentation_only is True
    assert data.operative_completion_authorized is False
    assert data.nursing_transfer_authorized is False


def test_assigned_roles_do_not_receive_other_patient_fields(ready_s10):
    item = build_role_surgery_widget(ready_s10['doctor'].user).model_dump(mode='json')['cases'][0]
    assert set(item) == {'case_id', 'case_status', 'documentation_status', 'pacu_preparation_status'}
    assert 'patient_id' not in item and 'admission_id' not in item


def test_unassigned_doctor_cannot_see_team_case(ready_s10):
    data = build_role_surgery_widget(ready_s10['reviewer'].user)
    assert data.view == 'doctor' and data.total_cases == 0
    assert data.cases == [] and data.dual_signed_reports == 0


def test_unassigned_nurse_cannot_see_team_case(ready_s10, make_staff):
    outsider = make_staff(ready_s10['clinic'], role=Role.NURSE)
    data = build_role_surgery_widget(outsider.user)
    assert data.view == 'nurse' and data.total_cases == 0 and data.cases == []


def test_suspended_staff_fails_closed(ready_s10):
    ready_s10['doctor'].status = StaffStatus.SUSPENDED
    db.session.flush()
    data = build_role_surgery_widget(ready_s10['doctor'].user)
    assert data.visibility == 'restricted' and data.cases == [] and data.total_cases == 0


def test_super_admin_sees_global_aggregates_without_identifiers(ready_s10, make_user):
    admin = make_user(None, role=Role.SUPER_ADMIN)
    data = build_role_surgery_widget(admin)
    assert data.view == 'super_admin' and data.scope == 'system_aggregate'
    assert data.total_cases >= 1 and data.cases == []
    assert data.visibility == 'aggregate_only'
    assert 'patient_id' not in str(data.model_dump())


def test_clinic_management_is_tenant_scoped(ready_s10, make_clinic, make_staff):
    clinic = make_clinic(name='S11V2 dashboard foreign tenant')
    admin = make_staff(clinic, role=Role.ADMIN)
    data = build_role_surgery_widget(admin.user)
    assert data.view == 'management' and data.total_cases == 0 and data.cases == []


def test_receptionist_receives_capacity_only(ready_s10, make_staff):
    receptionist = make_staff(ready_s10['clinic'], role=Role.RECEPTIONIST)
    data = build_role_surgery_widget(receptionist.user)
    assert data.view == 'operations' and data.scope == 'operational_aggregate'
    assert data.scheduled_bookings >= 1
    assert data.cases == [] and data.total_cases == 0


def test_driver_does_not_gain_clinical_access(ready_s10, make_staff):
    driver = make_staff(ready_s10['clinic'], role=Role.DRIVER)
    data = build_role_surgery_widget(driver.user)
    assert data.view == 'operations' and data.visibility == 'restricted'
    assert data.scheduled_bookings == 0 and data.cases == []


def test_finance_links_existing_invoice_without_exposing_notes(ready_s10, make_staff, make_invoice):
    ctx = ready_s10
    accountant = make_staff(ctx['clinic'], role=Role.ACCOUNTANT)
    before = build_role_surgery_widget(accountant.user)
    assert before.linked_billing_invoices == 0
    invoice = make_invoice(ctx['clinic'], ctx['patient'])
    _ref(ctx, 'billing', invoice.id)
    after = build_role_surgery_widget(accountant.user)
    assert after.view == 'finance' and after.linked_billing_invoices == 1
    assert after.cases == [] and after.total_cases == 0
    assert after.billing_clearance_authorized is False


def test_foreign_finance_cannot_count_other_clinic_invoices(ready_s10, make_staff, make_clinic, make_invoice):
    ctx = ready_s10
    invoice = make_invoice(ctx['clinic'], ctx['patient'])
    _ref(ctx, 'billing', invoice.id)
    foreign = make_clinic(name='S11V2 foreign billing')
    accountant = make_staff(foreign, role=Role.ACCOUNTANT)
    assert build_role_surgery_widget(accountant.user).linked_billing_invoices == 0


def test_patient_sees_own_unreleased_summary_no_details(ready_s10, make_user):
    ctx = ready_s10
    patient_user = make_user(ctx['clinic'], role=Role.PATIENT)
    ctx['patient'].user_id = patient_user.id
    db.session.flush()
    data = build_role_surgery_widget(patient_user)
    assert data.view == 'patient' and data.scope == 'own_patient'
    assert data.total_cases == 1 and data.release_required is True
    assert data.visibility == 'release_required'
    assert data.cases == [] and data.dual_signed_reports == 0
    assert 'postoperative_diagnosis' not in str(data.model_dump())


def test_foreign_patient_sees_no_surgery(ready_s10, make_user, make_patient):
    ctx = ready_s10
    user = make_user(ctx['clinic'], role=Role.PATIENT)
    make_patient(ctx['clinic'], user_id=user.id)
    data = build_role_surgery_widget(user)
    assert data.total_cases == 0 and data.cases == [] and data.release_required is True


def test_other_clinical_roles_do_not_gain_surgery_records(ready_s10, make_staff):
    person = make_staff(ready_s10['clinic'], role=Role.PHARMACIST)
    data = build_role_surgery_widget(person.user)
    assert data.visibility == 'restricted' and data.total_cases == 0 and data.cases == []


def test_seven_role_schemas_are_strict_and_read_only():
    for view in ('super_admin','management','doctor','nurse','operations','finance','patient'):
        data = SurgeryDashboardWidgetSchema(view=view, scope='restricted', visibility='restricted')
        assert data.operative_completion_authorized is False
        assert data.medication_administration_authorized is False
    with pytest.raises(PydanticValidationError):
        SurgeryDashboardWidgetSchema(view='doctor', scope='restricted', visibility='restricted',
                                     operative_completion_authorized=True)
    with pytest.raises(PydanticValidationError):
        SurgeryDashboardWidgetSchema(view='doctor', scope='restricted', visibility='restricted', patient_id=42)


def test_original_dashboard_service_now_includes_surgery(ready_s10):
    actor = ready_s10['doctor'].user
    result = dashboard_service.get_dashboard(actor_id=actor.id)
    assert result.surgery.view == 'doctor'
    assert result.surgery.total_cases == 1
    assert any(x.key == 'surgery_documentation_cases' for x in result.metrics)
    assert result.model_dump(mode='json')['surgery']['view'] == 'doctor'


def test_admin_dashboard_has_clinic_surgery(ready_s10):
    actor = ready_s10['admin'].user
    result = dashboard_service.get_dashboard(actor_id=actor.id)
    assert result.surgery.view == 'management'
    assert result.surgery.scope == 'clinic'


def test_existing_dashboard_route_emits_surgery_widget(ready_s10, client, auth_headers_for):
    response = client.get('/api/v1/dashboard', headers=auth_headers_for(ready_s10['doctor'].user))
    assert response.status_code == 200
    surgery = response.json['data']['surgery']
    assert surgery['view'] == 'doctor' and surgery['total_cases'] == 1
    assert surgery['operative_completion_authorized'] is False


def test_projection_does_not_mutate_surgical_or_nursing_records(ready_s10):
    ctx = ready_s10
    before = (ctx['case'].status, ctx['case'].version, ctx['report'].status,
              ctx['journal'].status, ctx['pacu_packet'].status)
    for person in ('admin','doctor','nurse'):
        build_role_surgery_widget(ctx[person].user)
    assert before == (ctx['case'].status, ctx['case'].version, ctx['report'].status,
                      ctx['journal'].status, ctx['pacu_packet'].status)
    assert db.session.query(SurgicalCase).count() == 1
    assert db.session.query(SurgicalOperativeReport).count() == 1
    assert db.session.query(NursingHandoff).count() == 0
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_mocked_dashboard_dispatch_behavior_stays_intact(ready_s10):
    marker = object()
    assert add_surgery_to_existing_dashboard(marker, ready_s10['doctor'].user) is marker
