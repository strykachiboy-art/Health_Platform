from __future__ import annotations

from typing import TypeAlias

from app.extensions import db

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    NotFoundError,
    ValidationError,
)

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardQuerySchema,
)

from app.modules.dashboard.schemas.super_admin_dashboard_schema import (
    SuperAdminDashboardSchema,
)
from app.modules.dashboard.schemas.management_dashboard_schema import (
    ManagementDashboardSchema,
)
from app.modules.dashboard.schemas.clinical_dashboard_schema import (
    ClinicalDashboardSchema,
)
from app.modules.dashboard.schemas.operations_dashboard_schema import (
    OperationsDashboardSchema,
)
from app.modules.dashboard.schemas.finance_dashboard_schema import (
    FinanceDashboardSchema,
)
from app.modules.dashboard.schemas.patient_dashboard_schema import (
    PatientDashboardSchema,
)

from app.modules.dashboard.services.super_admin_dashboard_service import (
    get_super_admin_dashboard,
)
from app.modules.dashboard.services.management_dashboard_service import (
    get_management_dashboard,
)
from app.modules.dashboard.services.clinical_dashboard_service import (
    get_clinical_dashboard,
)
from app.modules.dashboard.services.operations_dashboard_service import (
    get_operations_dashboard,
)
from app.modules.dashboard.services.finance_dashboard_service import (
    get_finance_dashboard,
)
from app.modules.dashboard.services.patient_dashboard_service import (
    get_patient_dashboard,
)


DashboardResponse: TypeAlias = (
    SuperAdminDashboardSchema
    | ManagementDashboardSchema
    | ClinicalDashboardSchema
    | OperationsDashboardSchema
    | FinanceDashboardSchema
    | PatientDashboardSchema
)


CLINICAL_ROLES = {
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.PARAMEDIC,
    Role.EMT,
}

OPERATIONS_ROLES = {
    Role.RECEPTIONIST,
    Role.DRIVER,
    Role.AMBULANCE_DISPATCHER,
    Role.AMBULANCE_COORDINATOR,
    Role.OTHER,
}


def _get_active_user(
    actor_id: int,
) -> User:
    if (
        isinstance(actor_id, bool)
        or not isinstance(actor_id, int)
        or actor_id <= 0
    ):
        raise ValidationError(
            "Actor ID must be a positive integer"
        )

    actor = db.session.get(
        User,
        actor_id,
    )

    if actor is None:
        raise NotFoundError(
            f"User {actor_id} not found"
        )

    if not actor.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    return actor


def _dispatch_dashboard(
    *,
    actor_id: int,
    query: DashboardQuerySchema | None = None,
) -> DashboardResponse:
    actor = _get_active_user(
        actor_id,
    )

    if actor.role is Role.SUPER_ADMIN:
        return get_super_admin_dashboard(
            actor=actor,
            query=query,
        )

    if actor.role is Role.ADMIN:
        return get_management_dashboard(
            actor=actor,
            query=query,
        )

    if actor.role in CLINICAL_ROLES:
        return get_clinical_dashboard(
            actor=actor,
            query=query,
        )

    if actor.role in OPERATIONS_ROLES:
        return get_operations_dashboard(
            actor=actor,
            query=query,
        )

    if actor.role is Role.ACCOUNTANT:
        return get_finance_dashboard(
            actor=actor,
            query=query,
        )

    if actor.role is Role.PATIENT:
        return get_patient_dashboard(
            actor=actor,
            query=query,
        )

    raise ValidationError(
        f"No dashboard is configured for role "
        f"'{actor.role.value}'"
    )

# CE1-S11V2: preserve the original six-role dispatcher and enrich its
# validated output with a least-privilege Surgery read model.
def get_dashboard(
    *,
    actor_id: int,
    query: DashboardQuerySchema | None = None,
) -> DashboardResponse:
    actor = _get_active_user(actor_id)
    result = _dispatch_dashboard(actor_id=actor_id, query=query)
    from app.modules.dashboard.services.surgery_dashboard_widget_service import (
        add_surgery_to_existing_dashboard,
    )
    return add_surgery_to_existing_dashboard(result, actor)
