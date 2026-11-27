"""Explicit staged source enrichment; never infer clinical conclusions or priority."""
from datetime import date, datetime
import json
from sqlalchemy import select
from flask import current_app
from app.extensions import db
from .print_policy import PrintDenied

MISSING = 'Not recorded'


def enabled():
    if current_app.config.get('PRINT_HOSPITAL_GRADE_STAGED_ENABLED') is not True:
        return False
    if not current_app.testing or current_app.config.get('PRINT_BRANDING_STAGED_ENABLED') is not True:
        raise PrintDenied('Hospital-grade printing unavailable')
    return True


def stamp(value):
    """Preserve the recorded clock, offset and precision; do not guess a timezone."""
    if value is None:
        return MISSING
    if not isinstance(value, datetime):
        raise PrintDenied('Invalid recorded timestamp')
    months = 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
    clock = f'{value.hour % 12 or 12:02d}:{value.minute:02d}'
    if value.second or value.microsecond:
        clock += f':{value.second:02d}'
    if value.microsecond:
        clock += f'.{value.microsecond:06d}'
    offset = value.strftime('%z')
    zone = 'UTC' + offset[:3] + ':' + offset[3:] if offset else '(offset not recorded)'
    return f'{value.day:02d} {months[value.month-1]} {value.year}, {clock} {"AM" if value.hour < 12 else "PM"} {zone}'


def _local(model, identifier, clinic_id):
    row = db.session.execute(select(model).where(model.id == identifier, model.clinic_id == clinic_id)).scalar_one_or_none()
    if row is None:
        raise PrintDenied('Related print source unavailable')
    return row


def _name(row):
    parts = [row.first_name, row.last_name]
    if not all(isinstance(p, str) and p.strip() for p in parts):
        raise PrintDenied('Related identity unavailable')
    return ' '.join(parts)


def patient_fields(patient_id, clinic_id, at=None):
    from app.modules.patient.models.patient_model import Patient
    patient = _local(Patient, patient_id, clinic_id)
    result = {'patient_name': _name(patient)}
    if at is not None:
        dob = patient.date_of_birth
        if isinstance(dob, datetime):
            dob = dob.date()
        day = at.date() if isinstance(at, datetime) else at
        age = MISSING
        if isinstance(dob, date) and isinstance(day, date) and dob <= day:
            age = str(day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))) + ' years'
        result.update(age_at_order=age, recorded_gender=str(getattr(patient.gender, 'value', patient.gender)) if patient.gender is not None else MISSING)
    return result


def staff_name(identifier, clinic_id):
    from app.modules.staff.models.staff_model import Staff
    return MISSING if identifier is None else _name(_local(Staff, identifier, clinic_id))


def lab_tests(order):
    from app.modules.lab.models.lab_model import LabOrderItem, LabTest
    items = db.session.execute(select(LabOrderItem, LabTest).outerjoin(LabTest, LabTest.id == LabOrderItem.test_id).where(LabOrderItem.order_id == order.id).order_by(LabOrderItem.id).limit(101)).all()
    if not items or len(items) > 100:
        raise PrintDenied('Ordered test inventory unavailable')
    rows = []
    for _, test in items:
        if test is None or test.clinic_id not in (None, order.clinic_id):
            raise PrintDenied('Foreign laboratory test')
        rows.append([test.name, str(getattr(test.sample_type, 'value', test.sample_type)) if test.sample_type is not None else MISSING, MISSING])
    return json.dumps(rows, ensure_ascii=False, separators=(',', ':'))


def radiology_fields(order, staff_id, *, staged=False):
    # A private draft has an author, NOT necessarily a credentialed radiologist.
    return {**patient_fields(order.patient_id, order.clinic_id),
            'modality': 'ct' if staged else str(getattr(order.modality, 'value', order.modality)),
            'body_part': order.body_site or MISSING,
            'report_author': staff_name(staff_id, order.clinic_id),
            'signature_status': 'Unsigned controlled copy'}
