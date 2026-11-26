"""Ordinary staff chat discovery and invitation decisions; no message access grant."""
from datetime import datetime, timezone

from app.core.auth.user.models.user_model import User
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.chat_enums import ConversationStatus, ConversationType, ParticipantStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.chat.models.admission_case_conversation_model import AdmissionCaseConversation
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import ConversationParticipant
from app.modules.chat.services.chat_policy_service import ChatPolicyService
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.models.organization_model import ClinicMembership, TenantMembership
from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.staff.models.staff_model import Staff


def _eligible(clinic_id):
    return (db.select(User, Staff, ClinicMembership.role).select_from(User)
            .join(Staff, Staff.user_id == User.id)
            .join(ClinicMembership, ClinicMembership.user_id == User.id)
            .join(Clinic, Clinic.id == ClinicMembership.clinic_id)
            .join(TenantMembership, db.and_(
                TenantMembership.user_id == User.id,
                TenantMembership.organization_id == ClinicMembership.organization_id))
            .where(User.clinic_id == clinic_id, User.is_active.is_(True),
                   User.role.notin_([Role.SUPER_ADMIN, Role.PATIENT]),
                   Staff.clinic_id == clinic_id, Staff.status == StaffStatus.ACTIVE,
                   ClinicMembership.clinic_id == clinic_id,
                   ClinicMembership.organization_id == Clinic.organization_id,
                   ClinicMembership.is_active.is_(True), ClinicMembership.revoked_at.is_(None),
                   TenantMembership.is_active.is_(True), TenantMembership.status == 'active'))


def require_eligible_staff(clinic_id, user_id):
    row = db.session.execute(_eligible(clinic_id).where(User.id == user_id).execution_options(populate_existing=True)).one_or_none()
    if row is None or row[2] != row[0].role.value:
        raise NotFoundError('Chat contact unavailable')
    return row[0]


def _page(page, per_page):
    if type(page) is not int or type(per_page) is not int or page < 1 or page > 100000 or not 1 <= per_page <= 100:
        raise ValidationError('Invalid chat management pagination')


def contacts(clinic_id, actor_id, page, per_page):
    _page(page, per_page)
    ensure_clinic_active(clinic_id)
    ChatPolicyService.ensure_chat_enabled(clinic_id)
    require_eligible_staff(clinic_id, actor_id)
    # Role names in the grant are wire values, while the SQL enum uses names.
    # Filter eligibility before pagination; never return email or phone.
    role_match = db.or_(*(db.and_(User.role == r, ClinicMembership.role == r.value) for r in Role))
    stmt = _eligible(clinic_id).where(User.id != actor_id, role_match)
    rows = db.session.execute(stmt.order_by(Staff.id).offset((page - 1) * per_page).limit(per_page + 1)).all()
    return {'items': [{'user_id': u.id, 'staff_id': s.id, 'clinic_id': clinic_id,
                       'name': f'{s.first_name} {s.last_name}', 'role': u.role.value}
                      for u, s, _ in rows[:per_page]], 'page': page, 'per_page': per_page,
            'has_next': len(rows) > per_page}


def _ordinary_conditions():
    return (Conversation.conversation_type.in_([ConversationType.DIRECT, ConversationType.GROUP]),
            Conversation.patient_id.is_(None), Conversation.appointment_id.is_(None),
            Conversation.consultation_id.is_(None),
            ~db.select(AdmissionCaseConversation.id).where(
                AdmissionCaseConversation.conversation_id == Conversation.id).exists())


def ordinary(conversation):
    return (conversation.conversation_type in (ConversationType.DIRECT, ConversationType.GROUP)
            and conversation.patient_id is None and conversation.appointment_id is None
            and conversation.consultation_id is None)


def lock_management(clinic_id, conversation_id):
    """Lock parent before membership checks. Canonical case membership is assignment-owned."""
    c = db.session.execute(db.select(Conversation).where(
        Conversation.id == conversation_id, Conversation.clinic_id == clinic_id
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if c is None or db.session.scalar(db.select(AdmissionCaseConversation.id).where(
        AdmissionCaseConversation.conversation_id == conversation_id)) is not None:
        raise NotFoundError('Conversation management unavailable')
    return c


def _invitation_row(p, c):
    # No title, description, clinical context, message, or membership roster
    # prior to acceptance. An invite is not a conversation read receipt.
    return {'id': p.id, 'conversation_id': c.id, 'clinic_id': c.clinic_id,
            'user_id': p.user_id, 'conversation_type': c.conversation_type.value,
            'created_by_id': c.created_by_id, 'role': p.role.value, 'status': p.status.value}


def invitations(clinic_id, actor_id, page, per_page):
    _page(page, per_page)
    ensure_clinic_active(clinic_id)
    ChatPolicyService.ensure_chat_enabled(clinic_id)
    require_eligible_staff(clinic_id, actor_id)
    rows = db.session.execute(db.select(ConversationParticipant, Conversation)
        .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
        .where(Conversation.clinic_id == clinic_id, ConversationParticipant.clinic_id == clinic_id,
               ConversationParticipant.user_id == actor_id,
               ConversationParticipant.status == ParticipantStatus.PENDING,
               Conversation.status == ConversationStatus.ACTIVE, *_ordinary_conditions())
        .order_by(ConversationParticipant.id).offset((page - 1) * per_page).limit(per_page + 1)).all()
    return {'items': [_invitation_row(p, c) for p, c in rows[:per_page]],
            'page': page, 'per_page': per_page, 'has_next': len(rows) > per_page}


@transactional
def respond(clinic_id, actor_id, invitation_id, status):
    if status not in ('accepted', 'declined'):
        raise ValidationError('Invitation decision must be accepted or declined')
    ensure_clinic_active(clinic_id)
    ChatPolicyService.ensure_chat_enabled(clinic_id)
    require_eligible_staff(clinic_id, actor_id)
    cid = db.session.scalar(db.select(ConversationParticipant.conversation_id).where(
        ConversationParticipant.id == invitation_id, ConversationParticipant.clinic_id == clinic_id,
        ConversationParticipant.user_id == actor_id))
    if cid is None:
        raise NotFoundError('Invitation unavailable')
    c = lock_management(clinic_id, cid)
    if not ordinary(c) or c.status != ConversationStatus.ACTIVE:
        raise NotFoundError('Invitation unavailable')
    p = db.session.execute(db.select(ConversationParticipant).where(
        ConversationParticipant.id == invitation_id, ConversationParticipant.clinic_id == clinic_id,
        ConversationParticipant.conversation_id == c.id, ConversationParticipant.user_id == actor_id,
        ConversationParticipant.status == ParticipantStatus.PENDING
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if p is None:
        raise NotFoundError('Invitation unavailable')
    # Recheck actor's eligibility after waiting for the parent lock.
    require_eligible_staff(clinic_id, actor_id)
    p.status = ParticipantStatus(status)
    p.joined_at = datetime.now(timezone.utc) if status == 'accepted' else None
    p.left_at = None
    p.removed_at = None
    create_audit_log(action=AuditAction.UPDATE, entity_type='ConversationParticipant', entity_id=p.id,
                     user_id=actor_id, clinic_id=clinic_id, description='Responded to own ordinary chat invitation',
                     old_value={'status': 'pending'}, new_value={'status': status, 'conversation_id': c.id})
    db.session.flush()
    return _invitation_row(p, c)
