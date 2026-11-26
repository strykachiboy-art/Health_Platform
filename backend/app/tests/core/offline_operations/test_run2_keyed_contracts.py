"""RUN2 backend: opt-in keys, not permission to auto-replay."""
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from app.extensions import db
from sqlalchemy import text
from app.core.enums.role_enums import Role
from app.core.enums.chat_enums import ConversationType, MessagePriority, MessageType
from app.core.exceptions import ConflictError, ValidationError, NotFoundError
from app.core.idempotency.models.idempotency_model import IdempotencyRecord
from app.modules.feedback.schemas.feedback_schema import FeedbackCreateSchema
from app.modules.feedback.models.feedback_model import Feedback
from app.modules.feedback.services.feedback_service import create_feedback
from app.modules.chat.models.message_model import Message
from app.modules.chat.models.chat_outbox_model import ChatOutbox
from app.modules.chat.services.conversation_service import create_conversation
from app.modules.chat.services.message_service import create_message


def payload(**extra):
    return FeedbackCreateSchema.model_validate(dict(feedback_type='bug_report', category='usability', subject='Synthetic usability issue', message='Synthetic description', **extra))


def test_runtime_registration(app):
    rules=[dict(rule=r.rule, endpoint=r.endpoint, methods=sorted(r.methods-{'HEAD','OPTIONS'})) for r in app.url_map.iter_rules()]
    for route in ('/api/v1/feedback','/api/v1/chat/conversations/<int:conversation_id>/messages','/api/v1/patient-portal/appointments'):
        assert any(r['rule']==route and 'POST' in r['methods'] for r in rules), route
    output=os.environ.get('RUN2_RUNTIME_MAP')
    if output: Path(output).write_text(json.dumps(rules,indent=2),encoding='utf-8')


def test_feedback_key_replay_and_conflict(clinic,user):
    kwargs=dict(clinic_id=clinic.id,actor_user_id=user.id,idempotency_key='run2-feedback-key-0001')
    first=create_feedback(payload=payload(),**kwargs)
    again=create_feedback(payload=payload(),**kwargs)
    assert first.id==again.id
    assert db.session.query(Feedback).filter_by(clinic_id=clinic.id,submitted_by_user_id=user.id).count()==1
    changed=payload().model_copy(update={'message':'Different description'})
    with pytest.raises(ConflictError):create_feedback(payload=changed,**kwargs)
    assert db.session.query(Feedback).filter_by(clinic_id=clinic.id,submitted_by_user_id=user.id).count()==1


def test_feedback_key_scope_and_legacy(clinic,user,make_user,make_clinic):
    other=make_user(clinic=clinic)
    key='run2-feedback-key-0002'
    a=create_feedback(clinic_id=clinic.id,actor_user_id=user.id,payload=payload(),idempotency_key=key)
    b=create_feedback(clinic_id=clinic.id,actor_user_id=other.id,payload=payload(),idempotency_key=key)
    assert a.id!=b.id
    with pytest.raises(NotFoundError):
        create_feedback(clinic_id=make_clinic().id,actor_user_id=user.id,payload=payload(),idempotency_key=key)
    x=create_feedback(actor_user_id=user.id,payload=payload())
    y=create_feedback(actor_user_id=user.id,payload=payload())
    assert x.id!=y.id


def test_feedback_key_rejects_sensitive_category_and_blank(clinic,user):
    kwargs=dict(clinic_id=clinic.id,actor_user_id=user.id)
    db.session.commit()
    with pytest.raises(ValidationError):create_feedback(payload=payload().model_copy(update={'category':'clinical_workflow'}),idempotency_key='run2-deny-category',**kwargs)
    with pytest.raises(ValidationError):create_feedback(payload=payload(),idempotency_key=' ',**kwargs)
    assert db.session.query(Feedback).filter_by(clinic_id=clinic.id).count()==0


def setup_chat(clinic,user,make_user,make_clinic_settings):
    make_clinic_settings(clinic=clinic,feature_flags={'chat_enabled':True})
    other=make_user(clinic=clinic)
    conv=create_conversation(clinic_id=clinic.id,created_by_id=user.id,conversation_type=ConversationType.GROUP,participant_user_ids=[other.id])
    return conv


def test_chat_key_replay_conflict_one_outbox(clinic,user,make_user,make_clinic_settings):
    conv=setup_chat(clinic,user,make_user,make_clinic_settings)
    kwargs=dict(clinic_id=clinic.id,conversation_id=conv.id,sender_id=user.id,idempotency_key='run2-chat-key-0001')
    a=create_message(content='Synthetic ordinary text',**kwargs)
    b=create_message(content='Synthetic ordinary text',**kwargs)
    assert a.id==b.id
    assert db.session.query(Message).filter_by(conversation_id=conv.id).count()==1
    assert db.session.query(ChatOutbox).filter_by(message_id=a.id).count()==1
    with pytest.raises(ConflictError):create_message(content='Different text',**kwargs)
    assert db.session.query(Message).filter_by(conversation_id=conv.id).count()==1
    assert db.session.query(ChatOutbox).filter_by(message_id=a.id).count()==1


def test_chat_key_urgent_and_media_denied(clinic,user,make_user,make_clinic_settings):
    conv=setup_chat(clinic,user,make_user,make_clinic_settings)
    kwargs=dict(clinic_id=clinic.id,conversation_id=conv.id,sender_id=user.id,idempotency_key='run2-chat-key-0002',content='Synthetic ordinary text')
    with pytest.raises(ValidationError):create_message(priority=MessagePriority.URGENT,**kwargs)
    with pytest.raises(ValidationError):create_message(message_type=MessageType.IMAGE,**kwargs)
    assert db.session.query(Message).filter_by(conversation_id=conv.id).count()==0


def test_chat_key_closed_conversation_denied(clinic,user,make_user,make_clinic_settings):
    from app.core.enums.chat_enums import ConversationStatus
    from app.core.exceptions import DomainError
    conv=setup_chat(clinic,user,make_user,make_clinic_settings)
    kwargs=dict(clinic_id=clinic.id,conversation_id=conv.id,sender_id=user.id,idempotency_key='run2-chat-key-0003',content='Synthetic ordinary text')
    create_message(**kwargs)
    conv.status=ConversationStatus.CLOSED;db.session.commit()
    with pytest.raises(DomainError):create_message(**kwargs)
    assert db.session.query(Message).filter_by(conversation_id=conv.id).count()==1


def grant_headers(app,clinic,user):
    from app.modules.clinic.models.organization_model import TenantMembership, ClinicMembership
    from app.core.auth.user.services.user_service import issue_auth_tokens
    db.session.add(TenantMembership(user_id=user.id,organization_id=clinic.organization_id,role='member',is_active=True,status='active'))
    db.session.flush()
    db.session.add(ClinicMembership(user_id=user.id,organization_id=clinic.organization_id,clinic_id=clinic.id,role=user.role.value,is_active=True))
    db.session.commit()
    with app.test_request_context():pair=issue_auth_tokens(user,clinic_context_id=clinic.id)
    return {'Authorization':'Bearer '+pair['access_token']}


def test_feedback_http_keyed(app,client,clinic,user):
    headers={**grant_headers(app,clinic,user),'Idempotency-Key':'run2-feedback-http-001'}
    url='/api/v1/feedback';body=payload().model_dump(mode='json')
    a=client.post(url,json=body,headers=headers);b=client.post(url,json=body,headers=headers)
    assert a.status_code==b.status_code==201,(a.status_code,b.status_code)
    assert a.json['data']['id']==b.json['data']['id']
    assert client.post(url,json={**body,'message':'changed'},headers=headers).status_code==409
    assert client.post(url,json=body).status_code==401


def test_chat_http_keyed(app,client,clinic,user,make_user,make_clinic_settings):
    conv=setup_chat(clinic,user,make_user,make_clinic_settings)
    headers={**grant_headers(app,clinic,user),'Idempotency-Key':'run2-chat-http-001'}
    url=f'/api/v1/chat/conversations/{conv.id}/messages';body={'content':'Synthetic ordinary text','message_type':'text','priority':'normal'}
    a=client.post(url,json=body,headers=headers);b=client.post(url,json=body,headers=headers)
    assert a.status_code==b.status_code==201,(a.status_code,b.status_code)
    assert a.json['data']['id']==b.json['data']['id']
    assert client.post(url,json={**body,'content':'changed'},headers=headers).status_code==409
    assert client.post(url,json=body).status_code==401


@pytest.mark.parametrize('kind',['feedback','chat'])
def test_postgres_parallel_same_key(app,clinic,user,make_user,make_clinic_settings,kind):
    assert db.engine.dialect.name=='postgresql'
    cid,uid=clinic.id,user.id
    conv=setup_chat(clinic,user,make_user,make_clinic_settings) if kind=='chat' else None
    vid=conv.id if conv else None
    db.session.commit();gate=Barrier(2)
    def send():
        with app.app_context():
            pid=db.session.execute(text('select pg_backend_pid()')).scalar_one()
            gate.wait(timeout=30)
            if kind=='feedback':obj=create_feedback(clinic_id=cid,actor_user_id=uid,payload=payload(),idempotency_key='run2-pg-feedback-001')
            else:obj=create_message(clinic_id=cid,sender_id=uid,conversation_id=vid,content='Synthetic ordinary text',idempotency_key='run2-pg-chat-001')
            return pid,obj.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        a,b=[f.result(timeout=90) for f in [pool.submit(send),pool.submit(send)]]
    assert a[0]!=b[0] and a[1]==b[1]
    db.session.expire_all()
    if kind=='feedback':assert db.session.query(Feedback).filter_by(clinic_id=cid).count()==1
    else:
        assert db.session.query(Message).filter_by(conversation_id=vid).count()==1
        assert db.session.query(ChatOutbox).filter_by(message_id=a[1]).count()==1
    assert db.session.query(IdempotencyRecord).filter_by(clinic_id=cid,user_id=uid,operation=('feedback.create' if kind=='feedback' else 'chat.message.create')).count()==1


def test_postgres_rollback_key_and_effect(app,clinic,user,make_user,make_clinic_settings,monkeypatch):
    assert db.engine.dialect.name=='postgresql'
    from app.modules.chat.services import message_service
    conv=setup_chat(clinic,user,make_user,make_clinic_settings)
    kwargs=dict(clinic_id=clinic.id,conversation_id=conv.id,sender_id=user.id,idempotency_key='run2-pg-rollback-001',content='Synthetic ordinary text')
    original=message_service.create_outbox_event
    def broken(**args):
        original(**args)
        raise RuntimeError('Synthetic interrupted transaction')
    with monkeypatch.context() as m:
        m.setattr(message_service,'create_outbox_event',broken)
        with pytest.raises(RuntimeError,match='Synthetic interrupted transaction'):
            create_message(**kwargs)
    assert db.session.query(IdempotencyRecord).filter_by(operation='chat.message.create',idempotency_key=kwargs['idempotency_key']).count()==0
    assert db.session.query(Message).filter_by(conversation_id=conv.id).count()==0
    assert db.session.query(ChatOutbox).filter_by(clinic_id=clinic.id).count()==0
    row=create_message(**kwargs)
    assert db.session.query(ChatOutbox).filter_by(message_id=row.id).count()==1


def test_postgres_feedback_rollback_key_and_effect(app,clinic,user,monkeypatch):
    assert db.engine.dialect.name=='postgresql'
    from app.modules.feedback.services import feedback_service
    kwargs=dict(clinic_id=clinic.id,actor_user_id=user.id,payload=payload(),idempotency_key='run2-pg-feedback-rollback')
    db.session.commit()
    original=feedback_service._create_feedback_audit
    def broken(**args):
        original(**args)
        raise RuntimeError('Synthetic interrupted transaction')
    with monkeypatch.context() as m:
        m.setattr(feedback_service,'_create_feedback_audit',broken)
        with pytest.raises(RuntimeError,match='Synthetic interrupted transaction'):
            create_feedback(**kwargs)
    assert db.session.query(IdempotencyRecord).filter_by(operation='feedback.create',idempotency_key=kwargs['idempotency_key']).count()==0
    assert db.session.query(Feedback).filter_by(clinic_id=clinic.id).count()==0
    assert create_feedback(**kwargs).id>0


def test_patient_booking_existing_idempotency(app,client,clinic,make_user,make_patient,make_staff):
    from datetime import datetime,timedelta,timezone
    from app.modules.appointment.models.appointment_model import Appointment
    patient_user=make_user(clinic,role=Role.PATIENT)
    patient=make_patient(clinic,user_id=patient_user.id)
    doctor=make_staff(clinic,role=Role.DOCTOR)
    headers={**grant_headers(app,clinic,patient_user),'Idempotency-Key':'run2-patient-booking-001'}
    start=(datetime.now(timezone.utc)+timedelta(days=7)).replace(hour=10,minute=0,second=0,microsecond=0)
    body={'staff_id':doctor.id,'scheduled_start':start.isoformat(),'scheduled_end':(start+timedelta(minutes=30)).isoformat(),'appointment_type':'in_person'}
    url='/api/v1/patient-portal/appointments'
    a=client.post(url,json=body,headers=headers);b=client.post(url,json=body,headers=headers)
    assert a.status_code==b.status_code==201,(a.status_code,b.status_code)
    assert a.json['data']['id']==b.json['data']['id']
    assert db.session.query(Appointment).filter_by(clinic_id=clinic.id,patient_id=patient.id).count()==1
    assert client.post(url,json={**body,'reason':'Different request'},headers=headers).status_code==409
    assert db.session.query(Appointment).filter_by(clinic_id=clinic.id,patient_id=patient.id).count()==1
