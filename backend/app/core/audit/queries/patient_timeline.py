from __future__ import annotations

from typing import Optional

from app.extensions import db

from app.core.audit.queries.audit_queries import (
    build_audit_query,
)
from app.core.enums.audit_enums import AuditAction


def _build_patient_timeline_query(
    *,
    actor_role,
    actor_clinic_id: Optional[int],
    patient_id: int,
    target_clinic_id: Optional[int] = None,
    action: Optional[AuditAction] = None,
):
    return build_audit_query(
        actor_role=actor_role,
        actor_clinic_id=actor_clinic_id,
        target_clinic_id=target_clinic_id,
        action=action,
        patient_id=patient_id,
    )


def get_patient_timeline(
    *,
    actor_role,
    actor_clinic_id: Optional[int],
    patient_id: int,
    target_clinic_id: Optional[int] = None,
    action: Optional[AuditAction] = None,
):
    statement = _build_patient_timeline_query(
        actor_role=actor_role,
        actor_clinic_id=actor_clinic_id,
        target_clinic_id=target_clinic_id,
        patient_id=patient_id,
        action=action,
    )

    return db.session.execute(
        statement
    ).scalars().all()


def get_patient_timeline_page(
    *,
    actor_role,
    actor_clinic_id: Optional[int],
    patient_id: int,
    target_clinic_id: Optional[int] = None,
    action: Optional[AuditAction] = None,
    page: int = 1,
    per_page: int = 20,
):
    statement = _build_patient_timeline_query(
        actor_role=actor_role,
        actor_clinic_id=actor_clinic_id,
        target_clinic_id=target_clinic_id,
        patient_id=patient_id,
        action=action,
    )

    return db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )


def count_patient_timeline(
    *,
    actor_role,
    actor_clinic_id: Optional[int],
    patient_id: int,
    target_clinic_id: Optional[int] = None,
    action: Optional[AuditAction] = None,
) -> int:
    statement = _build_patient_timeline_query(
        actor_role=actor_role,
        actor_clinic_id=actor_clinic_id,
        target_clinic_id=target_clinic_id,
        patient_id=patient_id,
        action=action,
    ).order_by(None)

    count_statement = db.select(
        db.func.count()
    ).select_from(
        statement.subquery()
    )

    return int(
        db.session.execute(
            count_statement
        ).scalar_one()
    )
