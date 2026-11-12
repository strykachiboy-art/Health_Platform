"""CE1-S01 internal-only surgical case planning.

This cannot authorize an operation, consent, checklist, room allocation or discharge.
Use only active-admission surgical procedure orders; day cases are later scope.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.clinical_orders.models.procedure_order_model import ProcedureOrder
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.modules.surgery.models.surgical_case_model import SurgicalCase


def _positive(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{name} must be a positive integer")
    return value


def _doctor(actor_staff_id: int, clinic_id: int) -> Staff:
    actor = db.session.execute(
        select(Staff).where(
            Staff.id == _positive(actor_staff_id, "actor_staff_id"),
            Staff.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if actor is None:
        raise NotFoundError("Surgical planning actor not found")
    if (
        actor.status != StaffStatus.ACTIVE
        or actor.user is None
        or not actor.user.is_active
        or actor.user.role != Role.DOCTOR
    ):
        raise ValidationError("Active doctor required for surgical planning")
    return actor


def _active_admission(admission_id: int, clinic_id: int) -> Admission:
    admission = db.session.execute(
        select(Admission)
        .join(Bed, Admission.bed_id == Bed.id)
        .join(Ward, Bed.ward_id == Ward.id)
        .where(
            Admission.id == admission_id,
            Ward.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if admission is None:
        raise NotFoundError("Surgical admission not found")
    if admission.status != AdmissionStatus.ADMITTED:
        raise ConflictError("Active admission required for surgical case")
    return admission


@transactional
def plan_surgical_case(
    *, procedure_order_id: int, clinic_id: int, actor_staff_id: int
) -> SurgicalCase:
    """Create one inert planning case for one surgical order.

    A second call never silently creates a second execution record.
    S01 does not expose this method through HTTP and does not start surgery.
    """
    clinic_id = _positive(clinic_id, "clinic_id")
    order_id = _positive(procedure_order_id, "procedure_order_id")
    actor = _doctor(actor_staff_id, clinic_id)

    order = db.session.execute(
        select(ProcedureOrder).where(
            ProcedureOrder.id == order_id,
            ProcedureOrder.clinic_id == clinic_id,
        ).with_for_update()
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("Surgical procedure order not found")
    if order.procedure_type != "surgical":
        raise ValidationError("Only surgical ProcedureOrders can create cases")
    if order.status != "active":
        raise ConflictError("Surgical procedure order must be active")
    if order.consent_required is not True:
        raise ConflictError("Surgical procedure must require consent")

    admission = _active_admission(order.admission_id, clinic_id)
    if order.patient_id != admission.patient_id:
        raise ConflictError("Procedure order patient/admission mismatch")

    existing = db.session.execute(
        select(SurgicalCase.id).where(
            SurgicalCase.procedure_order_id == order.id
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("Surgical case already exists for procedure order")

    case = SurgicalCase(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        procedure_order_id=order.id,
        planned_by_staff_id=actor.id,
        status="planned",
        version=1,
    )
    db.session.add(case)
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="SurgicalCase",
        entity_id=case.id,
        description="Planned surgical case; not authorized for execution",
        new_value={
            "procedure_order_id": order.id,
            "admission_id": admission.id,
            "status": case.status,
            "version": case.version,
        },
        user_id=actor.user_id,
        clinic_id=clinic_id,
    )
    return case


def get_surgical_case(*, case_id: int, clinic_id: int) -> SurgicalCase:
    """Clinic-scoped internal read, without broadening web route exposure."""
    case = db.session.execute(
        select(SurgicalCase).where(
            SurgicalCase.id == _positive(case_id, "case_id"),
            SurgicalCase.clinic_id == _positive(clinic_id, "clinic_id"),
        )
    ).scalar_one_or_none()
    if case is None:
        raise NotFoundError("Surgical case not found")
    return case
