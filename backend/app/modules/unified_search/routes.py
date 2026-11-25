"""Read-only POST search: keep patient search terms out of request URLs."""
from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import get_jwt

from app.core.enums.role_enums import Role
from app.core.exceptions import DomainError
from app.core.utils.decorators import active_staff_required, role_required
from app.extensions import db, limiter

from .contracts import PATIENT_ROLES, SearchFilters
from .service import search

unified_search_bp = Blueprint('unified_search', __name__, url_prefix='/search')


@unified_search_bp.after_request
def no_store(response):
    response.headers['Cache-Control'] = 'private, no-store, max-age=0'
    return response


@unified_search_bp.post('')
@limiter.limit('120 per minute')
@role_required(*(role for role in Role if role.value in PATIENT_ROLES))
@active_staff_required
def unified_search():
    if not current_app.testing or current_app.config.get('UNIFIED_CLINICAL_SEARCH_ENABLED') is not True:
        return jsonify({'success': False, 'error': 'Unified search unavailable'}), 404
    if request.headers.get('X-Emergency-Access-ID') is not None:
        return jsonify({'success': False, 'error': 'Emergency search is not supported'}), 403
    # Search is not a mutation, has no idempotency/replay contract, and accepts
    # no client-selected clinic, organization, patient scope or URL query.
    if request.args or not request.is_json or request.content_length is None or request.content_length > 4096:
        return jsonify({'success': False, 'error': 'Invalid search request'}), 400
    try:
        filters = SearchFilters.parse(request.get_json(silent=True))
        return jsonify({'success': True, 'data': search(g.current_user_id, get_jwt(), filters)}), 200
    except ValueError:
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Invalid search request or continuation'}), 400
    except DomainError:
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Search access denied'}), 403
