"""CE1-S11 authenticated, tenant-scoped Surgery read models only.

The module never creates/updates clinical records and never grants permission
for operation, Sign-Out, PACU arrival, handoff, eMAR, billing or discharge.
Documentation status is not an assertion that a procedure was performed.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.modules.surgery.models.surgical_case_model import SurgicalCase
from app.modules.surgery.models.intraoperative_evidence_model import SurgicalEvidenceJournal
from app.modules.surgery.models.surgical_material_trace_model import SurgicalMaterialTrace
from app.modules.surgery.models.pacu_preparation_model import SurgicalPacuPreparation
from app.modules.surgery.models.operative_report_model import (
    SurgicalOperativeReport, SurgicalOperativeSignature,
)
from app.modules.surgery.models.surgical_clinical_reference_model import SurgicalClinicalReference
from app.modules.surgery.services import intraoperative_evidence_service as evidence
from app.modules.surgery.services import surgical_material_trace_service as materials
from app.modules.surgery.services import pacu_preparation_service as pacu
from app.modules.surgery.services import operative_report_service as operative
from app.modules.surgery.services import surgical_clinical_reference_service as references

_VIEW_ROLES = frozenset({Role.DOCTOR, Role.NURSE, Role.ADMIN, Role.SUPER_ADMIN})
_CLINICAL_ROLES = frozenset({Role.DOCTOR, Role.NURSE})


def _positive(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{field} must be a positive integer")
    return value


def _paging_number(value, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{field} is invalid")
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        n = int(value)
    else:
        raise ValidationError(f"{field} must be a nonnegative decimal integer")
    if n < minimum or n > maximum:
        raise ValidationError(f"{field} must be between {minimum} and {maximum}")
    return n


def _viewer(*, actor_user_id: int, clinic_id: int) -> tuple[Role, int | None]:
    actor_user_id = _positive(actor_user_id, "actor_user_id")
    clinic_id = _positive(clinic_id, "clinic_id")
    user = db.session.execute(select(User).where(User.id == actor_user_id)).scalar_one_or_none()
    if user is None or not user.is_active or user.role not in _VIEW_ROLES:
        raise NotFoundError("Surgery view is unavailable")
    # The global super-admin's selected clinic has already been established by
    # the JWT clinic-context resolver at the authenticated route boundary.
    if user.role != Role.SUPER_ADMIN and user.clinic_id != clinic_id:
        raise NotFoundError("Surgery view is unavailable in this clinic")
    if user.role not in _CLINICAL_ROLES:
        return user.role, None
    staff = db.session.execute(select(Staff).where(
        Staff.user_id == actor_user_id,
        Staff.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if staff is None or staff.status != StaffStatus.ACTIVE:
        raise NotFoundError("Active clinic clinical staff required")
    return user.role, staff.id


def _case_scope(*, clinic_id: int, role: Role, staff_id: int | None):
    # Verify all three ownership links, not only the case's clinic_id. An
    # inconsistent patient, admission or ward is never returned to a viewer.
    q = (
        select(SurgicalCase.id)
        .join(Patient, Patient.id == SurgicalCase.patient_id)
        .join(Admission, Admission.id == SurgicalCase.admission_id)
        .join(Bed, Bed.id == Admission.bed_id)
        .join(Ward, Ward.id == Bed.ward_id)
        .where(
            SurgicalCase.clinic_id == clinic_id,
            Patient.clinic_id == clinic_id,
            Ward.clinic_id == clinic_id,
            Admission.patient_id == SurgicalCase.patient_id,
        )
    )
    if role == Role.DOCTOR:
        q = q.where(or_(
            SurgicalCase.planned_by_staff_id == staff_id,
            select(SurgicalEvidenceJournal.id).where(
                SurgicalEvidenceJournal.surgical_case_id == SurgicalCase.id,
                SurgicalEvidenceJournal.clinic_id == clinic_id,
                or_(SurgicalEvidenceJournal.surgeon_staff_id == staff_id,
                    SurgicalEvidenceJournal.anesthetist_staff_id == staff_id),
            ).exists(),
        ))
    elif role == Role.NURSE:
        q = q.where(or_(
            select(SurgicalEvidenceJournal.id).where(
                SurgicalEvidenceJournal.surgical_case_id == SurgicalCase.id,
                SurgicalEvidenceJournal.clinic_id == clinic_id,
                SurgicalEvidenceJournal.counting_nurse_staff_id == staff_id,
            ).exists(),
            select(SurgicalPacuPreparation.id).where(
                SurgicalPacuPreparation.surgical_case_id == SurgicalCase.id,
                SurgicalPacuPreparation.clinic_id == clinic_id,
                or_(SurgicalPacuPreparation.outgoing_nurse_staff_id == staff_id,
                    SurgicalPacuPreparation.receiving_nurse_staff_id == staff_id),
            ).exists(),
        ))
    return q


def _count_for(q, entity, *, clinic_id: int, state_field=None, state=None) -> int:
    statement = select(func.count(entity.id)).where(
        entity.clinic_id == clinic_id,
        entity.surgical_case_id.in_(q),
    )
    if state_field is not None:
        statement = statement.where(state_field == state)
    return int(db.session.execute(statement).scalar_one())


def _case_card(case: SurgicalCase, *, clinic_id: int) -> dict:
    journal = db.session.execute(select(SurgicalEvidenceJournal).where(
        SurgicalEvidenceJournal.clinic_id == clinic_id,
        SurgicalEvidenceJournal.surgical_case_id == case.id,
    )).scalar_one_or_none()
    pacu_row = db.session.execute(select(SurgicalPacuPreparation).where(
        SurgicalPacuPreparation.clinic_id == clinic_id,
        SurgicalPacuPreparation.surgical_case_id == case.id,
    )).scalar_one_or_none()
    report = db.session.execute(select(SurgicalOperativeReport).where(
        SurgicalOperativeReport.clinic_id == clinic_id,
        SurgicalOperativeReport.surgical_case_id == case.id,
    )).scalar_one_or_none()
    # Verify evidence using the existing source modules before any document
    # status or source counts appear in a response. No writes or seal actions.
    if journal is not None:
        evidence.get_surgical_evidence_journal(journal_id=journal.id, clinic_id=clinic_id)
    if pacu_row is not None:
        pacu.get_pacu_preparation(preparation_id=pacu_row.id, clinic_id=clinic_id)
    if report is not None:
        operative.get_operative_report(report_id=report.id, clinic_id=clinic_id)
        signatures = operative.list_operative_signatures(report_id=report.id, clinic_id=clinic_id)
    else:
        signatures = []
    material_traces = materials.list_surgical_material_traces(journal_id=journal.id, clinic_id=clinic_id) if journal is not None else []
    clinical_refs = references.list_surgical_clinical_references(report_id=report.id, clinic_id=clinic_id) if report is not None else []
    return {
        "case_id": case.id,
        "case_status": case.status,
        "journal_status": journal.status if journal else "not_started",
        "pacu_preparation_status": pacu_row.status if pacu_row else "not_started",
        "operative_report_status": report.status if report else "not_started",
        "signature_roles": sorted({x.signer_kind for x in signatures}),
        "material_trace_count": len(material_traces),
        "clinical_reference_count": len(clinical_refs),
        "documentary_only": True,
        "operative_completion_authorized": False,
        "pacu_arrival_or_transfer_authorized": False,
        "sign_out_authorized": False,
        "medication_administration_authorized": False,
        "billing_clearance_authorized": False,
    }


def get_surgery_case_read_model(*, actor_user_id: int, clinic_id: int, case_id: int) -> dict:
    clinic_id = _positive(clinic_id, "clinic_id")
    case_id = _positive(case_id, "case_id")
    role, staff_id = _viewer(actor_user_id=actor_user_id, clinic_id=clinic_id)
    allowed = _case_scope(clinic_id=clinic_id, role=role, staff_id=staff_id)
    case = db.session.execute(select(SurgicalCase).where(
        SurgicalCase.id == case_id, SurgicalCase.id.in_(allowed)
    )).scalar_one_or_none()
    if case is None:
        raise NotFoundError("Surgical case not found")
    return {
        **_case_card(case, clinic_id=clinic_id),
        "clinic_id": clinic_id,
        "patient_id": case.patient_id,
        "admission_id": case.admission_id,
        "procedure_order_id": case.procedure_order_id,
        "case_version": case.version,
    }


def get_surgery_dashboard(*, actor_user_id: int, clinic_id: int,
                          limit: int | str = 20, offset: int | str = 0) -> dict:
    clinic_id = _positive(clinic_id, "clinic_id")
    limit = _paging_number(limit, "limit", minimum=1, maximum=50)
    offset = _paging_number(offset, "offset", minimum=0, maximum=5000)
    role, staff_id = _viewer(actor_user_id=actor_user_id, clinic_id=clinic_id)
    visible_ids = _case_scope(clinic_id=clinic_id, role=role, staff_id=staff_id)
    total = int(db.session.execute(select(func.count()).select_from(visible_ids.subquery())).scalar_one())
    cases = list(db.session.execute(select(SurgicalCase).where(
        SurgicalCase.id.in_(visible_ids),
    ).order_by(SurgicalCase.id.desc()).offset(offset).limit(limit)).scalars().all())
    stats = {
        "planned": int(db.session.execute(select(func.count(SurgicalCase.id)).where(
            SurgicalCase.id.in_(visible_ids), SurgicalCase.status == "planned",
        )).scalar_one()),
        "on_hold": int(db.session.execute(select(func.count(SurgicalCase.id)).where(
            SurgicalCase.id.in_(visible_ids), SurgicalCase.status == "on_hold",
        )).scalar_one()),
        "cancelled": int(db.session.execute(select(func.count(SurgicalCase.id)).where(
            SurgicalCase.id.in_(visible_ids), SurgicalCase.status == "cancelled",
        )).scalar_one()),
        "sealed_documentation": _count_for(visible_ids, SurgicalEvidenceJournal,
            clinic_id=clinic_id, state_field=SurgicalEvidenceJournal.status, state="sealed"),
        "reviewed_pacu_preparations": _count_for(visible_ids, SurgicalPacuPreparation,
            clinic_id=clinic_id, state_field=SurgicalPacuPreparation.status, state="reviewed"),
        "dual_signed_reports": _count_for(visible_ids, SurgicalOperativeReport,
            clinic_id=clinic_id, state_field=SurgicalOperativeReport.status, state="dual_signed"),
    }
    return {
        "scope": "assigned_cases" if role in _CLINICAL_ROLES else "clinic",
        "clinic_id": clinic_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "metrics": stats,
        "cases": [_case_card(row, clinic_id=clinic_id) for row in cases],
        "documentation_only": True,
        "operative_completion_authorized": False,
        "clinical_execution_authorized": False,
    }
