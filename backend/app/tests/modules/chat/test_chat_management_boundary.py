from datetime import datetime, timezone

import pytest

from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.enums.chat_enums import ConversationStatus, ConversationType, ParticipantRole, ParticipantStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError
from app.modules.chat.models.conversation_participant_model import ConversationParticipant
from app.modules.chat.services import conversation_service as svc
from app.modules.chat.services import chat_management_service as management
from app.modules.clinic.models.organization_model import ClinicMembership, TenantMembership

ROOT = '/api/v1/chat'


def grant(db, clinic, user):
    db.session.add(TenantMembership(organization_id=clinic.organization_id, user_id=user.id,
                                    role='member', is_active=True, status='active'))
    db.session.flush()
    row = ClinicMembership(organization_id=clinic.organization_id, clinic_id=clinic.id,
                           user_id=user.id, role=user.role.value, is_active=True)
    db.session.add(row)
    db.session.flush()
    return row


def headers(app, user):
    with app.test_request_context():
        pair = issue_auth_tokens(user, clinic_context_id=user.clinic_id)
    return {'Authorization': 'Bearer ' + pair['access_token']}


@pytest.fixture
def context(app, db, clinic, make_clinic, make_staff, make_clinic_settings):
    a = make_staff(clinic, role=Role.DOCTOR, first_name='Sender')
    b = make_staff(clinic, role=Role.NURSE, first_name='Invitee')
    c = make_staff(clinic, role=Role.DOCTOR, first_name='Unrelated')
    foreign_clinic = make_clinic()
    foreign = make_staff(foreign_clinic, role=Role.DOCTOR, first_name='Foreign')
    for staff, site in ((a, clinic), (b, clinic), (c, clinic), (foreign, foreign_clinic)):
        grant(db, site, staff.user)
    for site in (clinic, foreign_clinic):
        make_clinic_settings(clinic=site, feature_flags={'chat_enabled': True})
    db.session.commit()
    return (a, b, c, foreign), [headers(app, s.user) for s in (a, b, c, foreign)]


def data(resp, expected=200):
    assert resp.status_code == expected, resp.get_json()
    result = resp.get_json()
    assert result['success'] is True
    return result['data']


def create(client, context, kind='direct'):
    (a,b,_,_), (ha,_,_,_) = context
    return data(client.post(ROOT+'/conversations', headers=ha, json={
        'conversation_type': kind, 'title': 'Private synthetic title',
        'participant_user_ids': [b.user_id]}), 201)


def pending(client, context):
    row = create(client, context)
    invite = data(client.get(ROOT+'/management/invitations', headers=context[1][1]))['items']
    assert len(invite) == 1 and invite[0]['conversation_id'] == row['id']
    return row, invite[0]


def test_contacts_only_live_same_clinic_staff(client, context, clinic, db, make_staff):
    inactive = make_staff(clinic, role=Role.NURSE, user_is_active=False)
    ungranted = make_staff(clinic, role=Role.NURSE)
    revoked = make_staff(clinic, role=Role.NURSE)
    grant(db, clinic, revoked.user).is_active = False
    db.session.commit()
    (a,b,c,f), (ha,_,_,_) = context
    page = data(client.get(ROOT+'/management/contacts?per_page=1', headers=ha))
    assert len(page['items']) == 1 and page['has_next'] is True
    second = data(client.get(ROOT+'/management/contacts?per_page=1&page=2', headers=ha))
    assert second['has_next'] is False
    rows = page['items'] + second['items']
    assert {r['user_id'] for r in rows} == {b.user_id,c.user_id}
    assert all(set(r)=={'user_id','staff_id','clinic_id','name','role'} and r['clinic_id']==clinic.id for r in rows)
    assert not {inactive.user_id, ungranted.user_id, revoked.user_id, f.user_id, a.user_id} & {r['user_id'] for r in rows}


@pytest.mark.parametrize('suffix', ['','/participants','/messages','/unread'])
def test_pending_cannot_read_conversation(client, context, suffix):
    row, invite = pending(client, context)
    assert set(invite)=={'id','conversation_id','clinic_id','user_id','conversation_type','created_by_id','role','status'}
    response = client.get(ROOT+f'/conversations/{row["id"]}'+suffix, headers=context[1][1])
    assert response.status_code in (403,404), response.get_json()


def test_invitee_accepts_and_only_then_reads(client, context):
    row, invite = pending(client, context)
    hb=context[1][1]
    out=data(client.post(ROOT+f'/management/invitations/{invite["id"]}/respond', headers=hb,json={'status':'accepted'}))
    assert out['status']=='accepted' and out['user_id']==context[0][1].user_id
    assert data(client.get(ROOT+f'/conversations/{row["id"]}',headers=hb))['id']==row['id']
    assert data(client.get(ROOT+'/management/invitations',headers=hb))['items']==[]
    assert client.post(ROOT+f'/management/invitations/{invite["id"]}/respond',headers=hb,json={'status':'accepted'}).status_code==404


def test_decline_does_not_grant_message_access(client, context):
    row, invite=pending(client,context);hb=context[1][1]
    assert data(client.post(ROOT+f'/management/invitations/{invite["id"]}/respond',headers=hb,json={'status':'declined'}))['status']=='declined'
    assert data(client.get(ROOT+'/management/invitations',headers=hb))['items']==[]
    assert client.get(ROOT+f'/conversations/{row["id"]}/messages',headers=hb).status_code==404


@pytest.mark.parametrize('actor', [0,2,3])
def test_other_users_cannot_respond_to_invite(client, context, actor):
    _, invite=pending(client,context)
    assert client.post(ROOT+f'/management/invitations/{invite["id"]}/respond',headers=context[1][actor],json={'status':'accepted'}).status_code==404


def test_staff_admin_cannot_force_accept_invitee(client, context):
    row, invite=pending(client,context)
    out=client.patch(ROOT+f'/conversations/{row["id"]}/participants/{invite["id"]}',headers=context[1][0],json={'status':'accepted'})
    assert out.status_code==422
    assert data(client.get(ROOT+'/management/invitations',headers=context[1][1]))['items'][0]['status']=='pending'


def test_pending_direct_recreation_does_not_disclose_title(client, context):
    row,_=pending(client,context)
    out=client.post(ROOT+'/conversations',headers=context[1][1],json={'conversation_type':'direct','participant_user_ids':[context[0][0].user_id]})
    assert out.status_code==404, out.get_json()
    assert 'Private synthetic title' not in out.get_data(as_text=True)


def test_foreign_and_ungranted_contact_cannot_be_invited(client, context, clinic, make_staff):
    user=make_staff(clinic,role=Role.NURSE)
    for uid in (context[0][3].user_id,user.user_id):
        out=client.post(ROOT+'/conversations',headers=context[1][0],json={'conversation_type':'direct','participant_user_ids':[uid]})
        assert out.status_code==404


def test_closed_invite_cannot_be_accepted(client, context):
    row,invite=pending(client,context);ha,hb=context[1][:2]
    assert data(client.post(ROOT+f'/conversations/{row["id"]}/status',headers=ha,json={'status':'closed'}))['status']=='closed'
    assert data(client.get(ROOT+'/management/invitations',headers=hb))['items']==[]
    assert client.post(ROOT+f'/management/invitations/{invite["id"]}/respond',headers=hb,json={'status':'accepted'}).status_code==404


@pytest.mark.parametrize('fields',[{'role':ParticipantRole.MEMBER},{'status':ParticipantStatus.REMOVED},{'role':ParticipantRole.MEMBER,'status':ParticipantStatus.LEFT}])
def test_last_active_admin_cannot_be_demoted_or_removed(context, db, clinic, fields):
    a,b,_,_=context[0]
    c=svc.create_conversation(clinic.id,a.user_id,ConversationType.GROUP,title='Team',participant_user_ids=[b.user_id])
    admin=db.session.execute(db.select(ConversationParticipant).where(ConversationParticipant.conversation_id==c.id,ConversationParticipant.user_id==a.user_id)).scalar_one()
    with pytest.raises(ConflictError):
        svc.update_participant(admin.id,clinic.id,**fields)
    db.session.refresh(admin)
    assert admin.role==ParticipantRole.ADMIN and admin.status==ParticipantStatus.ACCEPTED


def test_existing_direct_rechecks_inactive_target(context, db, clinic):
    a,b,_,_=context[0]
    svc.create_conversation(clinic.id,a.user_id,participant_user_ids=[b.user_id])
    b.user.is_active=False;db.session.commit()
    with pytest.raises(NotFoundError):
        svc.create_conversation(clinic.id,a.user_id,participant_user_ids=[b.user_id])


def test_linked_case_management_is_denied(clinic, db, make_patient, make_staff, make_conversation):
    from app.tests.modules.chat.test_case_chat_membership_sync_and_guard import _case
    from app.modules.chat.models.admission_case_conversation_model import AdmissionCaseConversation
    from app.core.auth.user.models.user_model import User
    ids=_case(clinic,make_patient,make_staff)
    owner=db.session.get(User,ids['admin'])
    c=make_conversation(clinic,owner,conversation_type=ConversationType.PATIENT,patient_id=ids['patient'])
    db.session.add(AdmissionCaseConversation(clinic_id=clinic.id,patient_id=ids['patient'],admission_id=ids['admission'],conversation_id=c.id,linked_by_user_id=owner.id))
    db.session.commit()
    with pytest.raises(NotFoundError):
        management.lock_management(clinic.id,c.id)


def test_stale_case_membership_is_excluded_before_inbox_pagination(clinic, db, make_patient, make_staff, make_conversation, make_conversation_participant):
    from app.tests.modules.chat.test_case_chat_membership_sync_and_guard import _case
    from app.modules.chat.models.admission_case_conversation_model import AdmissionCaseConversation
    from app.core.auth.user.models.user_model import User
    ids=_case(clinic,make_patient,make_staff)
    owner=db.session.get(User,ids['admin'])
    doctor=db.session.get(User,ids['doctor_user'])
    c=make_conversation(clinic,owner,conversation_type=ConversationType.PATIENT,patient_id=ids['patient'])
    db.session.add(AdmissionCaseConversation(clinic_id=clinic.id,patient_id=ids['patient'],admission_id=ids['admission'],conversation_id=c.id,linked_by_user_id=owner.id))
    make_conversation_participant(clinic,c,doctor,status=ParticipantStatus.ACCEPTED)
    db.session.commit()
    listed=svc.list_conversations(clinic.id,user_id=doctor.id)
    assert listed['items']==[] and listed['total']==0 and listed['pages']==0
