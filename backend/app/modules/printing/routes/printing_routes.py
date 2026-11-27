"""RAD6 authorized PDF downloads. Disabled by default pending clinical approval.

GET is user-initiated only; no auto-print, offline replay or durable PDF store.
Flask alone resolves identity and clinic. Source adapters never trust request PHI.
"""
from __future__ import annotations

from io import BytesIO
from flask import Blueprint, current_app, g, send_file, request, abort, jsonify
from flask_jwt_extended import get_jwt
from sqlalchemy import select

from app.core.auth.user.models.user_model import User
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.audit.services.audit_service import create_audit_log
from app.core.utils.decorators import get_current_clinic_id, role_required
from app.extensions import db, limiter
from app.modules.staff.models.staff_model import Staff
from app.modules.printing.services.print_policy import PrintDenied, supported_kinds
from app.modules.printing.services.rust_print_bridge import render_pdf
from app.modules.printing.services.branding_payload import render_authorized
from app.modules.printing.services.source_adapters import snapshot

printing_bp = Blueprint('printing',__name__,url_prefix='/printing')

PRINT_ROLES=(Role.ADMIN,Role.ACCOUNTANT,Role.RECEPTIONIST,Role.DOCTOR,Role.LAB_TECHNICIAN)


def _actor():
    clinic_id=get_current_clinic_id(required=True)
    user_id=getattr(g,'current_user_id',None)
    if type(user_id) is not int or user_id<=0:
        raise PrintDenied('Print unavailable')
    user=db.session.get(User,user_id)
    if (user is None or not user.is_active or user.role not in PRINT_ROLES
            or type(user.clinic_id) is not int or user.clinic_id!=clinic_id):
        raise PrintDenied('Print unavailable')
    context=get_jwt().get('clinic_context_id')
    if context is not None and (type(context) is not int or context!=clinic_id):
        raise PrintDenied('Print unavailable')
    staff=db.session.execute(select(Staff).where(
        Staff.user_id==user.id,Staff.clinic_id==clinic_id)).scalar_one_or_none()
    if staff is None or staff.status!=StaffStatus.ACTIVE:
        raise PrintDenied('Print unavailable')
    return user,staff,clinic_id


@printing_bp.after_request
def secure_headers(resp):
    resp.headers['Cache-Control']='private, no-store, max-age=0'
    resp.headers['Pragma']='no-cache'
    resp.headers['X-Content-Type-Options']='nosniff'
    resp.headers['X-Frame-Options']='DENY'
    resp.headers['Referrer-Policy']='no-referrer'
    return resp


@printing_bp.get('/<kind>/<int:record_id>.pdf')
@role_required(*PRINT_ROLES)
def print_document(kind: str,record_id:int):
    if not current_app.config.get('RAD6_PRINTING_ENABLED',False):
        abort(404)
    if (kind not in supported_kinds() or record_id<=0 or request.query_string
            or request.headers.get('Idempotency-Key') is not None):
        abort(404)
    try:
        user,staff,clinic_id=_actor()
        role=user.role.name
        doc=snapshot(kind=kind,record_id=record_id,clinic_id=clinic_id,
                     actor_role=role,actor_staff_id=staff.id)
        data, branding_audit = render_authorized(doc, render_pdf, owning_clinic_id=clinic_id)
        # Print request is auditable and must commit successfully before release.
        # No raw PDF/text/PHI is written to audit payload.
        create_audit_log(action=AuditAction.VIEW,entity_type='PrintedDocument',
            entity_id=record_id,user_id=user.id,clinic_id=clinic_id,
            description='Authorized bounded document download',
            new_value={'kind':kind,'source_version':doc.source_version,
                       'source_digest':doc.source_digest,'unsigned':True,**branding_audit})
        db.session.commit()
    except PrintDenied:
        db.session.rollback()
        abort(404)
    except Exception:
        db.session.rollback()
        raise
    resp=send_file(BytesIO(data),mimetype='application/pdf',as_attachment=True,
                   download_name=f'{kind}-{record_id}.pdf',max_age=0)
    resp.headers['X-Source-SHA256']=doc.source_digest
    resp.headers['X-Clinical-Release']='none'
    return resp


@printing_bp.route('/verify/<token>', methods=['GET','POST'])
@limiter.limit('30 per minute')
def verify_issued_document(token):
    from app.modules.printing.services.verification import lookup
    try:
        if request.query_string or request.headers.get('Idempotency-Key') is not None:
            raise PrintDenied('Verification unavailable')
        candidate = None
        if request.method == 'POST':
            if request.content_length is None or request.content_length>512 or not request.is_json:
                raise PrintDenied('Verification unavailable')
            body=request.get_json(silent=True)
            if not isinstance(body,dict) or set(body)!={'pdf_sha256'} or not isinstance(body['pdf_sha256'],str):
                raise PrintDenied('Verification unavailable')
            candidate=body['pdf_sha256']
        return jsonify({'data':lookup(token,candidate)})
    except PrintDenied:
        abort(404)


@printing_bp.get('/thermal/<int:paper>/payment/<int:payment_id>.escpos')
@role_required(Role.ADMIN,Role.ACCOUNTANT)
def thermal_payment_copy(paper,payment_id):
    from hashlib import sha256
    from app.modules.printing.services.thermal import render_payment
    if not current_app.config.get('RAD6_PRINTING_ENABLED',False) or request.query_string or request.headers.get('Idempotency-Key') is not None:
        abort(404)
    try:
        user,staff,clinic_id=_actor()
        data=render_payment(payment_id=payment_id,clinic_id=clinic_id,paper=paper)
        create_audit_log(action=AuditAction.VIEW,entity_type='Payment',entity_id=payment_id,user_id=user.id,clinic_id=clinic_id,
            description='Authorized staged thermal payment-record copy',new_value={'paper_mm':paper,'stream_sha256':sha256(data).hexdigest()})
        db.session.commit()
    except PrintDenied:
        db.session.rollback();abort(404)
    except Exception:
        db.session.rollback();raise
    return send_file(BytesIO(data),mimetype='application/octet-stream',as_attachment=True,download_name=f'payment-{payment_id}.escpos',max_age=0)
