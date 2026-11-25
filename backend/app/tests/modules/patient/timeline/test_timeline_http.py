"""Real HTTP/JWT/ORM checks; no raw privileged fixture token."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.consultation_enums import ConsultationStatus
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership, ClinicMembership
from app.modules.patient.models.patient_model import PatientVitals
from app.modules.patient.problems.model import PatientProblem
from app.modules.patient.timeline.query import event_selects, page_statement
from app.modules.surgery.services.surgical_dashboard_read_service import _case_scope


def grant(db, user, clinic):
    db.session.add(TenantMembership(user_id=user.id,organization_id=clinic.organization_id,role='member',status='active',is_active=True))
    db.session.flush()
    db.session.add(ClinicMembership(user_id=user.id,organization_id=clinic.organization_id,clinic_id=clinic.id,role=user.role.value,is_active=True))
    db.session.flush()


def headers(user):
    return {'Authorization':'Bearer '+issue_auth_tokens(user)['access_token']}


@pytest.fixture
def world(app, db, make_clinic, make_staff, make_user, make_patient):
    app.config['PATIENT_CLINICAL_TIMELINE_ENABLED']=True
    app.config['PATIENT_PROBLEM_LIST_ENABLED']=True
    app.config['PATIENT_LAB_RELEASE_ENABLED']=True
    a=make_clinic(name='Journey clinic'); same=make_clinic(name='Same tenant other clinic')
    org=HospitalOrganization(id=uuid4(),name='Other tenant');db.session.add(org);db.session.flush()
    foreign=make_clinic(name='Foreign clinic',organization_id=org.id)
    users={name:make_staff(c,role=r).user for name,c,r in [('doctor',a,Role.DOCTOR),('doctor2',a,Role.DOCTOR),('nurse',a,Role.NURSE),('admin',a,Role.ADMIN),('pharmacist',a,Role.PHARMACIST),('same',same,Role.DOCTOR),('foreign',foreign,Role.DOCTOR)]}
    users['patient']=make_user(a,role=Role.PATIENT);users['sa']=make_user(role=Role.SUPER_ADMIN)
    for name,u in users.items():
        if name!='sa':grant(db,u,same if name=='same' else foreign if name=='foreign' else a)
    p=make_patient(a,user_id=users['patient'].id);other=make_patient(a);p_same=make_patient(same);p_foreign=make_patient(foreign)
    db.session.commit()
    return dict(a=a,same=same,foreign=foreign,users=users,p=p,other=other,p_same=p_same,p_foreign=p_foreign)


def url(w, patient=None):
    return f"/api/v1/patients/{(patient or w['p']).id}/clinical-timeline"


def read(client, url_, h, **query):
    res=client.get(url_,headers=h,query_string=query)
    assert res.status_code==200,res.get_data(as_text=True)[:1000]
    assert 'no-store' in res.headers['Cache-Control']
    return res.json['data']


def test_feature_hold_and_roles(client,world,app):
    w=world;h=headers(w['users']['doctor']);u=url(w)
    assert client.get(u).status_code==401
    for role in ['admin','pharmacist','patient','sa']:
        assert client.get(u,headers=headers(w['users'][role])).status_code==403
    assert client.get(u,headers=h|{'X-Emergency-Access-ID':'1'}).status_code==403
    assert client.post(u,headers=h,json={}).status_code==405
    app.config['PATIENT_CLINICAL_TIMELINE_ENABLED']=False
    assert client.get(u,headers=h).status_code==404
    app.config['PATIENT_CLINICAL_TIMELINE_ENABLED']=True;app.config['TESTING']=False
    try:assert client.get(u,headers=h).status_code==404
    finally:app.config['TESTING']=True


def test_scope_and_grant_revocation(client,world,db):
    w=world;h=headers(w['users']['doctor']);u=url(w)
    for name in ['same','foreign']:
        assert client.get(u,headers=headers(w['users'][name])).status_code==404
    for p in ['p_same','p_foreign']:
        assert client.get(url(w,w[p]),headers=h).status_code==404
    assert client.get(u,headers={'Authorization':'Bearer '+issue_auth_tokens(w['users']['doctor'],auto_assign_clinic=False)['access_token']}).status_code==403
    h=headers(w['users']['doctor']);staff=w['users']['doctor'].staff
    staff.status=StaffStatus.SUSPENDED;db.session.commit()
    assert client.get(u,headers=h).status_code==403
    staff.status=StaffStatus.ACTIVE;db.session.commit()
    h=headers(w['users']['doctor'])
    membership=db.session.query(ClinicMembership).filter_by(user_id=w['users']['doctor'].id,clinic_id=w['a'].id).one();membership.is_active=False;db.session.commit()
    assert client.get(u,headers=h).status_code==401


def test_ordering_pagination_filters_and_patient_isolation(client,world,db,make_appointment,make_consultation,make_prescription):
    w=world;a=w['a'];p=w['p'];s=w['users']['doctor'].staff
    when=datetime(2026,1,2,12,tzinfo=timezone.utc);earlier=when-timedelta(days=1)
    # Same timestamp across event types and same-source IDs; cursor must not skip or duplicate.
    ap=make_appointment(a,p,s,created_at=when,scheduled_start=when,scheduled_end=when+timedelta(hours=1))
    ap2=make_appointment(a,p,s,created_at=when,scheduled_start=when,scheduled_end=when+timedelta(hours=1))
    make_appointment(a,w['other'],s,created_at=when,scheduled_start=when,scheduled_end=when+timedelta(hours=1))
    c=make_consultation(a,p,s,appointment=ap,started_at=earlier,created_at=earlier,ended_at=when,status=ConsultationStatus.COMPLETED,diagnosis='Synthetic diagnosis',record_status='final',record_version=1,finalized_by_staff_id=s.id,finalized_at=when)
    db.session.add(PatientVitals(patient_id=p.id,consultation_id=c.id,recorded_by_id=s.id,recorded_at=when,heart_rate_bpm=80))
    make_prescription(a,p,s,consultation_id=c.id,issued_at=when,notes='NEVER COPY THIS NARRATIVE')
    # A valid clinic ID with foreign patient lineage is not visible.
    make_consultation(a,w['other'],s,started_at=when)
    db.session.commit();h=headers(w['users']['doctor']);u=url(w)
    all_=read(client,u,h,limit='100')['items']
    types=[x['event_type'] for x in all_]
    assert types.count('appointment_booking_recorded')==2
    assert types.count('appointment_current_slot')==2
    assert 'consultation_started' in types and 'consultation_completed' in types and 'diagnosis_documented_at_finalization' in types
    assert 'vitals_recorded' in types and 'prescription_issued' in types
    assert 'patient_check_in' not in types and 'clinical_radiology_report_finalized' not in types
    assert 'NEVER COPY THIS NARRATIVE' not in str(all_) and 'Synthetic diagnosis' not in str(all_)
    assert all(x['source']['id'] in {ap.id,ap2.id} for x in all_ if x['source']['kind']=='appointment')
    assert all_[0]['occurred_at'].endswith('+00:00')
    assert all_==sorted(all_,key=lambda x:(-datetime.fromisoformat(x['occurred_at']).timestamp(),x['event_type'],-int(x['event_id'].split(':')[-1])))
    got=[];cursor=None
    while True:
        data=read(client,u,h,limit='2',**({'cursor':cursor} if cursor else {}));got+=data['items'];cursor=data['next_cursor']
        if cursor is None:break
        assert len(got)<=len(all_)
    assert got==all_ and len({x['event_id'] for x in got})==len(all_)
    data=read(client,u,h,limit='1');cursor=data['next_cursor'];assert cursor
    for query in [dict(limit='2',cursor=cursor),dict(limit='1',cursor=cursor+'x'),dict(limit='1',categories='vitals',cursor=cursor)]:
        assert client.get(u,headers=h,query_string=query).status_code==400
    assert client.get(url(w,w['other']),headers=h,query_string={'limit':'1','cursor':cursor}).status_code==400
    assert client.get(u,headers=headers(w['users']['doctor2']),query_string={'limit':'1','cursor':cursor}).status_code==400
    assert all(x['category']=='vitals' for x in read(client,u,h,categories='vitals')['items'])
    assert read(client,u,h,start='2026-01-02T12:00:00Z',end='2026-01-02T12:00:01Z')['items']==[x for x in all_ if datetime.fromisoformat(x['occurred_at'])>=when]
    nurse=read(client,u,headers(w['users']['nurse']))
    assert 'prescriptions' not in nurse['available_categories']
    assert not any(x['category']=='prescriptions' for x in nurse['items'])


def test_problem_history_and_flags(client,world,db,app):
    w=world;h=headers(w['users']['doctor']);u=url(w)
    path=f"/api/v1/patients/{w['p'].id}/problems"
    body=dict(name='Synthetic hypertension',category='chronic',code='I10',coding_system='http://hl7.org/fhir/sid/icd-10',clinical_status='active',verification_status='confirmed')
    r=client.post(path,headers=h,json=body);assert r.status_code==201,r.get_data(as_text=True)
    row=r.json['data'];change=client.patch(path+'/'+str(row['id']),headers=h,json={'action':'review','expected_version':1,'reason':'Synthetic review'});assert change.status_code==200,change.get_data(as_text=True)
    items=read(client,u,h,categories='problems')['items']
    assert [x['event_type'] for x in items]==['problem_review_or_change_recorded','problem_added']
    assert [x['record_version'] for x in items]==[2,1]
    assert all(x['source']['id']==row['id'] for x in items)
    assert 'Synthetic review' not in str(items)
    app.config['PATIENT_PROBLEM_LIST_ENABLED']=False
    assert read(client,u,h,categories='problems')['items']==[]


def test_all_source_query_compiles_and_executes_empty(client,world,db):
    w=world;h=headers(w['users']['doctor'])
    assert read(client,url(w),h)['items']==[]
    assert read(client,url(w),headers(w['users']['nurse']))['items']==[]
    # This executes every enabled source branch on the actual ORM schema;
    # the same test file runs in both SQLite and PostgreSQL environments.
    assert db.session.query(PatientProblem).count()==0


def test_lab_lifecycle_and_release_visibility(client,world,db,make_lab_test,make_lab_order,app):
    from app.core.enums.lab_enums import LabOrderStatus, LabResultFlag
    w=world;a=w['a'];p=w['p'];s=w['users']['doctor'].staff
    t=datetime(2026,1,2,12,tzinfo=timezone.utc)
    lab=make_lab_test(a)
    order=make_lab_order(a,p,s,[lab],status=LabOrderStatus.COMPLETED,created_at=t,
        collected_by_id=s.id,sample_collected_at=t+timedelta(minutes=1),
        processed_by_id=s.id,processed_at=t+timedelta(minutes=2),verified_by_id=s.id,verified_at=t+timedelta(minutes=4),
        completed_at=t+timedelta(minutes=5),patient_released_at=t+timedelta(minutes=6),patient_released_by_id=s.id,
        patient_release_user_id=w['users']['patient'].id,patient_release_snapshot={'synthetic_test_fixture':True})
    item=order.items[0];item.resulted_at=t+timedelta(minutes=3);item.result_value='SYNTHETIC PRIVATE RESULT';item.flag=LabResultFlag.NORMAL
    db.session.commit();u=url(w)
    data=read(client,u,headers(w['users']['doctor']),categories='laboratory')['items']
    assert {x['event_type'] for x in data}=={'lab_order_created','lab_sample_collected','lab_sample_processed','lab_result_entry_recorded','lab_results_verified','lab_order_completed','lab_patient_release_recorded'}
    assert all(x['source']['id']==order.id for x in data) and 'SYNTHETIC PRIVATE RESULT' not in str(data)
    for who in ['nurse','doctor2']:
        rows=read(client,u,headers(w['users'][who]),categories='laboratory')['items']
        assert not any('release' in x['event_type'] for x in rows)
        assert len(rows)==6
    app.config['PATIENT_LAB_RELEASE_ENABLED']=False
    assert len(read(client,u,headers(w['users']['doctor']),categories='laboratory')['items'])==6


def test_admission_emar_nursing_imaging_and_surgery_scope(client,world,db,make_ward,make_bed,make_prescription,make_prescription_item,make_drug):
    from app.modules.ward.models.ward_model import Admission, WardTransfer
    from app.core.enums.ward_enums import AdmissionStatus
    from app.modules.emar.models.emar_model import MedicationAdministration
    from app.core.enums.emar_enums import MedicationAdministrationStatus as MS
    from app.modules.nursing.models.nursing_model import NursingCarePlan,NursingCareTask
    from app.core.enums.nursing_enums import NursingCareTaskStatus
    from app.modules.clinical_orders.models.imaging_order_model import ImagingOrder
    from app.modules.clinical_orders.models.procedure_order_model import ProcedureOrder
    from app.modules.surgery.models.surgical_case_model import SurgicalCase
    w=world;a=w['a'];p=w['p'];s=w['users']['doctor'].staff;n=w['users']['nurse'].staff
    t=datetime(2026,1,2,12,tzinfo=timezone.utc);naive=t.replace(tzinfo=None)
    ward=make_ward(a);bed1=make_bed(ward);bed2=make_bed(ward)
    adm=Admission(patient_id=p.id,bed_id=bed2.id,admitted_by_id=s.id,status=AdmissionStatus.DISCHARGED,admitted_at=naive,discharged_at=naive+timedelta(days=2));db.session.add(adm);db.session.flush()
    db.session.add(WardTransfer(admission_id=adm.id,from_bed_id=bed1.id,to_bed_id=bed2.id,transferred_at=naive+timedelta(days=1)))
    rx=make_prescription(a,p,s,issued_at=t);item=make_prescription_item(rx,make_drug(a))
    common=dict(clinic_id=a.id,patient_id=p.id,admission_id=adm.id,prescription_id=rx.id,prescription_item_id=item.id)
    db.session.add_all([
        MedicationAdministration(**common,status=MS.ADMINISTERED,administered_at=t+timedelta(hours=1),administered_by_staff_id=n.id),
        MedicationAdministration(**common,status=MS.HELD,held_at=t+timedelta(hours=2),held_by_staff_id=n.id,hold_reason='Synthetic hold'),
        MedicationAdministration(**common,status=MS.REFUSED,refused_at=t+timedelta(hours=3),refused_recorded_by_staff_id=n.id,refusal_reason='Synthetic refusal'),
        MedicationAdministration(**common,status=MS.SCHEDULED,scheduled_at=t+timedelta(hours=4)),
    ])
    plan=NursingCarePlan(clinic_id=a.id,patient_id=p.id,admission_id=adm.id,created_by_staff_id=n.id,title='Synthetic',problem='Synthetic',goal='Synthetic',started_at=naive);db.session.add(plan);db.session.flush()
    db.session.add(NursingCareTask(clinic_id=a.id,patient_id=p.id,admission_id=adm.id,care_plan_id=plan.id,created_by_staff_id=n.id,completed_by_staff_id=n.id,description='Synthetic private task',status=NursingCareTaskStatus.COMPLETED,completed_at=naive+timedelta(hours=3)))
    common_order=dict(clinic_id=a.id,patient_id=p.id,admission_id=adm.id,ordered_by_staff_id=s.id,indication='Synthetic')
    db.session.add(ImagingOrder(**common_order,study_name='Synthetic imaging',modality='ct',body_site='head',ordered_at=t,scheduled_at=t+timedelta(hours=3)))
    po=ProcedureOrder(**common_order,procedure_name='Synthetic surgical plan',procedure_type='surgical',ordered_at=t);db.session.add(po);db.session.flush()
    case=SurgicalCase(clinic_id=a.id,patient_id=p.id,admission_id=adm.id,procedure_order_id=po.id,planned_by_staff_id=s.id,created_at=naive);db.session.add(case);db.session.commit()
    u=url(w);rows=read(client,u,headers(w['users']['doctor']))['items'];types={x['event_type'] for x in rows}
    assert {'patient_admitted','patient_discharged','bed_transfer_recorded','medication_administered','medication_held','medication_refused','medication_current_slot','nursing_task_completed','imaging_order_created','imaging_current_slot','surgical_case_plan_recorded'}<=types
    assert 'surgical_procedure_performance_recorded' not in types and 'clinical_radiology_report_finalized' not in types
    assert 'Synthetic private task' not in str(rows) and 'Synthetic hold' not in str(rows)
    for who in ['doctor2','nurse']:
        other=read(client,u,headers(w['users'][who]))['items']
        assert not any(x['category']=='surgery' for x in other)
        assert any(x['event_type']=='medication_administered' for x in other)
    assert db.session.query(MedicationAdministration).count()==4
