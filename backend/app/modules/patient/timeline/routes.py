"""Staged GET-only clinical timeline. No emergency-context expansion."""
from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import get_jwt
from app.core.enums.role_enums import Role
from app.core.exceptions import DomainError
from app.core.utils.decorators import role_required
from app.extensions import db
from .contracts import Filters
from .service import list_events

clinical_timeline_bp = Blueprint('patient_clinical_timeline', __name__, url_prefix='/patients')


def _reply(body, status):
    response = jsonify(body)
    response.headers['Cache-Control'] = 'private, no-store, max-age=0'
    return response, status


@clinical_timeline_bp.get('/<int:patient_id>/clinical-timeline')
@role_required(Role.DOCTOR, Role.NURSE)
def patient_timeline(patient_id):
    if not current_app.testing or current_app.config.get('PATIENT_CLINICAL_TIMELINE_ENABLED') is not True:
        return _reply({'success': False, 'error': 'Clinical timeline unavailable'}, 404)
    if request.headers.get('X-Emergency-Access-ID') is not None:
        return _reply({'success': False, 'error': 'Clinical timeline does not support emergency access'}, 403)
    try:
        data = list_events(g.current_user_id, get_jwt(), patient_id, Filters.parse(request.args))
        return _reply({'success': True, 'data': data}, 200)
    except ValueError:
        db.session.rollback()
        return _reply({'success': False, 'error': 'Invalid timeline filters or continuation'}, 400)
    except DomainError as exc:
        db.session.rollback()
        return _reply({'success': False, 'error': str(exc)}, exc.status_code)
