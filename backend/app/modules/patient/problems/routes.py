"""Proposed problem-list API. Disabled by default pending integration proof."""
from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import get_jwt
from pydantic import ValidationError as PydanticValidationError
from app.core.enums.role_enums import Role
from app.core.exceptions import DomainError, ValidationError
from app.core.utils.decorators import role_required
from app.extensions import db
from .contracts import ProblemCreate, ProblemChange
from .service import create_problem, change_problem, list_problems, get_problem, get_history, get_fhir_condition

problem_bp = Blueprint('patient_problems', __name__, url_prefix='/patients')
READ = (Role.DOCTOR, Role.NURSE)


def _respond(fn, *args, status=200, schema=None):
    if not current_app.testing or current_app.config.get('PATIENT_PROBLEM_LIST_ENABLED', False) is not True:
        return jsonify({'success': False, 'error': 'Problem list unavailable'}), 404
    try:
        if schema:
            args += (schema.model_validate(request.get_json(silent=True)),)
        result = fn(g.current_user_id, get_jwt(), *args)
        response = jsonify({'success': True, 'data': result})
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        return response, status
    except PydanticValidationError:
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Invalid problem-list request'}), 400
    except DomainError as exc:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(exc)}), exc.status_code


@problem_bp.get('/<int:patient_id>/problems')
@role_required(*READ)
def list_route(patient_id):
    status = request.args.get('status', 'all')
    try:
        if set(request.args) - {'status', 'after', 'limit'} or any(len(request.args.getlist(k)) != 1 for k in request.args):
            raise ValueError
        after, limit = int(request.args.get('after', '0')), int(request.args.get('limit', '50'))
        if status not in {'all', 'current', 'historical', 'resolved'} or after < 0 or not 1 <= limit <= 100:
            raise ValueError
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid pagination or filter'}), 400
    return _respond(list_problems, patient_id, status, after, limit)


@problem_bp.post('/<int:patient_id>/problems')
@role_required(Role.DOCTOR)
def create_route(patient_id):
    return _respond(create_problem, patient_id, schema=ProblemCreate, status=201)


@problem_bp.get('/<int:patient_id>/problems/<int:problem_id>')
@role_required(*READ)
def detail_route(patient_id, problem_id):
    return _respond(get_problem, patient_id, problem_id)


@problem_bp.patch('/<int:patient_id>/problems/<int:problem_id>')
@role_required(Role.DOCTOR)
def change_route(patient_id, problem_id):
    return _respond(change_problem, patient_id, problem_id, schema=ProblemChange)


@problem_bp.get('/<int:patient_id>/problems/<int:problem_id>/history')
@role_required(*READ)
def history_route(patient_id, problem_id):
    return _respond(get_history, patient_id, problem_id)


@problem_bp.get('/<int:patient_id>/problems/<int:problem_id>/fhir-condition')
@role_required(*READ)
def fhir_condition_route(patient_id, problem_id):
    return _respond(get_fhir_condition, patient_id, problem_id)
