"""Synthetic authenticated export proof using the actual Flask/ORM contracts."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import os
import json
from pathlib import Path
import pytest

from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.lab_enums import LabOrderStatus
from app.core.audit.models.audit_model import AuditLog
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership, ClinicMembership
from app.modules.patient.models.patient_model import PatientAllergy, PatientVitals
from app.modules.ward.models.ward_model import Admission
from app.modules.lab.models.lab_model import LabTest, LabOrder, LabOrderItem
from app.modules.lab.services.lab_service import enter_result, verify_results, complete_order
from app.modules.hie.providers.registry import is_provider_registered, get_provider
from app.modules.hie.providers.exceptions import HIEFailClosedError


def _grant(db, user, clinic):
    db.session.add(TenantMembership(user_id=user.id, organization_id=clinic.organization_id,
        role='member', status='active', is_active=True))
    db.session.flush()
    db.session.add(ClinicMembership(user_id=user.id, organization_id=clinic.organization_id,
        clinic_id=clinic.id, role=user.role.value, is_active=True))
    db.session.flush()


def _header(user, **kwargs):
    return {'Authorization': 'Bearer ' + issue_auth_tokens(user, **kwargs)['access_token']}


@pytest.fixture
def export_world(app, db, make_clinic, make_staff, make_user, make_patient):
    org = HospitalOrganization(id=uuid4(), name='Synthetic foreign FHIR organization')
    db.session.add(org); db.session.flush()
    clinic = make_clinic(name='Synthetic FHIR clinic')
    foreign = make_clinic(name='Synthetic foreign FHIR clinic', organization_id=org.id)
    doctor = make_staff(clinic, role=Role.DOCTOR)
    nurse = make_staff(clinic, role=Role.NURSE)
    admin = make_staff(clinic, role=Role.ADMIN)
    tech = make_staff(clinic, role=Role.LAB_TECHNICIAN)
    other_doctor = make_staff(foreign, role=Role.DOCTOR)
    owner = make_user(clinic, role=Role.PATIENT)
    sa = make_user(role=Role.SUPER_ADMIN)
    for user, c in ((doctor.user,clinic),(nurse.user,clinic),(admin.user,clinic),(tech.user,clinic),
                    (other_doctor.user,foreign),(owner,clinic)):
        _grant(db,user,c)
    patient = make_patient(clinic, user_id=owner.id, chronic_conditions='PRIVATE LEGACY DIAGNOSIS')
    other_patient = make_patient(foreign)
    second = make_patient(clinic)
    db.session.commit()
    approval = (str(clinic.organization_id), clinic.id, doctor.user_id, patient.id)
    app.config.update(FHIR_EXPORT_ENABLED=True, FHIR_EXPORT_SYNTHETIC_APPROVALS=(approval,),
                      PATIENT_PROBLEM_LIST_ENABLED=True, PATIENT_LAB_RELEASE_ENABLED=True)
    return dict(clinic=clinic,foreign=foreign,doctor=doctor,nurse=nurse,admin=admin,tech=tech,other_doctor=other_doctor,
                owner=owner,sa=sa,patient=patient,other_patient=other_patient,second=second,
                approval=approval,headers=_header(doctor.user),client=app.test_client())


def _path(w, section=None, patient=None):
    return f"/api/v1/hie/fhir/export/patient/{(patient or w['patient']).id}" + (f'/{section}' if section else '')


def _get(w, section=None):
    return w['client'].get(_path(w, section), headers=w['headers'])


def _resources(response):
    assert response.status_code == 200, response.get_json()
    assert response.mimetype == 'application/fhir+json'
    assert 'no-store' in response.headers['Cache-Control']
    bundle = response.get_json()
    assert bundle['resourceType'] == 'Bundle' and bundle['type'] == 'collection' and 'total' not in bundle
    return [e['resource'] for e in bundle['entry']]


def test_registered_provider_stays_credential_held(app):
    assert is_provider_registered('fhir-r4')
    provider=get_provider('fhir-r4', endpoint='https://fhir.partnerhealth.org/fhir/r4')
    with pytest.raises(HIEFailClosedError,match='credential'):
        provider.query_patient('https://issuer.example.org/mrn|SYNTHETIC')


def test_full_chart_real_orm_and_audit(export_world, db, make_ward, make_bed, make_drug, make_prescription, make_prescription_item):
    w=export_world; p=w['patient']; c=w['clinic']; d=w['doctor']; now=datetime.now(timezone.utc)
    bed=make_bed(make_ward(c)); drug=make_drug(c)
    rx=make_prescription(c,p,d); make_prescription_item(rx,drug,dosage='5 mg',frequency='Daily')
    db.session.add_all([Admission(patient_id=p.id,bed_id=bed.id,admitted_by_id=d.id),
        PatientVitals(patient_id=p.id,recorded_by_id=d.id,temperature_c=37.1,heart_rate_bpm=80,
                      blood_pressure_systolic=120,blood_pressure_diastolic=80,weight_kg=70,height_cm=175,recorded_at=now),
        PatientAllergy(patient_id=p.id,clinic_id=c.id,allergen_name='Synthetic allergen',reaction='Recorded rash',
                       allergen_type='drug',severity='moderate',verification_status='confirmed',recorded_by_id=d.id)])
    db.session.commit()
    problem=w['client'].post(f'/api/v1/patients/{p.id}/problems',headers=w['headers'],json={
        'name':'Synthetic hypertension','category':'chronic','code':'I10','coding_system':'http://hl7.org/fhir/sid/icd-10',
        'clinical_status':'active','verification_status':'confirmed'})
    assert problem.status_code==201, problem.get_json()
    # Existing release services, not a fabricated release snapshot.
    test=LabTest(clinic_id=c.id,name='Synthetic test',loinc_code='2345-7',unit='mg/dL',reference_range='1 - 10')
    start=now-timedelta(minutes=5)
    order=LabOrder(clinic_id=c.id,patient_id=p.id,ordered_by_id=d.id,collected_by_id=w['tech'].id,
        processed_by_id=w['tech'].id,status=LabOrderStatus.IN_PROGRESS,created_at=start,
        sample_collected_at=start+timedelta(seconds=1),processed_at=start+timedelta(seconds=2))
    db.session.add_all([test,order]);db.session.flush()
    item=LabOrderItem(order_id=order.id,test_id=test.id);db.session.add(item);db.session.commit()
    enter_result(item.id,'5',None,None,None,c.id)
    verify_results(order.id,w['tech'].id,c.id);complete_order(order.id,c.id)
    assert {r['resourceType'] for r in _resources(_get(w,'diagnostics'))}=={'Patient'}
    preview=w['client'].get(f'/api/v1/lab/orders/{order.id}/patient-release-preview',headers=w['headers'])
    assert preview.status_code==200,preview.get_json()
    publish=w['client'].post(f'/api/v1/lab/orders/{order.id}/patient-release',headers=w['headers'],
        json={'preview_sha256':preview.get_json()['data']['preview_sha256']})
    assert publish.status_code==200,publish.get_json()
    res=_get(w); rows=_resources(res)
    target=os.environ.get('CSP_FHIR_R02_SYNTHETIC_BUNDLE_PATH')
    if target:
        Path(target).write_text(json.dumps(res.get_json(),indent=2)+'\n',encoding='utf-8')
    assert {r['resourceType'] for r in rows}=={'Patient','Encounter','Observation','AllergyIntolerance','MedicationRequest','Condition','DiagnosticReport'}
    assert 'PRIVATE LEGACY DIAGNOSIS' not in res.get_data(as_text=True)
    assert all(r.get('subject',r.get('patient',{})).get('reference')==f'Patient/{p.id}' for r in rows if r['resourceType']!='Patient')
    audit=db.session.query(AuditLog).filter_by(entity_type='FHIRExport',patient_id=p.id).order_by(AuditLog.id).all()
    assert len(audit)==2 and all(a.clinic_id==c.id and a.user_id==d.user_id for a in audit)
    assert audit[-1].new_value['resource_count']==len(rows)
    withdraw=w['client'].post(f'/api/v1/lab/orders/{order.id}/patient-release-withdrawal',headers=w['headers'],
        json={'reason_code':'clinical_correction'})
    assert withdraw.status_code==200,withdraw.get_json()
    assert {r['resourceType'] for r in _resources(_get(w,'diagnostics'))}=={'Patient'}


def test_role_and_clinic_and_approval_boundaries(export_world,db):
    w=export_world; client=w['client']; path=_path(w,'patient')
    assert client.get(path).status_code==401
    for actor in (w['nurse'].user,w['admin'].user,w['tech'].user,w['owner'],w['sa']):
        assert client.get(path,headers=_header(actor)).status_code==403
    assert client.get(path,headers=_header(w['other_doctor'].user)).status_code==404
    assert client.get(_path(w,'patient',w['other_patient']),headers=w['headers']).status_code==404
    assert client.get(_path(w,'patient',w['second']),headers=w['headers']).status_code==403
    assert client.get(path,headers=_header(w['doctor'].user,auto_assign_clinic=False)).status_code==403
    assert client.get(path+'?clinic_id=99',headers=w['headers']).status_code==400
    assert db.session.query(AuditLog).filter_by(entity_type='FHIRExport').count()==0
    _resources(_get(w,'patient'))


def test_feature_hold_and_synthetic_approval(export_world,app,db):
    w=export_world
    app.config['FHIR_EXPORT_ENABLED']=False
    assert _get(w,'patient').status_code==404
    app.config['FHIR_EXPORT_ENABLED']=True
    app.config['TESTING']=False
    try:
        assert _get(w,'patient').status_code==404
    finally:
        app.config['TESTING']=True
    app.config['FHIR_EXPORT_SYNTHETIC_APPROVALS']=()
    assert _get(w,'patient').status_code==403
    assert db.session.query(AuditLog).filter_by(entity_type='FHIRExport').count()==0


def test_revoked_membership_and_staff_suspension(export_world,db):
    w=export_world; doctor=w['doctor']; doctor.status=StaffStatus.SUSPENDED; db.session.commit()
    assert _get(w,'patient').status_code==403
    doctor.status=StaffStatus.ACTIVE; db.session.commit()
    grant=db.session.query(ClinicMembership).filter_by(user_id=doctor.user_id,clinic_id=w['clinic'].id).one()
    grant.is_active=False; db.session.commit()
    assert _get(w,'patient').status_code==401


def test_cross_clinic_source_and_overflow_fail_closed(export_world,db):
    w=export_world; p=w['patient']; foreign=w['other_doctor']; now=datetime.now(timezone.utc)
    row=PatientVitals(patient_id=p.id,recorded_by_id=foreign.id,heart_rate_bpm=70,recorded_at=now)
    db.session.add(row);db.session.commit()
    assert _get(w,'observations').status_code==409
    db.session.delete(row);db.session.commit()
    db.session.add_all([PatientVitals(patient_id=p.id,recorded_by_id=w['doctor'].id,heart_rate_bpm=70,
        recorded_at=now+timedelta(seconds=i)) for i in range(51)]);db.session.commit()
    assert _get(w,'observations').status_code==409
    assert db.session.query(AuditLog).filter_by(entity_type='FHIRExport').count()==0


def test_failed_audit_does_not_disclose_bundle(export_world,db,monkeypatch):
    from app.modules.hie.routes import fhir_export_route
    w=export_world
    def failed(**kwargs):
        raise ValueError('Synthetic audit failure')
    monkeypatch.setattr(fhir_export_route,'create_audit_log',failed)
    res=_get(w,'patient')
    assert res.status_code==409 and res.get_json()['resourceType']=='OperationOutcome'
    assert 'entry' not in res.get_json()
    assert db.session.query(AuditLog).filter_by(entity_type='FHIRExport').count()==0
