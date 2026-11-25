"""Authenticated staged problem list: real Flask/JWT/ORM, disposable DB only."""
from datetime import datetime, timedelta, timezone, date
from uuid import uuid4
import pytest
from flask_jwt_extended import create_access_token, decode_token
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership, ClinicMembership
from app.modules.patient.problems.model import PatientProblem
from app.modules.clinical_records.models.clinical_record_amendment_model import ClinicalRecordAmendment
from app.modules.patient.problems.safety import confirmed_problem_context
from app.core.clinical_safety.services.clinical_safety_context_service import build_clinical_safety_context
from app.modules.patient.problems import service


def header(pair):
    return {'Authorization': 'Bearer ' + pair['access_token']}


def grant(db, actor, clinic):
    db.session.add(TenantMembership(user_id=actor.id, organization_id=clinic.organization_id, role='member', status='active', is_active=True))
    db.session.flush()
    db.session.add(ClinicMembership(user_id=actor.id, organization_id=clinic.organization_id, clinic_id=clinic.id, role=actor.role.value, is_active=True))
    db.session.flush()


@pytest.fixture
def world(app, db, make_clinic, make_staff, make_user, make_patient):
    app.config['PATIENT_PROBLEM_LIST_ENABLED'] = True
    other_org = HospitalOrganization(id=uuid4(), name='Synthetic foreign organization')
    db.session.add(other_org); db.session.flush()
    a = make_clinic(name='Problem alpha'); b = make_clinic(name='Problem beta', organization_id=other_org.id)
    doctor = make_staff(a, role=Role.DOCTOR); nurse = make_staff(a, role=Role.NURSE)
    foreign = make_staff(b, role=Role.DOCTOR); admin = make_staff(a, role=Role.ADMIN)
    patient_user = make_user(a, role=Role.PATIENT); superadmin = make_user(role=Role.SUPER_ADMIN)
    patient = make_patient(a, user_id=patient_user.id, chronic_conditions='Uncoded legacy asthma')
    second = make_patient(a); other = make_patient(b)
    for actor, clinic in [(doctor.user,a),(nurse.user,a),(foreign.user,b),(admin.user,a),(patient_user,a)]:
        grant(db,actor,clinic)
    db.session.commit()
    return dict(a=a,b=b,doctor=doctor,nurse=nurse,foreign=foreign,admin=admin,patient_user=patient_user,sa=superadmin,patient=patient,second=second,other=other)


def url(w, suffix=''):
    return f"/api/v1/patients/{w['patient'].id}/problems{suffix}"


def body(**extra):
    return dict(name='Hypertension', category='chronic', code='I10', coding_system='http://hl7.org/fhir/sid/icd-10', clinical_status='active', verification_status='confirmed') | extra


def create(client, w, **extra):
    res=client.post(url(w),headers=header(issue_auth_tokens(w['doctor'].user)),json=body(**extra))
    assert res.status_code == 201,res.get_data(as_text=True)[:500]
    assert 'no-store' in res.headers['Cache-Control']
    return res.json['data']


def test_lifecycle_and_original_amendment_evidence(client, world, db):
    w=world; first=create(client,w); doctor=header(issue_auth_tokens(w['doctor'].user)); nurse=header(issue_auth_tokens(w['nurse'].user))
    item=url(w,f"/{first['id']}")
    assert client.get(item,headers=nurse).json['data']==first
    assert client.post(url(w),headers=doctor,json=body()).status_code==409
    assert client.post(url(w),headers=nurse,json=body(name='Denied')).status_code==403
    content=first['content'] | {'clinical_status':'resolved','resolution_date':datetime.now(timezone.utc).date().isoformat()}
    data={'expected_version':1,'action':'transition','reason':'Clinical review resolved','content':content}
    changed=client.patch(item,headers=doctor,json=data)
    assert changed.status_code==200,changed.get_data(as_text=True)[:500]
    assert changed.json['data']['version']==2
    assert client.patch(item,headers=doctor,json=data).status_code==409
    assert client.get(url(w)+'?status=current',headers=nurse).json['data']['items']==[]
    assert len(client.get(url(w)+'?status=resolved',headers=nurse).json['data']['items'])==1
    history=client.get(item+'/history',headers=nurse).json['data']
    assert history['original']==first
    assert history['amendments'][0]['version']==2
    assert history['amendments'][0]['content']==changed.json['data']
    assert len(history['amendments'][0]['sha256'])==64
    assert db.session.query(ClinicalRecordAmendment).filter_by(source_record_type='patient_problem').count()==1
    assert db.session.get(PatientProblem,first['id']).original_snapshot==first
    assert w['patient'].chronic_conditions=='Uncoded legacy asthma'
    assert client.delete(item,headers=doctor).status_code==405


def test_role_scope_and_forged_authority_denied(client, world, db):
    w=world; item=create(client,w); doctor=header(issue_auth_tokens(w['doctor'].user))
    assert client.get(url(w)).status_code==401
    for actor in [w['admin'].user,w['patient_user'],w['sa']]:
        assert client.get(url(w),headers=header(issue_auth_tokens(actor))).status_code==403
    assert client.get(url(w),headers=header(issue_auth_tokens(w['foreign'].user))).status_code==404
    assert client.get(f"/api/v1/patients/{w['second'].id}/problems/{item['id']}",headers=doctor).status_code==404
    assert client.get(f"/api/v1/patients/{w['other'].id}/problems",headers=doctor).status_code==404
    assert client.post(url(w),headers=doctor,json=body(name='Forged',clinic_id=w['b'].id)).status_code==400
    assert client.post(url(w),headers=doctor,json=body(name='Foreign consultation',consultation_id=987654)).status_code==404
    unscoped=header(issue_auth_tokens(w['doctor'].user,auto_assign_clinic=False))
    assert client.get(url(w),headers=unscoped).status_code==403
    staff=w['doctor']; staff.status=StaffStatus.SUSPENDED;db.session.commit()
    assert client.get(url(w),headers=doctor).status_code==403


def test_revoked_grant_invalidates_signed_token(client, world, db):
    w=world; h=header(issue_auth_tokens(w['doctor'].user))
    grant_row=db.session.query(ClinicMembership).filter_by(user_id=w['doctor'].user_id,clinic_id=w['a'].id).one()
    grant_row.is_active=False;db.session.commit()
    assert client.get(url(w),headers=h).status_code==401


def test_feature_hold(client, world, app):
    h=header(issue_auth_tokens(world['doctor'].user))
    app.config['PATIENT_PROBLEM_LIST_ENABLED']=False
    assert client.get(url(world),headers=h).status_code==404
    app.config['PATIENT_PROBLEM_LIST_ENABLED']=True;app.config['TESTING']=False
    try:
        assert client.get(url(world),headers=h).status_code==404
    finally:
        app.config['TESTING']=True


def test_fhir_and_safety_exclude_unconfirmed_and_legacy(client, world, db):
    w=world; first=create(client,w); create(client,w,name='Suspected diabetes',code='E11.9',verification_status='provisional')
    create(client,w,name='SNOMED finding',code='44054006',coding_system='http://snomed.info/sct')
    now=datetime.now(timezone.utc)+timedelta(seconds=1)
    result=build_clinical_safety_context(clinic_id=w['a'].id,patient_id=w['patient'].id,evaluation_at=now).to_dict()
    assert result['diagnosis_codes']==['I10']
    assert {i['code'] for i in result['problem_conditions']}=={'I10','44054006'}
    h=header(issue_auth_tokens(w['doctor'].user))
    res=client.get(url(w,f"/{first['id']}/fhir-condition"),headers=h)
    assert res.status_code==200,res.get_data(as_text=True)[:500]
    fhir=res.json['data']; assert fhir['resourceType']=='Condition'; assert fhir['subject']['reference']==f"Patient/{w['patient'].id}"
    assert fhir['code']['coding'][0]['code']=='I10';assert 'note' not in fhir
    before=datetime.fromisoformat(first['recorded_at'])-timedelta(seconds=1)
    assert confirmed_problem_context(clinic_id=w['a'].id,patient_id=w['patient'].id,evaluation_at=before)==([],[])


def test_audit_failure_rolls_back_new_problem(client, world, db, monkeypatch):
    w=world
    def fail(*a,**kw): raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(service,'_audit',fail)
    from app.modules.patient.problems.contracts import ProblemCreate
    claims=decode_token(issue_auth_tokens(w['doctor'].user)['access_token'])
    with pytest.raises(RuntimeError,match='synthetic audit failure'):
        service.create_problem(w['doctor'].user_id,claims,w['patient'].id,ProblemCreate.model_validate(body()))
    assert db.session.query(PatientProblem).count()==0


def test_platform_context_has_no_clinical_membership(client, world, db):
    from app.modules.clinic.services.session_authority import context_from_claims, platform_context_from_claims
    from app.modules.clinic.services.tenant_boundary import require_tenant_clinic_access,TenantAccessDenied
    w=world; actor=w['sa']; pair=issue_auth_tokens(actor); h=header(pair)
    selected=client.post('/api/v1/auth/clinic-context',headers=h,json={'clinic_id':w['a'].id})
    assert selected.status_code==200,selected.get_data(as_text=True)[:500]
    new=selected.json['data']; access=decode_token(new['access_token']);refresh=decode_token(new['refresh_token'])
    for claims in [access,refresh]:
        assert claims['platform_clinic_id']==w['a'].id
        assert 'clinic_context_id' not in claims and 'tenant_id' not in claims
        assert context_from_claims(actor.id,claims)==(None,None)
        assert platform_context_from_claims(actor.id,claims)==w['a'].id
    assert new['clinic_context_id']==w['a'].id
    assert client.get('/api/v1/auth/clinic-context',headers=h).status_code==401
    assert client.get(url(w),headers=header(new)).status_code==403
    with pytest.raises(TenantAccessDenied):require_tenant_clinic_access(user_id=actor.id,organization_id=w['a'].organization_id,clinic_id=w['a'].id)
    forged=create_access_token(identity=str(w['doctor'].user_id),additional_claims={'role':'doctor','token_version':w['doctor'].user.token_version,'platform_clinic_id':w['a'].id})
    assert client.get(url(w),headers={'Authorization':'Bearer '+forged}).status_code==401
    refreshed=client.post('/api/v1/auth/refresh',headers={'Authorization':'Bearer '+new['refresh_token']})
    assert refreshed.status_code==200,refreshed.get_data(as_text=True)[:500]
    replacement=refreshed.json['data']; assert decode_token(replacement['access_token'])['platform_clinic_id']==w['a'].id
    cleared=client.delete('/api/v1/auth/clinic-context',headers=header(replacement))
    assert cleared.status_code==200;assert 'platform_clinic_id' not in decode_token(cleared.json['data']['access_token'])


def test_super_admin_root_creation_assigns_owner_without_grants(client, world, db):
    from app.modules.clinic.models.clinic_model import Clinic
    w=world; admin=header(issue_auth_tokens(w['admin'].user)); sa=header(issue_auth_tokens(w['sa']))
    assert client.post('/api/v1/clinics',headers=admin,json={'name':'Denied root'}).status_code==403
    result=client.post('/api/v1/clinics',headers=sa,json={'name':'Synthetic root'});assert result.status_code==201,result.get_data(as_text=True)[:500]
    c=db.session.get(Clinic,result.json['data']['id']); assert c.organization_id is not None and c.organization_id!=w['a'].organization_id
    assert db.session.query(ClinicMembership).filter_by(clinic_id=c.id).count()==0
    assert db.session.query(TenantMembership).filter_by(organization_id=c.organization_id).count()==0


def test_concurrent_duplicate_on_postgres(client, world, app, db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.core.exceptions import ConflictError
    from app.modules.patient.problems.contracts import ProblemCreate
    assert db.engine.dialect.name=='postgresql'
    w=world;uid=w['doctor'].user_id;pid=w['patient'].id;claims=decode_token(issue_auth_tokens(w['doctor'].user)['access_token'])
    db.session.remove(); barrier=Barrier(2,timeout=30)
    def attempt():
        with app.app_context():
            barrier.wait()
            try:
                service.create_problem(uid,claims,pid,ProblemCreate.model_validate(body()));return 'created'
            except ConflictError:return 'duplicate'
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(lambda _:attempt(),range(2)))
    assert sorted(result)==['created','duplicate']
    assert db.session.query(PatientProblem).count()==1


def test_existing_cds_rule_consumes_confirmed_problem_history(client, world):
    from app.tests.core.clinical_safety.test_rule_evaluator import make_rule
    from app.core.enums.clinical_safety_enums import ClinicalRuleAction, ClinicalRuleType
    from app.core.clinical_safety.services.rule_evaluator import evaluate_rule
    w = world
    first = create(client, w)
    create(client, w, name='Suspected diabetes', code='E11.9', verification_status='provisional')
    before = datetime.now(timezone.utc)
    def result(code, at):
        ctx = build_clinical_safety_context(clinic_id=w['a'].id, patient_id=w['patient'].id, evaluation_at=at).to_dict()
        rule = make_rule(rule_type=ClinicalRuleType.DIAGNOSIS_CONFLICT, action=ClinicalRuleAction.BLOCK,
                         is_hard_rule=True, configuration={'diagnosis_codes': [code]})
        return evaluate_rule(rule, ctx)
    assert result('I10', before).blocked is True
    assert result('E11.9', before).matched is False
    changed = client.patch(url(w, f"/{first['id']}"), headers=header(issue_auth_tokens(w['doctor'].user)), json={
        'expected_version': 1, 'action': 'transition', 'reason': 'Resolved at clinical review',
        'content': first['content'] | {'clinical_status': 'resolved', 'resolution_date': before.date().isoformat()}})
    assert changed.status_code == 200, changed.get_json()
    assert result('I10', datetime.now(timezone.utc)+timedelta(seconds=1)).matched is False
    assert result('I10', before).blocked is True


def test_workflow_review_is_bound_read_only_and_minimal(client, world, db, app, make_ward, make_bed, make_staff):
    from app.core.enums.ward_enums import AdmissionStatus, BedStatus, WardType
    from app.core.enums.medication_reconciliation_enums import MedicationReconciliationEvent
    from app.modules.ward.models.ward_model import Admission
    from app.modules.medication_reconciliation.services.medication_reconciliation_service import create_medication_reconciliation
    w = world
    first = create(client, w, notes='Private synthetic problem note')
    create(client, w, name='Suspected diabetes', code='E11.9', verification_status='provisional')
    pharmacist = make_staff(w['a'], role=Role.PHARMACIST)
    grant(db, pharmacist.user, w['a'])
    ward = make_ward(w['a'], ward_type=WardType.GENERAL)
    bed = make_bed(ward, status=BedStatus.OCCUPIED)
    admission = Admission(patient_id=w['patient'].id, bed_id=bed.id, admitted_by_id=w['doctor'].id,
        status=AdmissionStatus.ADMITTED, reason='Synthetic review', admitted_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db.session.add(admission); db.session.commit()
    record = create_medication_reconciliation(clinic_id=w['a'].id, admission_id=admission.id,
        event_type=MedicationReconciliationEvent.ADMISSION, actor_staff_id=w['doctor'].id,
        no_medications_reported=True, items=[])
    db.session.commit()
    ward_path = f'/api/v1/wards/admissions/{admission.id}/problem-context'
    medication_path = f'/api/v1/medication-reconciliations/{record.id}/problem-context'
    snapshot = (record.record_version, record.record_status, record.notes, admission.status)
    for path, actor in [(ward_path, w['doctor'].user), (ward_path, w['nurse'].user),
                        (medication_path, w['doctor'].user), (medication_path, w['nurse'].user),
                        (medication_path, pharmacist.user)]:
        res = client.get(path, headers=header(issue_auth_tokens(actor)))
        assert res.status_code == 200, res.get_json()
        assert 'no-store' in res.headers['Cache-Control']
        conditions = res.json['data']['confirmed_current_problems']
        assert [c['problem_id'] for c in conditions] == [first['id']]
        assert conditions[0]['code'] == 'I10' and 'notes' not in conditions[0]
    for path in (ward_path, medication_path):
        for actor in (w['admin'].user, w['patient_user'], w['sa']):
            assert client.get(path, headers=header(issue_auth_tokens(actor))).status_code == 403
        assert client.get(path, headers=header(issue_auth_tokens(w['foreign'].user))).status_code == 404
        assert client.get(path+'?patient_id=1', headers=header(issue_auth_tokens(w['doctor'].user))).status_code == 400
    ph = header(issue_auth_tokens(pharmacist.user))
    assert client.get(ward_path, headers=ph).status_code == 403
    assert client.get(url(w), headers=ph).status_code == 403
    assert client.post(medication_path, headers=ph, json={}).status_code == 405
    db.session.refresh(record); db.session.refresh(admission)
    assert (record.record_version, record.record_status, record.notes, admission.status) == snapshot
    assert db.session.query(ClinicalRecordAmendment).filter_by(source_record_type='patient_problem').count() == 0
    app.config['PATIENT_PROBLEM_LIST_ENABLED'] = False
    assert client.get(medication_path, headers=ph).status_code == 404
