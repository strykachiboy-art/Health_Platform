"""S12 patient-owned read and appointment actions; reuses canonical EHR services.

No endpoint accepts a patient ID or clinic ID. The server resolves both from
its authenticated, enrolled patient and the assigned active clinic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, g
from pydantic import BaseModel, ConfigDict, Field, ValidationError as SchemaError, field_validator
from sqlalchemy import select

from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.enums.appointment_enums import AppointmentType, AppointmentStatus
from app.core.enums.billing_enums import InvoiceStatus
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import DomainError, ConflictError, ValidationError, NotFoundError
from app.core.utils.decorators import role_required, get_current_clinic_id
from app.modules.appointment.models.appointment_model import Appointment
from app.modules.appointment.services.appointment_service import (
    create_appointment, cancel_appointment, get_appointments_for_patient,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.prescription.models.prescription_model import Prescription
from app.modules.billing.models.billing_model import Invoice

patient_portal_bp = Blueprint('patient_portal', __name__, url_prefix='/patient-portal')


class PatientBookingSchema(BaseModel):
    model_config = ConfigDict(extra='forbid')
    staff_id: int = Field(gt=0, strict=True)
    scheduled_start: datetime
    scheduled_end: datetime
    appointment_type: AppointmentType = AppointmentType.IN_PERSON
    reason: str | None = Field(default=None, max_length=255)

    @field_validator('reason')
    @classmethod
    def clean_reason(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError('Empty reason is not permitted')
        return value


class PatientCancelSchema(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cancellation_reason: str = Field(min_length=5, max_length=255)

    @field_validator('cancellation_reason')
    @classmethod
    def clean_reason(cls, value):
        value = value.strip()
        if len(value) < 5:
            raise ValueError('A cancellation reason is required')
        return value


def _patient_context():
    user = db.session.get(User, g.current_user_id)
    if user is None or not user.is_active or user.role is not Role.PATIENT:
        raise NotFoundError('Patient account unavailable')
    patient = user.patient
    if patient is None or not patient.is_active:
        raise ConflictError('Clinical enrollment required', code='PATIENT_ENROLLMENT_REQUIRED')
    clinic_id = get_current_clinic_id(required=True)
    if not user.clinic_id or patient.clinic_id != clinic_id or user.clinic_id != clinic_id:
        raise ConflictError('Patient clinic enrollment mismatch', code='PATIENT_ENROLLMENT_REQUIRED')
    return user, patient, clinic_id


def _response(data, status=200):
    return jsonify({'success': True, 'data': data}), status


def _failure(exc):
    if isinstance(exc, DomainError):
        body = {'success': False, 'error': str(exc)}
        if exc.code:
            body['code'] = exc.code
        return jsonify(body), exc.status_code
    if isinstance(exc, SchemaError):
        # Do not echo raw submitted clinical data from error contexts.
        return jsonify({'success': False, 'error': 'Invalid request parameters'}), 422
    raise exc


def _parse_page():
    raw = request.args.to_dict()
    if set(raw) - {'page'}:
        raise ValidationError('Unsupported query parameters')
    page = raw.get('page', '1')
    if not page.isdecimal() or not 1 <= int(page) <= 10000:
        raise ValidationError('Invalid page')
    return int(page)


def _appointment_row(item):
    # No staff clinical notes, private Google calendar identifiers, or
    # arbitrary patient records in the patient-facing response.
    return {
        'id': item.id,
        'status': item.status.value,
        'appointment_type': item.appointment_type.value,
        'staff_id': item.staff_id,
        'scheduled_start': item.scheduled_start.isoformat(),
        'scheduled_end': item.scheduled_end.isoformat(),
        'reason': item.reason,
    }


def _booking_times(start, end):
    if start.tzinfo is None or end.tzinfo is None or start.utcoffset() is None or end.utcoffset() is None:
        raise ValidationError('Appointment times require a timezone offset')
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    duration = end_utc - start_utc
    if start_utc < now + timedelta(minutes=15) or start_utc > now + timedelta(days=90):
        raise ValidationError('Appointment start outside allowed booking window')
    if duration < timedelta(minutes=15) or duration > timedelta(hours=2):
        raise ValidationError('Appointment duration outside allowed range')
    # The canonical Appointment columns currently use a naive UTC DateTime.
    return start_utc.replace(tzinfo=None), end_utc.replace(tzinfo=None)


def _doctor(staff_id, clinic_id):
    row = db.session.execute(
        select(Staff).join(User, Staff.user_id == User.id).where(
            Staff.id == staff_id,
            Staff.clinic_id == clinic_id,
            Staff.status == StaffStatus.ACTIVE,
            User.role == Role.DOCTOR,
            User.is_active.is_(True),
            User.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError('Clinician unavailable')
    return row


@patient_portal_bp.get('/me')
@role_required(Role.PATIENT)
def me():
    try:
        _, patient, _ = _patient_context()
        return _response({'first_name': patient.first_name, 'last_name': patient.last_name})
    except (DomainError, SchemaError) as exc:
        return _failure(exc)


@patient_portal_bp.get('/clinicians')
@role_required(Role.PATIENT)
def clinicians():
    try:
        _, _, clinic_id = _patient_context()
        rows = db.session.execute(
            select(Staff).join(User, Staff.user_id == User.id).where(
                Staff.clinic_id == clinic_id,
                Staff.status == StaffStatus.ACTIVE,
                User.role == Role.DOCTOR,
                User.is_active.is_(True),
                User.clinic_id == clinic_id,
            ).order_by(Staff.last_name, Staff.first_name, Staff.id).limit(100)
        ).scalars().all()
        return _response({'items': [
            {'id': row.id, 'name': f'{row.first_name} {row.last_name}', 'specialty': row.specialty}
            for row in rows
        ], 'availability_verified': False})
    except (DomainError, SchemaError) as exc:
        return _failure(exc)


@patient_portal_bp.get('/appointments')
@role_required(Role.PATIENT)
def appointments():
    try:
        _, patient, clinic_id = _patient_context()
        page = _parse_page()
        result = get_appointments_for_patient(patient.id, clinic_id=clinic_id, page=page, per_page=30)
        return _response({'items': [_appointment_row(a) for a in result.items],
                          'page': result.page, 'has_next': result.has_next})
    except (DomainError, SchemaError) as exc:
        return _failure(exc)


@patient_portal_bp.post('/appointments')
@role_required(Role.PATIENT)
def book():
    try:
        user, patient, clinic_id = _patient_context()
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            raise ValidationError('Invalid booking request')
        payload = PatientBookingSchema.model_validate(raw)
        if payload.appointment_type not in (AppointmentType.IN_PERSON, AppointmentType.TELEMEDICINE, AppointmentType.FOLLOW_UP):
            raise ValidationError('Unsupported self-service appointment type')
        _doctor(payload.staff_id, clinic_id)
        start, end = _booking_times(payload.scheduled_start, payload.scheduled_end)
        key = request.headers.get('Idempotency-Key', '')
        if not isinstance(key, str) or not (16 <= len(key) <= 128) or not key.isascii() or not all(c.isalnum() or c in '-_' for c in key):
            raise ValidationError('Valid Idempotency-Key required')
        appointment = create_appointment(
            clinic_id=clinic_id, patient_id=patient.id, staff_id=payload.staff_id,
            scheduled_start=start, scheduled_end=end,
            appointment_type=payload.appointment_type, reason=payload.reason,
            notes=None, idempotency_key=key, idempotency_user_id=user.id,
        )
        return _response(_appointment_row(appointment), 201)
    except (DomainError, SchemaError) as exc:
        return _failure(exc)


@patient_portal_bp.post('/appointments/<int:appointment_id>/cancel')
@role_required(Role.PATIENT)
def cancel(appointment_id):
    try:
        _, patient, clinic_id = _patient_context()
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            raise ValidationError('Invalid cancellation request')
        payload = PatientCancelSchema.model_validate(raw)
        current = db.session.execute(select(Appointment).where(
            Appointment.id == appointment_id, Appointment.patient_id == patient.id,
            Appointment.clinic_id == clinic_id,
        ).with_for_update()).scalar_one_or_none()
        if current is None:
            raise NotFoundError('Appointment unavailable')
        # Self-service cancellation is restricted to unconfirmed future requests.
        # Confirmed visits require the clinic's human scheduling process.
        if current.status is not AppointmentStatus.SCHEDULED:
            raise ConflictError('Contact the clinic to change this appointment')
        start = current.scheduled_start
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= datetime.now(timezone.utc) + timedelta(hours=2):
            raise ConflictError('Contact the clinic for near-term changes')
        # Existing canonical status transition and audit remain authoritative.
        result = cancel_appointment(appointment_id, reason=payload.cancellation_reason, clinic_id=clinic_id)
        return _response(_appointment_row(result))
    except (DomainError, SchemaError) as exc:
        return _failure(exc)


@patient_portal_bp.get('/prescriptions')
@role_required(Role.PATIENT)
def prescriptions():
    try:
        _, patient, clinic_id = _patient_context()
        page = _parse_page()
        rows = db.session.execute(select(Prescription).where(
            Prescription.patient_id == patient.id, Prescription.clinic_id == clinic_id,
        ).order_by(Prescription.issued_at.desc(), Prescription.id.desc()).limit(30).offset((page - 1) * 30)).scalars().all()
        return _response({'items': [{
            'id': p.id, 'status': p.status.value,
            'issued_at': p.issued_at.isoformat() if p.issued_at else None,
            'expires_at': p.expires_at.isoformat() if p.expires_at else None,
        } for p in rows], 'page': page, 'has_next': len(rows) == 30})
    except (DomainError, SchemaError) as exc:
        return _failure(exc)


@patient_portal_bp.get('/invoices')
@role_required(Role.PATIENT)
def invoices():
    try:
        _, patient, clinic_id = _patient_context()
        page = _parse_page()
        rows = db.session.execute(select(Invoice).where(
            Invoice.patient_id == patient.id, Invoice.clinic_id == clinic_id,
            Invoice.status.in_((InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.PAID, InvoiceStatus.OVERDUE)),
        ).order_by(Invoice.created_at.desc(), Invoice.id.desc()).limit(30).offset((page - 1) * 30)).scalars().all()
        return _response({'items': [{
            'id': i.id, 'invoice_number': i.invoice_number,
            'status': i.status.value, 'total_amount': str(i.total_amount),
            'amount_paid': str(i.amount_paid), 'currency': i.currency,
            'due_date': i.due_date.isoformat() if i.due_date else None,
        } for i in rows], 'page': page, 'has_next': len(rows) == 30})
    except (DomainError, SchemaError) as exc:
        return _failure(exc)
