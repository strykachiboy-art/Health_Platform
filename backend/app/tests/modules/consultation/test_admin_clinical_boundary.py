"""Real Flask, synthetic tenant grants, signed tokens and role-gate responses.
No mocked authentication, no production data or external radiology systems.
"""
from uuid import uuid4
import re
import pytest
from flask_jwt_extended import decode_token
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.enums.role_enums import Role
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership, ClinicMembership
from app.tests.modules.nursing.test_nursing_service_invariants import _setup_admission
from app.tests.modules.clinical_orders.test_unified_order_read_model import _seed_all_domains

ROUTES = {
    'changed_clinical_orders': (
        ('GET', '/api/v1/clinical-orders/imaging/<int:imaging_order_id>'),
        ('GET', '/api/v1/clinical-orders/imaging'),
        ('GET', '/api/v1/clinical-orders/procedures/<int:procedure_order_id>'),
        ('GET', '/api/v1/clinical-orders/procedures'),
        ('GET', '/api/v1/clinical-orders/blood-products/<int:blood_product_order_id>'),
        ('GET', '/api/v1/clinical-orders/blood-products'),
        ('GET', '/api/v1/clinical-orders/diets/<int:diet_order_id>'),
        ('GET', '/api/v1/clinical-orders/diets'),
        ('GET', '/api/v1/clinical-orders/nursing-orders/<int:nursing_order_id>'),
        ('GET', '/api/v1/clinical-orders/nursing-orders'),
        ('GET', '/api/v1/clinical-orders/nursing-orders/<int:nursing_order_id>/executions'),
        ('GET', '/api/v1/clinical-orders/patients/<int:patient_id>'),
    ),
    'changed_consultation': (
        ('POST', '/api/v1/consultations/'),
        ('PATCH', '/api/v1/consultations/<int:consultation_id>'),
        ('POST', '/api/v1/consultations/<int:consultation_id>/complete'),
        ('POST', '/api/v1/consultations/<int:consultation_id>/cancel'),
        ('POST', '/api/v1/consultations/templates'),
    ),
    'changed_patient': (
        ('POST', '/api/v1/patients/<int:patient_id>/vitals'),
        ('POST', '/api/v1/patients/<int:patient_id>/allergies'),
        ('PATCH', '/api/v1/patients/<int:patient_id>/allergies/<int:allergy_id>'),
        ('PATCH', '/api/v1/patients/<int:patient_id>/allergies/<int:allergy_id>/status'),
    ),
    'existing_clinical_orders': (
        ('POST', '/api/v1/clinical-orders/imaging'),
        ('POST', '/api/v1/clinical-orders/imaging/<int:imaging_order_id>/cancel'),
        ('POST', '/api/v1/clinical-orders/imaging/<int:imaging_order_id>/complete'),
        ('POST', '/api/v1/clinical-orders/procedures'),
        ('POST', '/api/v1/clinical-orders/procedures/<int:procedure_order_id>/verify-consent'),
        ('POST', '/api/v1/clinical-orders/procedures/<int:procedure_order_id>/cancel'),
        ('POST', '/api/v1/clinical-orders/procedures/<int:procedure_order_id>/complete'),
        ('POST', '/api/v1/clinical-orders/blood-products'),
        ('POST', '/api/v1/clinical-orders/blood-products/<int:blood_product_order_id>/verify-compatibility'),
        ('POST', '/api/v1/clinical-orders/blood-products/<int:blood_product_order_id>/fulfill'),
        ('POST', '/api/v1/clinical-orders/blood-products/<int:blood_product_order_id>/emergency-review'),
        ('POST', '/api/v1/clinical-orders/blood-products/<int:blood_product_order_id>/units/<int:issued_unit_id>/lab-followup'),
        ('POST', '/api/v1/clinical-orders/blood-products/<int:blood_product_order_id>/cancel'),
        ('POST', '/api/v1/clinical-orders/diets'),
        ('POST', '/api/v1/clinical-orders/diets/<int:diet_order_id>/complete'),
        ('POST', '/api/v1/clinical-orders/diets/<int:diet_order_id>/cancel'),
        ('POST', '/api/v1/clinical-orders/nursing-orders'),
        ('POST', '/api/v1/clinical-orders/nursing-orders/<int:nursing_order_id>/execute'),
        ('POST', '/api/v1/clinical-orders/nursing-orders/<int:nursing_order_id>/complete'),
        ('POST', '/api/v1/clinical-orders/nursing-orders/<int:nursing_order_id>/cancel'),
    ),
    'existing_consultation': (
        ('POST', '/api/v1/consultations/<int:consultation_id>/finalize'),
        ('POST', '/api/v1/consultations/<int:consultation_id>/amend'),
    ),
    'existing_lab': (
        ('POST', '/api/v1/lab/orders'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/collect-sample'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/equipment'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/process'),
        ('POST', '/api/v1/lab/order-items/<int:order_item_id>/result'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/verify'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/complete'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/cancel'),
        ('GET', '/api/v1/lab/orders/<int:order_id>/patient-release-preview'),
        ('GET', '/api/v1/lab/orders/<int:order_id>/patient-release'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/patient-release'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/patient-release-withdrawal'),
        ('GET', '/api/v1/lab/me/released-results'),
        ('GET', '/api/v1/lab/me/released-results/<int:order_id>'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/patient-release-correction'),
        ('GET', '/api/v1/lab/orders/<int:order_id>/patient-replacement-preview'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/patient-replacement'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/patient-replacement-rejection'),
        ('GET', '/api/v1/lab/orders/<int:order_id>/patient-critical-release-preview'),
        ('POST', '/api/v1/lab/orders/<int:order_id>/patient-critical-release'),
        ('GET', '/api/v1/lab/me/released-results/<int:order_id>/pdf'),
    ),
    'existing_prescription': (
        ('POST', '/api/v1/prescriptions'),
        ('GET', '/api/v1/prescriptions/<int:prescription_id>'),
        ('GET', '/api/v1/prescriptions/patients/<int:patient_id>'),
        ('POST', '/api/v1/prescriptions/<int:prescription_id>/cancel'),
        ('POST', '/api/v1/prescriptions/<int:prescription_id>/complete'),
        ('POST', '/api/v1/prescriptions/interactions/check'),
    ),
    'existing_radiology': (
        ('POST', '/api/v1/radiology/exams'),
        ('GET', '/api/v1/radiology/exams/by-order/<int:imaging_order_id>'),
        ('GET', '/api/v1/radiology/exams/<int:exam_id>'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/schedule'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/schedule/cancel'),
        ('GET', '/api/v1/radiology/exams/<int:exam_id>/worklist'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/reports'),
        ('PATCH', '/api/v1/radiology/reports/<int:report_id>'),
        ('GET', '/api/v1/radiology/exams/<int:exam_id>/reports'),
        ('GET', '/api/v1/radiology/exams/<int:exam_id>/clinical-worklist'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/clinical-worklist'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/staged-study'),
        ('GET', '/api/v1/radiology/exams/<int:exam_id>/staged-study/<int:publication_id>/instance'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/staged-ct'),
        ('GET', '/api/v1/radiology/exams/<int:exam_id>/staged-ct/<int:publication_id>/viewer/<path:target>'),
        ('GET', '/api/v1/radiology/exams/<int:exam_id>/staged-report/<int:publication_id>/preview'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/staged-report/<int:publication_id>/freeze'),
        ('GET', '/api/v1/radiology/exams/<int:exam_id>/staged-report/<int:publication_id>'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/staged-report/<int:publication_id>/amend'),
        ('GET', '/api/v1/radiology/exams/<int:exam_id>/staged-report/<int:publication_id>/copy-preview'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/staged-report/<int:publication_id>/copy'),
        ('POST', '/api/v1/radiology/exams/<int:exam_id>/staged-report/<int:publication_id>/withdraw'),
        ('GET', '/api/v1/radiology/me/staged-reports'),
        ('GET', '/api/v1/radiology/me/staged-reports/<int:exam_id>'),
        ('GET', '/api/v1/radiology/me/staged-reports/<int:exam_id>/pdf'),
    ),
    'existing_surgery': (
        ('POST', '/api/v1/surgery/cases'),
        ('POST', '/api/v1/surgery/bookings'),
        ('PATCH', '/api/v1/surgery/bookings/<int:booking_id>'),
        ('POST', '/api/v1/surgery/bookings/<int:booking_id>/cancel'),
        ('POST', '/api/v1/surgery/cases/<int:case_id>/consents'),
        ('POST', '/api/v1/surgery/consents/<int:consent_id>/verify'),
        ('POST', '/api/v1/surgery/consents/<int:consent_id>/withdraw'),
        ('POST', '/api/v1/surgery/cases/<int:case_id>/checklists/<phase>'),
        ('POST', '/api/v1/surgery/cases/<int:case_id>/preanesthesia'),
        ('POST', '/api/v1/surgery/anesthesia/<int:assessment_id>/baselines'),
        ('POST', '/api/v1/surgery/anesthesia/<int:assessment_id>/attest'),
        ('POST', '/api/v1/surgery/reports'),
        ('PATCH', '/api/v1/surgery/reports/<int:report_id>'),
        ('POST', '/api/v1/surgery/reports/<int:report_id>/sign'),
    ),
    'existing_ward': (
        ('POST', '/api/v1/wards/admissions/<int:admission_id>/discharge'),
        ('POST', '/api/v1/wards/admissions/<int:admission_id>/discharge-summary/amend'),
        ('GET', '/api/v1/wards/admissions/<int:admission_id>/problem-context'),
    ),
}

@pytest.fixture
def world(app, db, make_clinic, make_staff, make_patient, make_consultation):
    org = HospitalOrganization(id=uuid4(), name='Admin boundary synthetic hospital')
    db.session.add(org)
    db.session.flush()
    clinic = make_clinic(organization_id=org.id, is_headquarters=True)
    patient, nurse, doctor, ward, bed, admission = _setup_admission(
        clinic=clinic, make_patient=make_patient, make_staff=make_staff,
    )
    admin = make_staff(clinic, role=Role.ADMIN)
    pharmacist = make_staff(clinic, role=Role.PHARMACIST)
    for staff in (admin, doctor, nurse, pharmacist):
        actor = staff.user
        db.session.add(TenantMembership(user_id=actor.id, organization_id=org.id,
                                        role='member', status='active', is_active=True))
        db.session.flush()
        db.session.add(ClinicMembership(user_id=actor.id, organization_id=org.id,
                                        clinic_id=clinic.id, role=actor.role.value, is_active=True))
    consultation = make_consultation(clinic, patient, doctor)
    second_patient = make_patient(clinic)
    foreign_org = HospitalOrganization(id=uuid4(), name='Admin boundary foreign hospital')
    db.session.add(foreign_org); db.session.flush()
    foreign_clinic = make_clinic(organization_id=foreign_org.id, is_headquarters=True)
    foreign_patient = make_patient(foreign_clinic)
    orders = _seed_all_domains({'patient':patient, 'doctor':doctor, 'nurse':nurse,
                               'admission':admission}, clinic)
    db.session.commit()
    def header(staff):
        with app.test_request_context():
            token = issue_auth_tokens(staff.user, clinic_context_id=clinic.id)['access_token']
            claims = decode_token(token)
        assert claims['clinic_context_id'] == clinic.id
        assert claims['tenant_id'] == str(org.id)
        assert claims['role'] == staff.user.role.value
        return {'Authorization':'Bearer '+token}
    return {'clinic':clinic, 'patient':patient, 'admission':admission, 'consultation':consultation,
            'second_patient':second_patient, 'foreign_patient':foreign_patient,
            'orders':orders, 'doctor_staff':doctor, 'nurse_staff':nurse, 'admin_staff':admin,
            'admin':header(admin), 'doctor':header(doctor), 'nurse':header(nurse),
            'pharmacist':header(pharmacist)}


def concrete(path, w, allergy_id=1):
    ids = {'patient_id':w['patient'].id, 'admission_id':w['admission'].id,
           'consultation_id':w['consultation'].id, 'imaging_order_id':w['orders']['imaging'].id,
           'procedure_order_id':w['orders']['procedure'].id, 'blood_product_order_id':w['orders']['blood'].id,
           'diet_order_id':w['orders']['diet'].id, 'nursing_order_id':w['orders']['nursing'].id,
           'prescription_id':w['orders']['prescription'].id, 'order_id':w['orders']['lab'].id,
           'allergy_id':allergy_id, 'target':'synthetic', 'phase':'sign_in'}
    return re.sub(r'<(?:[^:>]+:)?([^>]+)>',lambda m:str(ids.get(m.group(1),1)),path)


def deny(response, method, path):
    # Reject false-green 401, 404, 422 or unrelated 403 errors.
    assert response.status_code == 403, (method,path,response.status_code,response.get_json())
    assert response.get_json()['error'] == 'Insufficient permissions', (method,path,response.get_json())


@pytest.mark.parametrize('group', tuple(ROUTES))
def test_admin_denied_at_real_role_gate(client, world, group):
    w=world
    allergy=client.post(f"/api/v1/patients/{w['patient'].id}/allergies",
                        headers=w['doctor'],json={'allergen_name':'Synthetic allergen'})
    assert allergy.status_code==201, allergy.get_json()
    allergy_id=allergy.get_json()['data']['id']
    for method, template in ROUTES[group]:
        path=concrete(template,w,allergy_id)
        kwargs={'headers':w['admin']}
        if method!='GET':kwargs['json']={}
        deny(client.open(path,method=method,**kwargs),method,path)


@pytest.mark.parametrize('actor',['doctor','nurse'])
def test_clinical_order_read_remains_authorized(client, world, actor):
    w=world
    for method,path in ROUTES['changed_clinical_orders']:
        assert method=='GET'
        p=concrete(path,w)
        if path.endswith(('/imaging','/procedures','/blood-products','/diets','/nursing-orders')):
            p += '?admission_id='+str(w['admission'].id)
        response=client.get(p,headers=w[actor])
        assert response.status_code==200,(p,response.get_json())
        if '/patients/' in p:
            data=response.get_json()
            assert data['total']==7 and len(data['items'])==7
            assert any(item['order_type']=='imaging' for item in data['items'])


@pytest.mark.parametrize('actor',['doctor','nurse'])
def test_consultation_mutations_remain_authorized(client,db,world,actor):
    w=world; h=w[actor]; p='/api/v1/consultations/'
    created=client.post(p,headers=h,json={'patient_id':w['second_patient'].id,
                                         'staff_id':w[actor+'_staff'].id,
                                         'chief_complaint':'Synthetic symptom'})
    assert created.status_code==201, created.get_json()
    row=created.get_json()['data']; detail=p+str(row['id'])
    assert row['clinic_id']==w['clinic'].id
    updated=client.patch(detail,headers=h,json={'notes':'Synthetic note'})
    assert updated.status_code==200,updated.get_json()
    assert updated.get_json()['data']['notes']=='Synthetic note'
    complete=client.post(detail+'/complete',headers=h,json={'diagnosis':'Synthetic diagnosis'})
    assert complete.status_code==200,complete.get_json()
    assert complete.get_json()['data']['status']=='completed'
    # Existing role permits cancellation of a second in-progress consultation.
    cancelled=client.post(p+str(w['consultation'].id)+'/cancel',headers=h,json={'reason':'Synthetic cancellation'})
    assert cancelled.status_code==200,cancelled.get_json()
    assert cancelled.get_json()['data']['status']=='cancelled'


def test_patient_clinical_writes_preserve_clinicians(client,world):
    w=world; p=f"/api/v1/patients/{w['patient'].id}"
    for actor in ('doctor','nurse'):
        r=client.post(p+'/vitals',headers=w[actor],json={'heart_rate':70})
        assert r.status_code==201,r.get_json()
    for actor in ('doctor','nurse','pharmacist'):
        r=client.post(p+'/allergies',headers=w[actor],json={'allergen_name':'Synthetic '+actor})
        assert r.status_code==201,r.get_json()
        a=p+'/allergies/'+str(r.get_json()['data']['id'])
        r=client.patch(a,headers=w[actor],json={'reaction':'Synthetic reaction'})
        assert r.status_code==200,r.get_json()
        r=client.patch(a+'/status',headers=w[actor],json={'verification_status':'refuted','reason':'Synthetic correction'})
        assert r.status_code==200,r.get_json()
        assert r.get_json()['data']['verification_status']=='refuted'


def test_admin_retains_patient_directory_staff_and_departments(client,world):
    w=world
    for p in (f"/api/v1/patients/{w['patient'].id}",'/api/v1/staff','/api/v1/departments'):
        response=client.get(p,headers=w['admin'])
        assert response.status_code==200,(p,response.get_json())


def test_doctor_template_creation_remains_authorized(client,world):
    r=client.post('/api/v1/consultations/templates',headers=world['doctor'],
                  json={'name':'Synthetic clinical template','structure':{'sections':['history']}})
    assert r.status_code==201,r.get_json()


def test_foreign_order_feed_still_denied(client,world):
    path=f"/api/v1/clinical-orders/patients/{world['foreign_patient'].id}"
    r=client.get(path,headers=world['doctor'])
    assert r.status_code==404,r.get_json()
    deny(client.get(path,headers=world['admin']),'GET',path)
