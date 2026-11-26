"""Real Flask + DB + signed JWT/Redis route checks. No mocked services."""
from datetime import date, timedelta
from uuid import uuid4
import json
import os
from pathlib import Path
from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
import pytest
from flask_jwt_extended import decode_token
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import LeaveStatus, LeaveType
from app.core.enums.excuse_enums import ExcuseStatus, ExcuseType
from app.core.exceptions import ValidationError
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership, ClinicMembership
from app.modules.staff.models.staff_model import LeaveRequest
from app.modules.staff.models.excuse_model import Excuse
from app.modules.staff.services import staff_service, excuse_service

PREFIX='/api/v1/staff/'

@pytest.fixture
def world(app, db, make_clinic, make_user, make_staff):
    a=HospitalOrganization(id=uuid4(),name='SA contract synthetic Alpha')
    b=HospitalOrganization(id=uuid4(),name='SA contract synthetic Beta')
    db.session.add_all([a,b]); db.session.flush()
    own=make_clinic(name='SA contract Alpha HQ',organization_id=a.id,is_headquarters=True)
    sibling=make_clinic(name='SA contract Alpha branch',organization_id=a.id,parent_clinic_id=own.id)
    foreign=make_clinic(name='SA contract Beta HQ',organization_id=b.id,is_headquarters=True)
    sa=make_user(role=Role.SUPER_ADMIN)
    admin=make_staff(own,role=Role.ADMIN).user
    subjects=[make_staff(c,role=Role.DOCTOR) for c in (own,own,sibling,foreign)]
    for actor,c in [(admin,own),*((s.user,c) for s,c in zip(subjects,(own,own,sibling,foreign)))]:
        db.session.add(TenantMembership(user_id=actor.id,organization_id=c.organization_id,role='member',status='active',is_active=True)); db.session.flush()
        db.session.add(ClinicMembership(user_id=actor.id,organization_id=c.organization_id,clinic_id=c.id,role=actor.role.value,is_active=True))
    start=date.today()+timedelta(days=20)
    leaves=[LeaveRequest(staff_id=s.id,leave_type=LeaveType.ANNUAL,status=LeaveStatus.PENDING,start_date=start,end_date=start+timedelta(days=1),reason='Synthetic leave') for s in subjects]
    excuses=[Excuse(staff_id=s.id,excuse_type=ExcuseType.PERSONAL,status=ExcuseStatus.PENDING,description='Synthetic excuse') for s in subjects]
    db.session.add_all(leaves+excuses); db.session.commit()
    def header(actor, clinic_id=None):
        with app.test_request_context():
            token=issue_auth_tokens(actor,clinic_context_id=clinic_id)['access_token']
            claims=decode_token(token)
        if actor.role is Role.SUPER_ADMIN:
            assert claims.get('platform_clinic_id')==clinic_id
            assert 'clinic_context_id' not in claims and 'tenant_id' not in claims
        else:
            assert claims.get('clinic_context_id')==clinic_id
        return {'Authorization':'Bearer '+token}
    assert db.session.query(ClinicMembership).filter_by(user_id=sa.id).count()==0
    return dict(own=own,sibling=sibling,foreign=foreign,sa=sa,admin=admin,subjects=subjects,leave=leaves,excuses=excuses,selected=header(sa,own.id),unselected=header(sa),admin_h=header(admin,own.id),doctor_h=header(subjects[0].user,own.id))

def data(response,code=200):
    assert response.status_code==code,response.get_json()
    return response.get_json()['data']

@pytest.mark.parametrize('kind',['leave','excuses'])
@pytest.mark.parametrize('decision',['approve','reject'])
def test_platform_review_selected_clinic_only(client,db,world,kind,decision):
    w=world; rows=w[kind]; h=w['selected']; path=PREFIX+kind
    items=data(client.get(path,headers=h))['items']
    assert {i['id'] for i in items}=={rows[0].id,rows[1].id}
    filtered=data(client.get(path,headers=h,query_string={'staff_id':w['subjects'][1].id}))['items']
    assert [i['id'] for i in filtered]==[rows[1].id]
    assert data(client.get(f'{path}/{rows[1].id}',headers=h))['id']==rows[1].id
    for row in rows[2:]:
        assert client.get(f'{path}/{row.id}',headers=h).status_code==404
        assert client.post(f'{path}/{row.id}/{decision}',headers=h,json={}).status_code==404
    reply=data(client.post(f'{path}/{rows[1].id}/{decision}',headers=h,json={}))
    assert reply['id']==rows[1].id and reply['reviewed_by_user_id']==w['sa'].id
    assert reply['status']=={'approve':'approved','reject':'rejected'}[decision]
    if kind=='leave': assert reply['clinic_id']==w['own'].id
    db.session.expire_all()
    assert rows[1].status.value==reply['status']
    assert rows[1].reviewed_by_user_id==w['sa'].id
    log=db.session.query(AuditLog).filter_by(entity_type='LeaveRequest' if kind=='leave' else 'Excuse', entity_id=rows[1].id, user_id=w['sa'].id, action=AuditAction.STATUS_CHANGE).one()
    assert log.clinic_id==w['own'].id and len(log.evidence_sha256)==64
    assert log.new_value['clinic_id']==w['own'].id
    assert all(r.status.value=='pending' and r.reviewed_by_user_id is None for r in rows[2:])
    assert client.post(f'{path}/{rows[1].id}/{decision}',headers=h,json={}).status_code==409

@pytest.mark.parametrize('kind',['leave','excuses'])
def test_unselected_platform_and_ordinary_staff_denied(client,db,world,kind):
    w=world; rows=w[kind]; path=PREFIX+kind
    h=w['unselected']
    for p in (path,f'{path}/{rows[0].id}'):
        assert client.get(p,headers=h).status_code==422
    for decision in ('approve','reject'):
        assert client.post(f'{path}/{rows[0].id}/{decision}',headers=h,json={}).status_code==422
        assert client.post(f'{path}/{rows[1].id}/{decision}',headers=w['doctor_h'],json={}).status_code==403
    # Non-manager filters cannot request another member's rows.
    own=data(client.get(path,headers=w['doctor_h'],query_string={'staff_id':w['subjects'][1].id}))['items']
    assert [i['id'] for i in own]==[rows[0].id]
    assert client.get(f'{path}/{rows[1].id}',headers=w['doctor_h']).status_code==404
    assert {i['id'] for i in data(client.get(path,headers=w['admin_h']))['items']}=={rows[0].id,rows[1].id}
    approved=data(client.post(f'{path}/{rows[1].id}/approve',headers=w['admin_h'],json={}))
    assert approved['reviewed_by_user_id']==w['admin'].id and approved['status']=='approved'
    db.session.expire_all(); assert rows[0].status.value=='pending'

@pytest.mark.parametrize('kind',['leave','excuses'])
@pytest.mark.parametrize('decision',['approve','reject'])
def test_platform_service_requires_verified_selected_actor(app,db,world,kind,decision):
    w=world; rows=w[kind]
    if kind=='leave': fn=getattr(staff_service,decision+'_leave_request'); key='leave_id'
    else: fn=getattr(excuse_service,decision+'_excuse'); key='excuse_id'
    # Direct service use with a guessed platform actor is not authorized.
    with pytest.raises(ValidationError,match='platform clinic context'):
        fn(**{key:rows[0].id,'clinic_id':w['own'].id,'reviewer_user_id':w['sa'].id})
    # A real signed token scoped to Alpha HQ cannot be used to review Beta.
    with app.test_request_context(headers=w['selected']):
        with pytest.raises(ValidationError,match='platform clinic context'):
            fn(**{key:rows[3].id,'clinic_id':w['foreign'].id,'reviewer_user_id':w['sa'].id})
    # A different signed actor cannot borrow the platform reviewer identity.
    with app.test_request_context(headers=w['admin_h']):
        with pytest.raises(ValidationError,match='platform clinic context'):
            fn(**{key:rows[0].id,'clinic_id':w['own'].id,'reviewer_user_id':w['sa'].id})
    db.session.expire_all(); assert rows[0].status.value==rows[3].status.value=='pending'

@pytest.mark.parametrize('role',['selected','admin_h'])
def test_payroll_existing_flutter_receipt_contract(client,world,role):
    w=world; h=w[role]; path=PREFIX+'payroll'; clinic=w['own'].id
    payload={'staff_id':w['subjects'][0].id,'pay_period_start':'2026-10-01','pay_period_end':'2026-10-31','base_salary':'500'}
    assert client.post(path,headers=h,json={**payload,'clinic_id':w['foreign'].id}).status_code==422
    assert client.post(path,headers=h,json={**payload,'staff_id':w['subjects'][3].id}).status_code==404
    created_response=client.post(path,headers=h,json=payload)
    row=data(created_response,201)
    assert row['clinic_id']==clinic and row['staff_id']==payload['staff_id'] and row['id']>0
    read_response=client.get(f"{path}/{row['id']}",headers=h)
    assert data(read_response)['clinic_id']==clinic
    assert data(client.get(path,headers=h))['items'][0]['clinic_id']==clinic
    paid_response=client.post(f"{path}/{row['id']}/pay",headers=h,json={})
    paid=data(paid_response)
    assert paid['id']==row['id'] and paid['clinic_id']==clinic and paid['paid_at'] is not None
    batch=data(client.post(path+'/generate',headers=h,json={'pay_period_start':'2026-11-01','pay_period_end':'2026-11-30','salary_lookup':{str(w['subjects'][1].id):'500',str(w['subjects'][3].id):'700'}}),201)
    assert len(batch)==1 and batch[0]['staff_id']==w['subjects'][1].id and batch[0]['clinic_id']==clinic
    if os.environ.get('SA_CONTRACT_RECEIPT_DIR'):
        Path(os.environ['SA_CONTRACT_RECEIPT_DIR'], 'payroll_'+role+'.json').write_text(json.dumps({'clinic_id':clinic,'request':payload,'create':created_response.get_json(),'read':read_response.get_json(),'paid':paid_response.get_json()},indent=2),encoding='utf-8')


def test_leave_existing_flutter_receipt_contract(client,world):
    w=world; h=w['doctor_h']; path=PREFIX+'leave'; start=date.today()+timedelta(days=60)
    payload={'leave_type':'annual','start_date':start.isoformat(),'end_date':start.isoformat(),'reason':'Synthetic receipt'}
    response=client.post(path,headers=h,json=payload)
    row=data(response,201)
    assert row['clinic_id']==w['own'].id and row['staff_id']==w['subjects'][0].id
    assert data(client.get(f"{path}/{row['id']}",headers=h))['clinic_id']==w['own'].id
    assert all(x['clinic_id']==w['own'].id for x in data(client.get(path,headers=h))['items'])
    if os.environ.get('SA_CONTRACT_RECEIPT_DIR'):
        Path(os.environ['SA_CONTRACT_RECEIPT_DIR'],'leave_doctor.json').write_text(json.dumps({'clinic_id':w['own'].id,'request':payload,'create':response.get_json()},indent=2),encoding='utf-8')
