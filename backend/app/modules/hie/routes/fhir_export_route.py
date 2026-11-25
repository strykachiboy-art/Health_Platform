"""Staged FHIR R4 export: local response only, no partner delivery.

A synthetic, actor/patient/tenant-bound approval is required in testing.
This is not a production consent system or a grant from the ADMIN role.
"""
from datetime import datetime, timezone
from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import get_jwt
from sqlalchemy import select

from app.core.auth.user.models.user_model import User
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import DomainError, NotFoundError, ConflictError, ValidationError
from app.core.utils.decorators import role_required, transactional
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.services.session_authority import context_from_claims, TenantContextDenied
from app.modules.clinic.services.tenant_boundary import require_tenant_clinic_access, TenantAccessDenied
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.staff.models.staff_model import Staff
from app.modules.patient.models.patient_model import Patient, PatientAllergy, PatientVitals
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.modules.prescription.models.prescription_model import Prescription, PrescriptionItem
from app.modules.patient.problems.model import PatientProblem
from app.modules.patient.problems.service import serialize
from app.modules.patient.problems.interoperability import local_condition
from app.modules.lab.services import lab_release_service
from app.modules.lab.models.lab_model import LabOrder
from app.modules.hie.providers.fhir_export import (
    patient_to_fhir, admission_to_fhir_encounter, allergy_to_fhir,
    prescription_to_fhir_medication_request, vitals_to_fhir_observation,
    released_lab_to_fhir, build_fhir_collection_bundle,
)

fhir_export_bp = Blueprint('fhir_export', __name__, url_prefix='/hie/fhir/export')
KINDS = ('encounters', 'observations', 'allergies', 'medications', 'conditions', 'diagnostics')


class ExportDenied(DomainError):
    status_code = 403


@fhir_export_bp.after_request
def _no_store(response):
    response.headers['Cache-Control'] = 'private, no-store, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


def _bounded(statement, cap=100):
    rows = list(db.session.execute(statement.limit(cap + 1)).scalars())
    if len(rows) > cap:
        raise ConflictError('FHIR export exceeds staged record limit; no partial export returned')
    return rows


def _scope(user_id, claims, patient_id):
    user = db.session.get(User, user_id)
    if user is None or not user.is_active or user.role is not Role.DOCTOR:
        raise ExportDenied('FHIR export authority required')
    try:
        tenant, clinic_id = context_from_claims(user_id, claims)
        require_tenant_clinic_access(user_id=user_id, organization_id=tenant, clinic_id=clinic_id)
    except (TenantContextDenied, TenantAccessDenied) as exc:
        raise ExportDenied('Bound clinic membership required') from exc
    clinic = db.session.get(Clinic, clinic_id)
    if clinic is None or str(clinic.organization_id) != tenant:
        raise ExportDenied('Bound organization required')
    staff = db.session.execute(select(Staff).where(Staff.user_id == user_id,
        Staff.clinic_id == clinic_id, Staff.status == StaffStatus.ACTIVE)).scalar_one_or_none()
    if staff is None:
        raise ExportDenied('Active clinical staff required')
    patient = db.session.execute(select(Patient).where(Patient.id == patient_id,
        Patient.clinic_id == clinic_id)).scalar_one_or_none()
    if patient is None:
        raise NotFoundError('Patient not found')
    # No HTTP parameter can create an approval. This is a test-harness-only
    # substitute for a future durable consent and export authorization contract.
    approvals = current_app.config.get('FHIR_EXPORT_SYNTHETIC_APPROVALS', ())
    if (tenant, clinic_id, user_id, patient_id) not in approvals:
        raise ExportDenied('Explicit synthetic export approval required')
    return tenant, clinic, patient


def _same_clinic_staff(staff_id, clinic_id):
    if staff_id is not None and db.session.execute(select(Staff.id).where(
            Staff.id == staff_id, Staff.clinic_id == clinic_id)).scalar_one_or_none() is None:
        raise ConflictError('FHIR source ownership mismatch')


def _same_consultation(consultation_id, clinic_id, patient_id):
    if consultation_id is not None and db.session.execute(select(Consultation.id).where(
            Consultation.id == consultation_id, Consultation.clinic_id == clinic_id,
            Consultation.patient_id == patient_id)).scalar_one_or_none() is None:
        raise ConflictError('FHIR source ownership mismatch')


def _collect(kind, clinic, patient):
    pid, cid = patient.id, clinic.id
    if kind == 'encounters':
        # Admission has no clinic column; ownership follows patient, bed/ward
        # and the admitting staff. Never trust patient_id alone.
        rows = _bounded(select(Admission).where(Admission.patient_id == pid).order_by(Admission.id))
        for row in rows:
            ward_id = db.session.execute(select(Ward.id).join(Bed, Bed.ward_id == Ward.id).where(
                Bed.id == row.bed_id, Ward.clinic_id == cid)).scalar_one_or_none()
            if ward_id is None:
                raise ConflictError('FHIR admission ownership mismatch')
            _same_clinic_staff(row.admitted_by_id, cid)
        return [admission_to_fhir_encounter(x) for x in rows]
    if kind == 'observations':
        rows = _bounded(select(PatientVitals).where(PatientVitals.patient_id == pid).order_by(PatientVitals.id), 50)
        for row in rows:
            _same_clinic_staff(row.recorded_by_id, cid)
            _same_consultation(row.consultation_id, cid, pid)
        return [resource for row in rows for resource in vitals_to_fhir_observation(row)]
    if kind == 'allergies':
        rows = _bounded(select(PatientAllergy).where(PatientAllergy.patient_id == pid,
            PatientAllergy.clinic_id == cid).order_by(PatientAllergy.id))
        for row in rows:
            _same_clinic_staff(row.recorded_by_id, cid)
        return [allergy_to_fhir(x) for x in rows]
    if kind == 'medications':
        rows = _bounded(select(PrescriptionItem).join(Prescription,
            PrescriptionItem.prescription_id == Prescription.id).where(Prescription.patient_id == pid,
            Prescription.clinic_id == cid).order_by(PrescriptionItem.id), 200)
        for row in rows:
            if row.drug is None or row.drug.clinic_id not in (None, cid):
                raise ConflictError('FHIR medication ownership mismatch')
            _same_clinic_staff(row.prescription.prescribed_by_id, cid)
            _same_consultation(row.prescription.consultation_id, cid, pid)
        return [prescription_to_fhir_medication_request(x) for x in rows]
    if kind == 'conditions':
        if current_app.config.get('PATIENT_PROBLEM_LIST_ENABLED') is not True:
            raise ConflictError('Structured problem export unavailable')
        rows = _bounded(select(PatientProblem).where(PatientProblem.patient_id == pid,
            PatientProblem.clinic_id == cid, PatientProblem.organization_id == clinic.organization_id).order_by(PatientProblem.id))
        return [local_condition(serialize(x)) for x in rows]
    if kind == 'diagnostics':
        if current_app.config.get('PATIENT_LAB_RELEASE_ENABLED') is not True:
            raise ConflictError('Released laboratory export unavailable')
        # Only the existing patient release policy selects visible reports,
        # including replacement publications. Never export raw lab items.
        if patient.user_id is None:
            return []
        owner_patient = lab_release_service._own_patient(patient.user_id, cid)
        if owner_patient.id != pid:
            raise ConflictError('FHIR release recipient mismatch')
        rows = _bounded(lab_release_service._visible(owner_patient, patient.user_id).order_by(LabOrder.id), 50)
        return [resource for row in rows for resource in released_lab_to_fhir(lab_release_service._released(row))]
    raise ValidationError('Unknown FHIR export section')


@transactional
def _export(user_id, claims, patient_id, section):
    tenant, clinic, patient = _scope(user_id, claims, patient_id)
    # Stable local namespace, intentionally non-routable; this is a synthetic
    # exchange boundary, not a public FHIR server endpoint.
    issuer = f'https://synthetic.example.invalid/fhir/{tenant}/{clinic.id}'
    resources = [patient_to_fhir(patient, issuer=issuer)]
    if section != 'patient':
        for kind in KINDS if section == 'all' else (section,):
            resources.extend(_collect(kind, clinic, patient))
    bundle = build_fhir_collection_bundle(resources, issuer=issuer,
        bundle_id=f'patient-{patient.id}-{section}', timestamp=datetime.now(timezone.utc).isoformat())
    # Store only the operation metadata. No exported payload or token in audit.
    create_audit_log(action=AuditAction.VIEW, entity_type='FHIRExport', entity_id=patient.id,
        clinic_id=clinic.id, patient_id=patient.id, user_id=user_id,
        description='Approved synthetic local FHIR export',
        new_value={'section': section, 'resource_count': len(resources)})
    return bundle


def _respond(patient_id, section):
    if not current_app.testing or current_app.config.get('FHIR_EXPORT_ENABLED') is not True:
        return jsonify({'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': 'forbidden', 'diagnostics': 'FHIR export unavailable'}]}), 404
    if request.args or section not in ('all', 'patient', *KINDS):
        return jsonify({'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': 'invalid'}]}), 400
    try:
        result = _export(g.current_user_id, get_jwt(), patient_id, section)
    except DomainError as exc:
        code = {403: 'forbidden', 404: 'not-found', 409: 'conflict'}.get(exc.status_code, 'invalid')
        response = jsonify({'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': code}]})
        response.headers['Content-Type'] = 'application/fhir+json'
        return response, exc.status_code
    except (ValueError, TypeError, AttributeError, KeyError):
        response = jsonify({'resourceType': 'OperationOutcome', 'issue': [{'severity': 'error', 'code': 'processing', 'diagnostics': 'Source record requires review'}]})
        response.headers['Content-Type'] = 'application/fhir+json'
        return response, 409
    response = jsonify(result)
    response.headers['Content-Type'] = 'application/fhir+json'
    return response


@fhir_export_bp.get('/patient/<int:patient_id>')
@role_required(Role.DOCTOR)
def export_patient_bundle(patient_id):
    return _respond(patient_id, 'all')


@fhir_export_bp.get('/patient/<int:patient_id>/<section>')
@role_required(Role.DOCTOR)
def export_patient_section(patient_id, section):
    return _respond(patient_id, section)
