from __future__ import annotations

from app.extensions import db

from app.core.auth.user.models.user_model import User
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.department_enums import DepartmentStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError

from app.modules.clinic.models.clinic_model import Clinic
from app.modules.department.models.department_model import Department
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff

from app.modules.dashboard.schemas.department_dashboard_schema import (
    SuperAdminDepartmentClinicSummarySchema,
    SuperAdminDepartmentDashboardSchema,
)

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardAlertSchema,
    DashboardQuerySchema,
)

from app.modules.dashboard.schemas.super_admin_dashboard_schema import (
    SuperAdminAccessControlOverviewSchema,
    SuperAdminAIDashboardSchema,
    SuperAdminAIApprovalSummarySchema,
    SuperAdminAIFeatureUsageSchema,
    SuperAdminAIOverviewSchema,
    SuperAdminAIRiskSummarySchema,
    SuperAdminDashboardOverviewSchema,
    SuperAdminDashboardSchema,
)

from app.modules.dashboard.services.dashboard_widget_service import (
    build_ai_aggregates,
    build_dashboard_context,
    build_metric,
    build_recent_activity,
    resolve_dashboard_period,
)


def get_super_admin_dashboard(
    *,
    actor: User,
    query: DashboardQuerySchema | None = None,
) -> SuperAdminDashboardSchema:
    if actor.role is not Role.SUPER_ADMIN:
        raise ValidationError(
            "Super administrator dashboard is not available "
            "to this user"
        )

    if not actor.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    period = resolve_dashboard_period(
        query,
    )

    total_clinics = (
        db.session.execute(
            db.select(
                db.func.count(
                    Clinic.id,
                )
            )
        ).scalar_one()
        or 0
    )

    active_clinics = (
        db.session.execute(
            db.select(
                db.func.count(
                    Clinic.id,
                )
            ).where(
                Clinic.status
                == ClinicStatus.ACTIVE,
            )
        ).scalar_one()
        or 0
    )

    suspended_clinics = (
        db.session.execute(
            db.select(
                db.func.count(
                    Clinic.id,
                )
            ).where(
                Clinic.status
                == ClinicStatus.SUSPENDED,
            )
        ).scalar_one()
        or 0
    )

    total_users = (
        db.session.execute(
            db.select(
                db.func.count(
                    User.id,
                )
            )
        ).scalar_one()
        or 0
    )

    active_users = (
        db.session.execute(
            db.select(
                db.func.count(
                    User.id,
                )
            ).where(
                User.is_active.is_(True),
            )
        ).scalar_one()
        or 0
    )

    total_staff = (
        db.session.execute(
            db.select(
                db.func.count(
                    Staff.id,
                )
            )
        ).scalar_one()
        or 0
    )

    total_patients = (
        db.session.execute(
            db.select(
                db.func.count(
                    Patient.id,
                )
            )
        ).scalar_one()
        or 0
    )

    department_rows = db.session.execute(
        db.select(
            Clinic.id,
            Clinic.name,
            db.func.count(
                Department.id,
            ).label(
                "total_departments",
            ),
            db.func.count(
                Department.id,
            ).filter(
                Department.status
                == DepartmentStatus.ACTIVE,
            ).label(
                "active_departments",
            ),
        )
        .outerjoin(
            Department,
            Department.clinic_id == Clinic.id,
        )
        .group_by(
            Clinic.id,
            Clinic.name,
        )
        .order_by(
            Clinic.name.asc(),
            Clinic.id.asc(),
        )
    ).all()

    department_dashboard = (
        SuperAdminDepartmentDashboardSchema(
            total_departments=sum(
                int(total or 0)
                for (
                    _clinic_id,
                    _clinic_name,
                    total,
                    _active,
                ) in department_rows
            ),
            active_departments=sum(
                int(active or 0)
                for (
                    _clinic_id,
                    _clinic_name,
                    _total,
                    active,
                ) in department_rows
            ),
            by_clinic=[
                SuperAdminDepartmentClinicSummarySchema(
                    clinic_id=clinic_id,
                    clinic_name=clinic_name,
                    total_departments=int(
                        total or 0
                    ),
                    active_departments=int(
                        active or 0
                    ),
                )
                for (
                    clinic_id,
                    clinic_name,
                    total,
                    active,
                ) in department_rows
            ],
        )
    )

    overview = SuperAdminDashboardOverviewSchema(
        total_clinics=int(
            total_clinics
        ),
        active_clinics=int(
            active_clinics
        ),
        suspended_clinics=int(
            suspended_clinics
        ),
        total_users=int(
            total_users
        ),
        active_users=int(
            active_users
        ),
        total_staff=int(
            total_staff
        ),
        total_patients=int(
            total_patients
        ),
    )

    role_rows = db.session.execute(
        db.select(
            User.role,
            db.func.count(
                User.id,
            ),
        )
        .group_by(
            User.role,
        )
    ).all()

    users_by_role = {
        role.value: int(
            count or 0
        )
        for role, count in role_rows
    }

    total_admins = users_by_role.get(
        Role.ADMIN.value,
        0,
    )

    total_super_admins = users_by_role.get(
        Role.SUPER_ADMIN.value,
        0,
    )

    active_admins = (
        db.session.execute(
            db.select(
                db.func.count(
                    User.id,
                )
            ).where(
                User.role == Role.ADMIN,
                User.is_active.is_(True),
            )
        ).scalar_one()
        or 0
    )

    access_control = (
        SuperAdminAccessControlOverviewSchema(
            total_users=int(
                total_users
            ),
            active_users=int(
                active_users
            ),
            inactive_users=int(
                total_users - active_users
            ),
            total_admins=int(
                total_admins
            ),
            active_admins=int(
                active_admins
            ),
            total_super_admins=int(
                total_super_admins
            ),
            users_by_role=users_by_role,
        )
    )

    ai_data = build_ai_aggregates(
        period=period,
    )

    ai = SuperAdminAIDashboardSchema(
        overview=SuperAdminAIOverviewSchema(
            total_ai_requests=ai_data[
                "total_ai_requests"
            ],
            total_credits_used=ai_data[
                "total_credits_used"
            ],
            total_input_tokens=ai_data[
                "total_input_tokens"
            ],
            total_output_tokens=ai_data[
                "total_output_tokens"
            ],
            total_tokens=ai_data[
                "total_tokens"
            ],
            estimated_cost=ai_data[
                "estimated_cost"
            ],
            pending_reviews=ai_data[
                "pending_reviews"
            ],
            approved_reviews=ai_data[
                "approved_reviews"
            ],
            rejected_reviews=ai_data[
                "rejected_reviews"
            ],
            low_risk_results=ai_data[
                "low_risk_results"
            ],
            medium_risk_results=ai_data[
                "medium_risk_results"
            ],
            high_risk_results=ai_data[
                "high_risk_results"
            ],
            critical_risk_results=ai_data[
                "critical_risk_results"
            ],
        ),
        feature_usage=[
            SuperAdminAIFeatureUsageSchema(
                **item,
            )
            for item in ai_data[
                "feature_usage"
            ]
        ],
        risk_summary=[
            SuperAdminAIRiskSummarySchema(
                **item,
            )
            for item in ai_data[
                "risk_summary"
            ]
        ],
        approval_summary=[
            SuperAdminAIApprovalSummarySchema(
                **item,
            )
            for item in ai_data[
                "approval_summary"
            ]
        ],
    )

    alerts = []

    if suspended_clinics:
        alerts.append(
            DashboardAlertSchema(
                key="suspended_clinics",
                severity="warning",
                title="Suspended clinics",
                count=int(
                    suspended_clinics
                ),
                description=(
                    "Clinics currently marked as suspended."
                ),
            )
        )

    if ai_data[
        "critical_risk_results"
    ]:
        alerts.append(
            DashboardAlertSchema(
                key="critical_ai_results",
                severity="critical",
                title="Critical AI results",
                count=ai_data[
                    "critical_risk_results"
                ],
                description=(
                    "AI results classified at critical risk "
                    "within the selected dashboard period."
                ),
            )
        )

    if ai_data[
        "pending_reviews"
    ]:
        alerts.append(
            DashboardAlertSchema(
                key="pending_ai_reviews",
                severity="warning",
                title="Pending AI reviews",
                count=ai_data[
                    "pending_reviews"
                ],
                description=(
                    "AI results awaiting review."
                ),
            )
        )

    metrics = [
        build_metric(
            key="active_clinics",
            label="Active clinics",
            value=int(
                active_clinics
            ),
            unit="clinics",
        ),
        build_metric(
            key="active_users",
            label="Active users",
            value=int(
                active_users
            ),
            unit="users",
        ),
        build_metric(
            key="administrators",
            label="Administrators",
            value=int(
                total_admins
            ),
            unit="users",
        ),
        build_metric(
            key="ai_requests",
            label="AI requests",
            value=ai_data[
                "total_ai_requests"
            ],
            unit="requests",
        ),
    ]

    return SuperAdminDashboardSchema(
        context=build_dashboard_context(
            role=actor.role,
            scope="system",
            clinic_id=None,
        ),
        period=period,
        overview=overview,
        departments=department_dashboard,
        access_control=access_control,
        ai=ai,
        metrics=metrics,
        alerts=alerts,
        recent_activity=build_recent_activity(
            clinic_id=None,
        ),
    )