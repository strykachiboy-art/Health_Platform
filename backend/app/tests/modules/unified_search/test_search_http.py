"""Real HTTP / JWT / ORM tests, on SQLite and disposable PostgreSQL."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership, ClinicMembership
from app.modules.unified_search.contracts import RANKS, available_resources

URL = '/api/v1/search'


def grant(db, user, clinic):
    db.session.add(TenantMembership(user_id=user.id, organization_id=clinic.organization_id, role='member', status='active', is_active=True))
    db.session.flush()
    db.session.add(ClinicMembership(user_id=user.id, organization_id=clinic.organization_id, clinic_id=clinic.id, role=user.role.value, is_active=True))
    db.session.flush()


def headers(user):
    return {'Authorization': 'Bearer ' + issue_auth_tokens(user)['access_token']}


@pytest.fixture
def world(app, db, make_clinic, make_staff, make_user, make_patient):
    app.config['UNIFIED_CLINICAL_SEARCH_ENABLED'] = True
    a = make_clinic(name='Search clinic')
    same = make_clinic(name='Other clinic same tenant')
    org = HospitalOrganization(id=uuid4(), name='Other tenant')
    db.session.add(org)
    db.session.flush()
    foreign = make_clinic(name='Other tenant clinic', organization_id=org.id)
    roles = [Role.DOCTOR, Role.NURSE, Role.ADMIN, Role.RECEPTIONIST, Role.LAB_TECHNICIAN, Role.PHARMACIST, Role.PARAMEDIC, Role.EMT]
    users = {r.value: make_staff(a, role=r).user for r in roles}
    users['other_doctor'] = make_staff(a, role=Role.DOCTOR).user
    users['no_staff'] = make_user(a, role=Role.DOCTOR)
    users['patient'] = make_user(a, role=Role.PATIENT)
    users['accountant'] = make_staff(a, role=Role.ACCOUNTANT).user
    users['same'] = make_staff(same, role=Role.DOCTOR).user
    users['foreign'] = make_staff(foreign, role=Role.DOCTOR).user
    users['sa'] = make_user(role=Role.SUPER_ADMIN)
    for key, u in users.items():
        if key != 'sa':
            grant(db, u, same if key == 'same' else foreign if key == 'foreign' else a)
    p = make_patient(a, first_name='Ada', last_name='Synthetic', email='synthetic-search@example.invalid', phone='5551234')
    p2 = make_patient(a, first_name='Ada', last_name='Second', is_active=False)
    p_same = make_patient(same, first_name='Ada', last_name='SameTenant')
    p_foreign = make_patient(foreign, first_name='Ada', last_name='ForeignTenant')
    db.session.commit()
    return dict(a=a, same=same, foreign=foreign, users=users, p=p, p2=p2, p_same=p_same, p_foreign=p_foreign)


def read(client, h, **body):
    r = client.post(URL, headers=h, json={'q': 'Ada', **body})
    assert r.status_code == 200, r.get_data(as_text=True)[:1000]
    assert 'no-store' in r.headers['Cache-Control']
    assert r.json['success'] is True
    return r.json['data']


def test_feature_hold_and_denials(client, world, app):
    users = world['users']
    h = headers(users['doctor'])
    assert client.post(URL, json={'q':'Ada'}).status_code == 401
    for name in ['patient', 'sa', 'accountant', 'no_staff']:
        assert client.post(URL, headers=headers(users[name]), json={'q':'Ada'}).status_code == 403
    assert client.get(URL, headers=h).status_code == 405
    assert client.post(URL, headers=h | {'X-Emergency-Access-ID':'1'}, json={'q':'Ada'}).status_code == 403
    assert client.post(URL+'?clinic_id=1', headers=h, json={'q':'Ada'}).status_code == 400
    assert client.post(URL, headers=h, json={'q':'Ada','clinic_id':world['same'].id}).status_code == 400
    app.config['UNIFIED_CLINICAL_SEARCH_ENABLED'] = False
    assert client.post(URL, headers=h, json={'q':'Ada'}).status_code == 404
    app.config['UNIFIED_CLINICAL_SEARCH_ENABLED'] = True
    app.config['TESTING'] = False
    try:
        assert client.post(URL, headers=h, json={'q':'Ada'}).status_code == 404
    finally:
        app.config['TESTING'] = True


def test_identity_scope_status_and_literal_wildcards(client, world):
    w = world
    h = headers(w['users']['doctor'])
    rows = read(client, h, resource_types=['patient'])['items']
    assert {r['patient_id'] for r in rows} == {w['p'].id,w['p2'].id}
    assert all(r['kind']=='patient' and r['record_id']==r['patient_id'] and r['admission_id'] is None for r in rows)
    for q in ['Ada Synthetic','synthetic-search@example.invalid','5551234',w['p'].patient_number,f"#{w['p'].id}"]:
        result = read(client,h,q=q,resource_types=['patient'])['items']
        assert [r['patient_id'] for r in result] == [w['p'].id]
        assert 'email' not in result[0] and 'phone' not in result[0]
    assert read(client,h,q='%%')['items']==[]
    assert read(client,h,q='__')['items']==[]
    assert read(client,h,q="' OR 1=1 --")['items']==[]
    assert [r['patient_id'] for r in read(client,h,resource_types=['patient'],status='inactive')['items']]==[w['p2'].id]
    for name,p in [('same',w['p_same']),('foreign',w['p_foreign'])]:
        assert [r['patient_id'] for r in read(client,headers(w['users'][name]),resource_types=['patient'])['items']]==[p.id]
    assert read(client,h,resource_types=['patient'])['organization_id']==str(w['a'].organization_id)
    assert read(client,h,resource_types=['patient'])['clinic_id']==w['a'].id


def test_session_and_membership_revocation(client,world,db):
    w=world; user=w['users']['doctor']; h=headers(user)
    assert client.post(URL,headers={'Authorization':'Bearer '+issue_auth_tokens(user,auto_assign_clinic=False)['access_token']},json={'q':'Ada'}).status_code==403
    h=headers(user);user.staff.status=StaffStatus.SUSPENDED;db.session.commit()
    assert client.post(URL,headers=h,json={'q':'Ada'}).status_code==403
    user.staff.status=StaffStatus.ACTIVE;db.session.commit();h=headers(user)
    membership=db.session.query(ClinicMembership).filter_by(user_id=user.id,clinic_id=w['a'].id).one();membership.is_active=False;db.session.commit()
    assert client.post(URL,headers=h,json={'q':'Ada'}).status_code==401


def test_pagination_and_scope_bound_cursor(client,world,db,make_appointment):
    w=world;a=w['a'];p=w['p'];staff=w['users']['doctor'].staff
    for _ in range(3):make_appointment(a,p,staff)
    # Inconsistent cross-clinic patient and staff linkage must not leak.
    make_appointment(a,w['p_same'],staff)
    make_appointment(a,p,w['users']['foreign'].staff)
    db.session.commit();h=headers(w['users']['doctor'])
    complete=read(client,h,limit=50)['items']
    assert len(complete)==5 and all(r['patient_id'] in {p.id,w['p2'].id} for r in complete)
    assert complete==sorted(complete,key=lambda r:(RANKS[r['resource_type']],-r['record_id']))
    got=[];cursor=None
    while True:
        data=read(client,h,limit=2,**({'cursor':cursor} if cursor else {}));got+=data['items'];cursor=data['next_cursor']
        assert len(got)<=len(complete)
        if cursor is None:break
    assert got==complete
    cursor=read(client,h,limit=1)['next_cursor'];assert cursor
    for changed in [{'q':'Different'},{'limit':2},{'status':'active'},{'resource_types':['patient']},{'cursor':cursor+'x'}]:
        assert client.post(URL,headers=h,json={'q':'Ada','limit':1,'cursor':cursor,**changed}).status_code==400
    for who in ['other_doctor','same','foreign']:
        assert client.post(URL,headers=headers(w['users'][who]),json={'q':'Ada','limit':1,'cursor':cursor}).status_code==400
    assert client.post(URL,headers=headers(w['users']['doctor']),json={'q':'Ada','limit':1,'cursor':cursor}).status_code==400


def test_real_sources_and_role_matrix(client,world,db,make_appointment,make_lab_test,make_lab_order,make_prescription,make_prescription_item,make_drug,make_ward,make_bed):
    from app.modules.ward.models.ward_model import Admission
    from app.core.enums.ward_enums import AdmissionStatus
    from app.modules.emar.models.emar_model import MedicationAdministration
    from app.core.enums.emar_enums import MedicationAdministrationStatus as MS
    from app.modules.nursing.models.nursing_model import NursingCarePlan,NursingCareTask
    from app.modules.clinical_orders.models.imaging_order_model import ImagingOrder
    from app.modules.clinical_orders.models.procedure_order_model import ProcedureOrder
    from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder
    from app.modules.clinical_orders.models.diet_order_model import DietOrder
    from app.modules.clinical_orders.models.nursing_order_model import NursingOrder
    w=world;a=w['a'];p=w['p'];s=w['users']['doctor'].staff;n=w['users']['nurse'].staff
    t=datetime(2026,1,2,12,tzinfo=timezone.utc);naive=t.replace(tzinfo=None)
    appointment=make_appointment(a,p,s,reason='PRIVATE_NARRATIVE')
    lab=make_lab_order(a,p,s,[make_lab_test(a)])
    rx=make_prescription(a,p,s,notes='PRIVATE_NARRATIVE');item=make_prescription_item(rx,make_drug(a))
    adm=Admission(patient_id=p.id,bed_id=make_bed(make_ward(a)).id,admitted_by_id=s.id,status=AdmissionStatus.ADMITTED,admitted_at=naive)
    db.session.add(adm);db.session.flush()
    emar=MedicationAdministration(clinic_id=a.id,patient_id=p.id,admission_id=adm.id,prescription_id=rx.id,prescription_item_id=item.id,status=MS.SCHEDULED,scheduled_at=t)
    plan=NursingCarePlan(clinic_id=a.id,patient_id=p.id,admission_id=adm.id,created_by_staff_id=n.id,title='Synthetic',problem='Synthetic',goal='Synthetic',started_at=naive)
    db.session.add_all([emar,plan]);db.session.flush()
    task=NursingCareTask(clinic_id=a.id,patient_id=p.id,admission_id=adm.id,care_plan_id=plan.id,created_by_staff_id=n.id,description='PRIVATE_NARRATIVE')
    order=dict(clinic_id=a.id,patient_id=p.id,admission_id=adm.id,ordered_by_staff_id=s.id,ordered_at=t)
    imaging=ImagingOrder(**order,indication='PRIVATE_NARRATIVE',study_name='Synthetic',modality='ct',body_site='head')
    procedure=ProcedureOrder(**order,indication='PRIVATE_NARRATIVE',procedure_name='Synthetic',procedure_type='surgical')
    blood=BloodProductOrder(**order,product_type='packed_red_blood_cells',units_requested=1,indication='PRIVATE_NARRATIVE',patient_blood_type_at_order='O+')
    diet=DietOrder(**order,diet_type='regular',feeding_route='oral')
    nursing=NursingOrder(**order,category='monitoring',frequency='daily',order_text='PRIVATE_NARRATIVE')
    db.session.add_all([task,imaging,procedure,blood,diet,nursing]);db.session.commit()
    records=dict(patient=p,appointment=appointment,lab_order=lab,prescription=rx,imaging_order=imaging,procedure_order=procedure,blood_product_order=blood,diet_order=diet,nursing_order=nursing,nursing_task=task,medication_administration=emar)
    for role in ['doctor','nurse','admin','receptionist','lab_technician','pharmacist','paramedic','emt']:
        h=headers(w['users'][role]);result=read(client,h,q='Ada Synthetic',limit=50)
        allowed=available_resources(role)
        assert result['available_resources']==allowed
        assert {r['resource_type']:r['record_id'] for r in result['items']}=={k:v.id for k,v in records.items() if k in allowed}
        assert all(r['kind']==('patient' if r['resource_type']=='patient' else 'operational') for r in result['items'])
        assert all(r['patient_id']==p.id and r['status'] in allowed[r['resource_type']] for r in result['items'])
        assert 'PRIVATE_NARRATIVE' not in str(result)
        denied=set(records)-set(allowed)
        for k in denied:
            assert client.post(URL,headers=h,json={'q':'Ada','resource_types':[k]}).status_code==403
    h=headers(w['users']['doctor'])
    for k,r in records.items():
        result=read(client,h,q=f'#{r.id}',resource_types=[k])['items']
        assert [v['record_id'] for v in result]==[r.id]
    assert read(client,h,resource_types=['medication_administration'],status='scheduled')['items'][0]['admission_id']==adm.id
    assert read(client,h,q='PRIVATE_NARRATIVE')['items']==[]
    assert client.post(URL,headers=h,json={'q':'Ada','resource_types':['patient'],'status':'scheduled'}).status_code==400
    assert db.session.query(MedicationAdministration).count()==1

    # Independent policy oracle: never infer the Admin boundary from the
    # implementation's own available_resources() mapping.
    h = headers(w['users']['admin'])
    allowed_admin = {'patient', 'appointment', 'lab_order'}
    result = read(client, h, q='Ada Synthetic', limit=50)
    assert set(result['available_resources']) == allowed_admin
    assert {r['resource_type']: r['record_id'] for r in result['items']} == {
        k: records[k].id for k in allowed_admin
    }
    for k in set(records) - allowed_admin:
        for requested in ([k], ['patient', k]):
            denied = client.post(URL, headers=h, json={
                'q': f'#{records[k].id}', 'resource_types': requested,
            })
            assert denied.status_code == 403
            assert denied.json == {'success': False, 'error': 'Search access denied'}
    assert all(r['resource_type'] in allowed_admin for r in
               read(client, h, q=f'#{imaging.id}')['items'])
