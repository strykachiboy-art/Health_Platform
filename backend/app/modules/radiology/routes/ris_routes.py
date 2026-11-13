"""RAD1 clinic-scoped RIS status and working-note API; no signed diagnostics.

Documentation-only. Unknown write outcomes require authenticated GET readback;
no idempotency key, automatic HTTP replay, or client-supplied identity authority.
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request
from flask_jwt_extended import get_jwt
from sqlalchemy import select

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import NotFoundError, ValidationError
from app.core.utils.decorators import role_required, active_staff_required, get_current_clinic_id
from app.extensions import db
from app.modules.staff.models.staff_model import Staff
from app.modules.radiology.schemas.ris_schema import (
    CreateExamBody, ScheduleIntentBody, DraftReportBody, ReviseReportBody,
)
from app.modules.radiology.services import ris_service as ris

radiology_bp = Blueprint('radiology', __name__, url_prefix='/radiology')


@radiology_bp.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    return response


def _actor():
    clinic_id = get_current_clinic_id(required=True)
    user_id = getattr(g, 'current_user_id', None)
    if type(user_id) is not int or user_id <= 0:
        raise ValidationError('Verified RIS user required')
    user = db.session.get(User, user_id)
    if user is None or not user.is_active or user.role != Role.DOCTOR:
        raise NotFoundError('RIS action unavailable')
    # Normal doctor tokens omit clinic_context_id. Inconsistent claim is denied.
    context = get_jwt().get('clinic_context_id')
    if context is not None and (type(context) is not int or context != clinic_id):
        raise NotFoundError('RIS action unavailable in selected clinic')
    if user.clinic_id != clinic_id:
        raise NotFoundError('RIS action unavailable in selected clinic')
    staff = db.session.execute(select(Staff).where(
        Staff.user_id == user.id, Staff.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if staff is None or staff.status != StaffStatus.ACTIVE:
        raise NotFoundError('Active RIS clinician required')
    return clinic_id, staff.id


def _body(schema):
    # No Idempotency-Key is accepted: RAD1 has no replay-safe write contract.
    if request.headers.get('Idempotency-Key') is not None:
        raise ValidationError('RIS mutations do not support idempotency replay')
    if not request.is_json or request.content_length is None or not (2 <= request.content_length <= 128 * 1024):
        raise ValidationError('Bounded JSON request is required')
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        raise ValidationError('JSON object request is required')
    return schema.model_validate(raw)


def _ok(data, code=200):
    return jsonify({
        'success': True, 'data': data, 'ris_foundation_only': True,
        'mwl_published': False, 'archive_verified': False,
        'diagnostic_report_signed': False, 'released_to_chart': False,
        'critical_result_delivery_authorized': False,
    }), code


@radiology_bp.post('/exams')
@role_required(Role.DOCTOR)
@active_staff_required
def establish():
    clinic_id, staff_id = _actor()
    dto = _body(CreateExamBody)
    return _ok(ris.establish_exam(imaging_order_id=dto.imaging_order_id,
        clinic_id=clinic_id, actor_staff_id=staff_id), 201)


@radiology_bp.get('/exams/by-order/<int:imaging_order_id>')
@role_required(Role.DOCTOR)
@active_staff_required
def by_order(imaging_order_id):
    clinic_id, staff_id = _actor()
    return _ok(ris.get_exam_by_order(imaging_order_id=imaging_order_id,
        clinic_id=clinic_id, actor_staff_id=staff_id))


@radiology_bp.get('/exams/<int:exam_id>')
@role_required(Role.DOCTOR)
@active_staff_required
def get_exam(exam_id):
    clinic_id, staff_id = _actor()
    return _ok(ris.get_exam(exam_id=exam_id, clinic_id=clinic_id, actor_staff_id=staff_id))


@radiology_bp.post('/exams/<int:exam_id>/schedule')
@role_required(Role.DOCTOR)
@active_staff_required
def schedule(exam_id):
    clinic_id, staff_id = _actor()
    dto = _body(ScheduleIntentBody)
    return _ok(ris.schedule_intent(exam_id=exam_id, clinic_id=clinic_id,
        actor_staff_id=staff_id, **dto.model_dump()), 201)


@radiology_bp.post('/exams/<int:exam_id>/schedule/cancel')
@role_required(Role.DOCTOR)
@active_staff_required
def cancel_schedule(exam_id):
    clinic_id, staff_id = _actor()
    from app.modules.radiology.schemas.ris_schema import _Strict, Positive
    class _Cancel(_Strict):
        expected_version: Positive
    dto = _body(_Cancel)
    return _ok(ris.cancel_schedule_intent(exam_id=exam_id, clinic_id=clinic_id,
        actor_staff_id=staff_id, expected_version=dto.expected_version))


@radiology_bp.get('/exams/<int:exam_id>/worklist')
@role_required(Role.DOCTOR)
@active_staff_required
def worklist(exam_id):
    clinic_id, staff_id = _actor()
    return _ok(ris.get_worklist_intent(exam_id=exam_id, clinic_id=clinic_id,
        actor_staff_id=staff_id))


@radiology_bp.post('/exams/<int:exam_id>/reports')
@role_required(Role.DOCTOR)
@active_staff_required
def create_draft(exam_id):
    clinic_id, staff_id = _actor()
    dto = _body(DraftReportBody)
    return _ok(ris.create_draft(exam_id=exam_id, clinic_id=clinic_id,
        actor_staff_id=staff_id, content=dto.content), 201)


@radiology_bp.patch('/reports/<int:report_id>')
@role_required(Role.DOCTOR)
@active_staff_required
def revise_draft(report_id):
    clinic_id, staff_id = _actor()
    dto = _body(ReviseReportBody)
    return _ok(ris.revise_draft(report_id=report_id, clinic_id=clinic_id,
        actor_staff_id=staff_id, **dto.model_dump()))


@radiology_bp.get('/exams/<int:exam_id>/reports')
@role_required(Role.DOCTOR)
@active_staff_required
def private_history(exam_id):
    clinic_id, staff_id = _actor()
    return _ok(ris.get_draft_history(exam_id=exam_id,
        clinic_id=clinic_id, actor_staff_id=staff_id))
