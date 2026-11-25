"""Read-only, resource-bound review of confirmed longitudinal conditions.

Do not modify medication reconciliation documents, care plans, or discharge
summaries. Existing clinical decisions and immutable records remain authoritative.
"""
from datetime import datetime, timezone
from app.core.enums.role_enums import Role
from app.modules.clinic.services.session_authority import context_from_claims
from app.modules.patient.problems.service import _scope, ProblemAccessDenied
from app.modules.patient.problems.safety import confirmed_problem_context


def _bound_clinic(user_id, claims):
    _, clinic_id = context_from_claims(user_id, claims)
    if clinic_id is None:
        raise ProblemAccessDenied('Bound clinic membership required')
    return clinic_id


def _projection(user_id, claims, patient_id, *, medication_review=False):
    clinic, _, patient = _scope(user_id, claims, patient_id,
                               medication_review=medication_review)
    at = datetime.now(timezone.utc)
    conditions, _ = confirmed_problem_context(clinic_id=clinic.id,
        patient_id=patient.id, evaluation_at=at)
    return {'patient_id': patient.id, 'evaluated_at': at.isoformat(),
            'confirmed_current_problems': conditions}


def admission_problem_context(user_id, claims, admission_id):
    from app.modules.ward.services.ward_service import get_admission
    from app.core.auth.user.models.user_model import User
    from app.extensions import db
    user = db.session.get(User, user_id)
    if user is None or user.role not in (Role.DOCTOR, Role.NURSE):
        raise ProblemAccessDenied('Admission problem review denied')
    admission = get_admission(admission_id, clinic_id=_bound_clinic(user_id, claims))
    return {'admission_id': admission.id, **_projection(user_id, claims, admission.patient_id)}


def medication_problem_context(user_id, claims, reconciliation_id):
    from app.modules.medication_reconciliation.services.medication_reconciliation_service import get_medication_reconciliation
    from app.core.auth.user.models.user_model import User
    from app.extensions import db
    user = db.session.get(User, user_id)
    if user is None or user.role not in (Role.DOCTOR, Role.NURSE, Role.PHARMACIST):
        raise ProblemAccessDenied('Medication problem review denied')
    record = get_medication_reconciliation(reconciliation_id,
        clinic_id=_bound_clinic(user_id, claims))
    return {'reconciliation_id': record.id,
            **_projection(user_id, claims, record.patient_id, medication_review=True)}
