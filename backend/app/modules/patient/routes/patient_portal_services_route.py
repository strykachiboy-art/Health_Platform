"""S13: patient-owned prescription details, billing detail, sandbox checkout, care chat.

These endpoints do not accept a patient or clinic ID. Do not expand generic staff
billing or internal admission-case chat authorization.
"""
from __future__ import annotations

import os
from decimal import Decimal
from flask import Blueprint, jsonify, request, g
from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field, ValidationError as SchemaError

from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.enums.billing_enums import InvoiceStatus, PaymentMethod, PaymentGateway
from app.core.enums.chat_enums import ConversationType, ConversationStatus, ParticipantStatus, MessageType, MessageStatus
from app.core.exceptions import DomainError, NotFoundError, ValidationError, ServiceUnavailableError
from app.core.utils.decorators import role_required
from app.modules.patient.routes.patient_portal_route import _patient_context, _response, _failure
from app.modules.prescription.models.prescription_model import Prescription, PrescriptionItem
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.billing.models.billing_model import Invoice, InvoiceItem, Payment
from app.modules.billing.services.payment_orchestration_service import orchestrate_payment
from app.modules.billing.services.gateways.factory import get_payment_gateway
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import ConversationParticipant
from app.modules.chat.models.admission_case_conversation_model import AdmissionCaseConversation
from app.modules.chat.services.chat_policy_service import ChatPolicyService
from app.modules.chat.services.chat_security_service import ChatSecurityService
from app.modules.chat.services.message_service import list_messages, create_message

patient_portal_services_bp = Blueprint('patient_portal_services', __name__, url_prefix='/patient-portal')


class _CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    gateway: str = Field(pattern=r'^paystack$')


class _MessageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=2000)


def _safe(handler):
    try:
        return handler()
    except (DomainError, SchemaError) as exc:
        return _failure(exc)


def _own_prescription(record_id):
    _, patient, clinic_id = _patient_context()
    record = db.session.execute(select(Prescription).where(
        Prescription.id == record_id,
        Prescription.patient_id == patient.id,
        Prescription.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if record is None:
        raise NotFoundError('Prescription unavailable')
    return record, clinic_id


def _own_invoice(record_id):
    _, patient, clinic_id = _patient_context()
    record = db.session.execute(select(Invoice).where(
        Invoice.id == record_id,
        Invoice.patient_id == patient.id,
        Invoice.clinic_id == clinic_id,
        Invoice.status.in_((InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID,
                            InvoiceStatus.PAID, InvoiceStatus.OVERDUE)),
    )).scalar_one_or_none()
    if record is None:
        raise NotFoundError('Invoice unavailable')
    return record, clinic_id


def _sandbox_ready(clinic_id):
    # Two independent gates: an explicit operator opt-in AND provider TEST key.
    # Never allow the Patient portal to initiate a real-money charge here.
    if os.environ.get('S13_PATIENT_CHECKOUT_SANDBOX_ENABLED') != '1':
        return False
    try:
        provider = get_payment_gateway(PaymentGateway.PAYSTACK, clinic_id=clinic_id)
        return isinstance(getattr(provider, 'secret_key', None), str) and provider.secret_key.startswith('sk_test_')
    except (ValueError, DomainError, KeyError):
        return False


@patient_portal_services_bp.get('/prescriptions/<int:record_id>')
@role_required(Role.PATIENT)
def prescription_detail(record_id):
    def run():
        record, clinic_id = _own_prescription(record_id)
        items = db.session.execute(select(PrescriptionItem).where(
            PrescriptionItem.prescription_id == record.id,
        ).order_by(PrescriptionItem.id).limit(100)).scalars().all()
        drug_ids = {item.drug_id for item in items}
        drugs = {}
        if drug_ids:
            rows = db.session.execute(select(Drug).where(Drug.id.in_(drug_ids))).scalars().all()
            drugs = {row.id: row for row in rows if row.clinic_id is None or row.clinic_id == clinic_id}
        return _response({
            'id': record.id, 'status': record.status.value,
            'issued_at': record.issued_at.isoformat() if record.issued_at else None,
            'expires_at': record.expires_at.isoformat() if record.expires_at else None,
            'items': [{
                'id': item.id,
                'medicine': drugs[item.drug_id].name if item.drug_id in drugs else 'Medicine unavailable',
                'strength': drugs[item.drug_id].strength if item.drug_id in drugs else None,
                'dosage': item.dosage,
                'frequency': item.frequency,
                'duration': item.duration,
                'quantity': item.quantity,
                'route': item.route_display,
                'instructions': item.instructions,
            } for item in items],
        })
    return _safe(run)


@patient_portal_services_bp.get('/invoices/<int:record_id>')
@role_required(Role.PATIENT)
def invoice_detail(record_id):
    def run():
        record, clinic_id = _own_invoice(record_id)
        items = db.session.execute(select(InvoiceItem).where(
            InvoiceItem.invoice_id == record.id,
        ).order_by(InvoiceItem.id).limit(100)).scalars().all()
        payments = db.session.execute(select(Payment).where(
            Payment.invoice_id == record.id,
        ).order_by(Payment.created_at.desc(), Payment.id.desc()).limit(50)).scalars().all()
        balance = Decimal(str(record.total_amount)) - Decimal(str(record.amount_paid))
        may_pay = (record.status in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID,
                                    InvoiceStatus.OVERDUE) and balance > 0
                   and record.currency == 'NGN' and _sandbox_ready(clinic_id))
        return _response({
            'id': record.id, 'invoice_number': record.invoice_number,
            'status': record.status.value, 'currency': record.currency,
            'total_amount': str(record.total_amount), 'amount_paid': str(record.amount_paid),
            'outstanding_balance': str(balance), 'sandbox_checkout_available': may_pay,
            'items': [{
                'description': row.description, 'quantity': row.quantity,
                'unit_price': str(row.unit_price), 'subtotal': str(row.subtotal),
            } for row in items],
            'payments': [{
                'id': pay.id, 'amount': str(pay.amount), 'currency': pay.currency,
                'method': pay.method.value, 'status': pay.status.value,
                'created_at': pay.created_at.isoformat() if pay.created_at else None,
            } for pay in payments],
        })
    return _safe(run)


@patient_portal_services_bp.post('/invoices/<int:record_id>/checkout')
@role_required(Role.PATIENT)
def start_checkout(record_id):
    def run():
        user, _, _ = _patient_context()
        invoice, clinic_id = _own_invoice(record_id)
        if not _sandbox_ready(clinic_id):
            raise ServiceUnavailableError('Patient sandbox checkout not enabled')
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValidationError('Invalid checkout request')
        _CheckoutRequest.model_validate(body)
        key = request.headers.get('Idempotency-Key', '')
        if not isinstance(key, str) or not (16 <= len(key) <= 128) or not key.isascii() or not all(c.isalnum() or c in '-_' for c in key):
            raise ValidationError('Valid checkout idempotency key required')
        if invoice.status not in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.OVERDUE) or invoice.currency != 'NGN':
            raise ValidationError('Invoice is not eligible for sandbox checkout')
        balance = Decimal(str(invoice.total_amount)) - Decimal(str(invoice.amount_paid))
        if balance <= 0:
            raise ValidationError('Invoice has no payable balance')
        result = orchestrate_payment(
            clinic_id=clinic_id, invoice_id=invoice.id,
            amount=balance, method=PaymentMethod.CARD, gateway=PaymentGateway.PAYSTACK,
            currency='NGN', idempotency_key=key, idempotency_user_id=user.id,
            actor_user_id=user.id, require_test_credentials=True,
        )
        if result['outcome'] == 'failed':
            raise ValidationError('Sandbox checkout rejected by provider')
        payment = result['payment']
        url = result.get('checkout_url')
        # Never claim successful payment based on an initialization response.
        return _response({
            'payment_id': payment.id,
            'outcome': result['outcome'],
            'checkout_url': url if isinstance(url, str) else None,
        }, 200 if result['outcome'] == 'successful' else 202)
    return _safe(run)


def _own_conversation(conversation_id):
    user, patient, clinic_id = _patient_context()
    # Internal admission-case chat is STAFF ONLY even when patient_id matches.
    # Patient must be explicitly accepted as a participant on a patient-type chat.
    conversation = db.session.execute(select(Conversation).join(
        ConversationParticipant,
        ConversationParticipant.conversation_id == Conversation.id,
    ).where(
        Conversation.id == conversation_id,
        Conversation.clinic_id == clinic_id,
        Conversation.patient_id == patient.id,
        Conversation.conversation_type == ConversationType.PATIENT,
        Conversation.status == ConversationStatus.ACTIVE,
        ConversationParticipant.clinic_id == clinic_id,
        ConversationParticipant.user_id == user.id,
        ConversationParticipant.status == ParticipantStatus.ACCEPTED,
        ~Conversation.id.in_(select(AdmissionCaseConversation.conversation_id)),
    )).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError('Care conversation unavailable')
    ChatPolicyService.ensure_chat_enabled(clinic_id)
    ChatSecurityService.ensure_user_can_access_conversation(user.id, conversation.id)
    return user, clinic_id, conversation


@patient_portal_services_bp.get('/conversations')
@role_required(Role.PATIENT)
def my_care_conversations():
    def run():
        user, patient, clinic_id = _patient_context()
        if request.args:
            raise ValidationError('Unsupported care conversation query')
        ChatPolicyService.ensure_chat_enabled(clinic_id)
        records = db.session.execute(select(Conversation).join(
            ConversationParticipant,
            ConversationParticipant.conversation_id == Conversation.id,
        ).where(
            Conversation.clinic_id == clinic_id,
            Conversation.patient_id == patient.id,
            Conversation.conversation_type == ConversationType.PATIENT,
            Conversation.status == ConversationStatus.ACTIVE,
            ConversationParticipant.user_id == user.id,
            ConversationParticipant.clinic_id == clinic_id,
            ConversationParticipant.status == ParticipantStatus.ACCEPTED,
            ~Conversation.id.in_(select(AdmissionCaseConversation.conversation_id)),
        ).order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(30)).scalars().all()
        return _response({'items': [{
            'id': item.id, 'title': item.title or 'Care conversation',
            'updated_at': item.updated_at.isoformat() if item.updated_at else None,
        } for item in records]})
    return _safe(run)


@patient_portal_services_bp.get('/conversations/<int:conversation_id>/messages')
@role_required(Role.PATIENT)
def my_care_messages(conversation_id):
    def run():
        user, clinic_id, _ = _own_conversation(conversation_id)
        if request.args:
            raise ValidationError('Unsupported care message query')
        page = list_messages(clinic_id=clinic_id, conversation_id=conversation_id,
                             user_id=user.id, page=1, per_page=30, message_type=MessageType.TEXT)
        return _response({'items': [{
            'id': row.id, 'sender_is_me': row.sender_id == user.id,
            'content': row.content, 'created_at': row.created_at.isoformat() if row.created_at else None,
        } for row in reversed(page.items) if row.status != MessageStatus.DELETED and row.content]})
    return _safe(run)


@patient_portal_services_bp.post('/conversations/<int:conversation_id>/messages')
@role_required(Role.PATIENT)
def send_care_message(conversation_id):
    def run():
        user, clinic_id, _ = _own_conversation(conversation_id)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValidationError('Invalid care message')
        payload = _MessageRequest.model_validate(body)
        content = payload.content.strip()
        if not content:
            raise ValidationError('Empty care message')
        msg = create_message(clinic_id=clinic_id, conversation_id=conversation_id,
                             sender_id=user.id, content=content, message_type=MessageType.TEXT)
        return _response({'id': msg.id, 'status': msg.status.value}, 201)
    return _safe(run)
