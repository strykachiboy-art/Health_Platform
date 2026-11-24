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

@radiology_bp.get('/exams/<int:exam_id>/clinical-worklist')
@role_required(Role.DOCTOR)
@active_staff_required
def clinical_worklist_status(exam_id):
    from app.modules.radiology.services import radc_worklist_service as clinical
    if request.args:
        raise ValidationError('Unsupported query parameters')
    clinic_id, staff_id = _actor()
    return _ok(clinical.get_publication(exam_id=exam_id, clinic_id=clinic_id, actor_staff_id=staff_id))


@radiology_bp.post('/exams/<int:exam_id>/clinical-worklist')
@role_required(Role.DOCTOR)
@active_staff_required
def clinical_worklist_publish(exam_id):
    from app.modules.radiology.services import radc_worklist_service as clinical
    from app.modules.radiology.schemas.ris_schema import _Strict, Positive
    class _Publish(_Strict):
        expected_version: Positive
    if request.args:
        raise ValidationError('Unsupported query parameters')
    clinic_id, staff_id = _actor()
    dto = _body(_Publish)
    return _ok(clinical.publish(exam_id=exam_id, clinic_id=clinic_id,
        actor_staff_id=staff_id, expected_version=dto.expected_version))

@radiology_bp.post('/exams/<int:exam_id>/staged-study')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_study_validate(exam_id):
    from app.modules.radiology.services import radc_acquisition_service as acquisition
    from app.modules.radiology.schemas.ris_schema import _Strict, Positive
    from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed
    class _Acquire(_Strict):
        publication_id: Positive
    if request.args:
        raise ValidationError('Unsupported query parameters')
    clinic_id, staff_id = _actor()
    dto = _body(_Acquire)
    try:
        return _ok(acquisition.validate(exam_id=exam_id, clinic_id=clinic_id,
            actor_staff_id=staff_id, publication_id=dto.publication_id), 201)
    except ArchiveUnconfirmed:
        return jsonify({'success': False, 'error': 'Staged archive outcome unconfirmed'}), 503


@radiology_bp.get('/exams/<int:exam_id>/staged-study/<int:publication_id>/instance')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_study_instance(exam_id, publication_id):
    from flask import Response
    from app.modules.radiology.services import radc_acquisition_service as acquisition
    from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed
    if request.args:
        raise ValidationError('Unsupported query parameters')
    clinic_id, staff_id = _actor()
    try:
        data = acquisition.retrieve(exam_id=exam_id, clinic_id=clinic_id,
            actor_staff_id=staff_id, publication_id=publication_id)
    except ArchiveUnconfirmed:
        return jsonify({'success': False, 'error': 'Staged archive unavailable'}), 503
    return Response(data, mimetype='application/dicom', headers={
        'X-Content-Type-Options':'nosniff','Content-Disposition':'attachment; filename="staged-nondiagnostic.dcm"',
        'X-Radiology-Scope':'synthetic-nondiagnostic-only'})

@radiology_bp.post('/exams/<int:exam_id>/staged-ct')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_ct_validate(exam_id):
    from app.modules.radiology.services import radc_ct_service as ct
    from app.modules.radiology.schemas.ris_schema import _Strict, Positive
    from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed
    class _AcquireCT(_Strict):
        publication_id: Positive
    if request.args:
        raise ValidationError('Unsupported query parameters')
    clinic_id, staff_id = _actor()
    dto = _body(_AcquireCT)
    try:
        return _ok(ct.validate(exam_id=exam_id, clinic_id=clinic_id,
            actor_staff_id=staff_id, publication_id=dto.publication_id), 201)
    except ArchiveUnconfirmed:
        return jsonify({'success': False, 'error': 'Staged CT archive unavailable'}), 503


@radiology_bp.get('/exams/<int:exam_id>/staged-ct/<int:publication_id>/viewer/<path:target>')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_ct_viewer(exam_id, publication_id, target):
    from flask import Response
    from app.modules.radiology.services import radc_ct_service as ct
    from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed
    clinic_id, staff_id = _actor()
    try:
        body, content_type = ct.view(exam_id=exam_id, clinic_id=clinic_id,
            actor_staff_id=staff_id, publication_id=publication_id, target=target,
            query=list(request.args.items(multi=True)))
    except ArchiveUnconfirmed:
        return jsonify({'success': False, 'error': 'Staged CT archive unavailable'}), 503
    return Response(body, content_type=content_type, headers={
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
        'X-Radiology-Scope': 'synthetic-nondiagnostic-only'})

# Test-profile-only reporting routes. The old RAD1 envelope continues to deny
# clinical signing and chart release; staged reporting uses a distinct receipt.
def _staged_report_args(exam_id, publication_id):
    if request.args or request.headers.get('Idempotency-Key') is not None:
        raise ValidationError('Unsupported staged report parameters')
    clinic_id,staff_id=_actor()
    return dict(exam_id=exam_id,clinic_id=clinic_id,actor_staff_id=staff_id,publication_id=publication_id)


def _staged_report_response(fn, **kwargs):
    from app.modules.radiology.services.radc_orthanc_transport import ArchiveUnconfirmed
    try:
        return jsonify({'success':True,'data':fn(**kwargs),'synthetic_only':True,'diagnostic_report_signed':False,'released_to_chart':False})
    except ArchiveUnconfirmed:
        return jsonify({'success':False,'error':'Staged archive unavailable'}),503


@radiology_bp.get('/exams/<int:exam_id>/staged-report/<int:publication_id>/preview')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_report_preview(exam_id,publication_id):
    from app.modules.radiology.services import radc_report_service as report
    return _staged_report_response(report.preview,**_staged_report_args(exam_id,publication_id))


@radiology_bp.post('/exams/<int:exam_id>/staged-report/<int:publication_id>/freeze')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_report_freeze(exam_id,publication_id):
    from app.modules.radiology.services import radc_report_service as report
    from app.modules.radiology.schemas.ris_schema import _Strict,Positive
    from pydantic import Field
    class Body(_Strict):
        expected_version: Positive
        preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    return _staged_report_response(report.finalize,**_staged_report_args(exam_id,publication_id),**_body(Body).model_dump())


@radiology_bp.get('/exams/<int:exam_id>/staged-report/<int:publication_id>')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_report_status(exam_id,publication_id):
    from app.modules.radiology.services import radc_report_service as report
    return _staged_report_response(report.status,**_staged_report_args(exam_id,publication_id))


@radiology_bp.post('/exams/<int:exam_id>/staged-report/<int:publication_id>/amend')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_report_amend(exam_id,publication_id):
    from app.modules.radiology.services import radc_report_service as report
    from app.modules.radiology.schemas.ris_schema import _Strict,Positive
    from pydantic import Field
    class Body(_Strict):
        expected_version: Positive
        report_text: str = Field(min_length=1,max_length=12000)
        reason: str = Field(min_length=5,max_length=1000)
    return _staged_report_response(report.amend,**_staged_report_args(exam_id,publication_id),**_body(Body).model_dump())


@radiology_bp.get('/exams/<int:exam_id>/staged-report/<int:publication_id>/copy-preview')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_report_copy_preview(exam_id,publication_id):
    from app.modules.radiology.services import radc_report_service as report
    return _staged_report_response(report.release_preview,**_staged_report_args(exam_id,publication_id))


@radiology_bp.post('/exams/<int:exam_id>/staged-report/<int:publication_id>/copy')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_report_copy(exam_id,publication_id):
    from app.modules.radiology.services import radc_report_service as report
    from app.modules.radiology.schemas.ris_schema import _Strict
    from pydantic import Field
    class Body(_Strict):
        preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    return _staged_report_response(report.publish,**_staged_report_args(exam_id,publication_id),**_body(Body).model_dump())


@radiology_bp.post('/exams/<int:exam_id>/staged-report/<int:publication_id>/withdraw')
@role_required(Role.DOCTOR)
@active_staff_required
def staged_report_withdraw(exam_id,publication_id):
    from app.modules.radiology.services import radc_report_service as report
    from app.modules.radiology.schemas.ris_schema import _Strict,Positive
    class Body(_Strict):
        expected_version: Positive
    return _staged_report_response(report.withdraw,**_staged_report_args(exam_id,publication_id),**_body(Body).model_dump())


def _staged_patient_args():
    if request.args or request.headers.get('Idempotency-Key') is not None:
        raise ValidationError('Unsupported staged report parameters')
    clinic_id=get_current_clinic_id(required=True)
    context=get_jwt().get('clinic_context_id')
    if context is not None and (type(context) is not int or context!=clinic_id):
        raise NotFoundError('Staged copy unavailable')
    return dict(user_id=g.current_user_id,clinic_id=clinic_id)


@radiology_bp.get('/me/staged-reports')
@role_required(Role.PATIENT)
def staged_patient_reports():
    from app.modules.radiology.services import radc_report_service as report
    return _staged_report_response(report.patient_list,**_staged_patient_args())


@radiology_bp.get('/me/staged-reports/<int:exam_id>')
@role_required(Role.PATIENT)
def staged_patient_report(exam_id):
    from app.modules.radiology.services import radc_report_service as report
    return _staged_report_response(report.patient_detail,exam_id=exam_id,**_staged_patient_args())


@radiology_bp.get('/me/staged-reports/<int:exam_id>/pdf')
@role_required(Role.PATIENT)
def staged_patient_report_pdf(exam_id):
    from flask import Response,abort
    from app.modules.radiology.services import radc_report_service as report
    from app.modules.printing.services.print_policy import PrintDenied
    try:
        data,doc=report.patient_pdf(exam_id=exam_id,**_staged_patient_args())
    except PrintDenied:
        abort(404)
    return Response(data,mimetype='application/pdf',headers={'X-Content-Type-Options':'nosniff','X-Source-SHA256':doc.source_digest,'X-Radiology-Scope':'synthetic-nondiagnostic-only','Content-Disposition':'attachment; filename="staged-radiology-copy.pdf"'})
