from __future__ import annotations

from sqlalchemy import and_

from app.extensions import db

from app.core.auth.user.models.user_model import User
from app.core.enums.appointment_enums import (
    AppointmentStatus,
)
from app.core.enums.billing_enums import (
    InvoiceStatus,
)
from app.core.enums.department_enums import DepartmentStatus
from app.core.enums.lab_enums import (
    LabOrderStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_department_enums import StaffDepartmentStatus
from app.core.enums.staff_enums import (
    StaffStatus,
)
from app.core.enums.ward_enums import (
    AdmissionStatus,
    BedStatus,
)
from app.core.exceptions import ValidationError

from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.billing.models.billing_model import (
    Invoice,
)
from app.modules.inventory.models.inventory_model import (
    InventoryItem,
)
from app.modules.lab.models.lab_model import (
    LabOrder,
)
from app.modules.department.models.department_model import (
    Department,
)
from app.modules.patient.models.patient_model import (
    Patient,
)
from app.modules.staff.models.staff_department_model import (
    StaffDepartment,
)
from app.modules.staff.models.staff_model import (
    Staff,
)
from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    Ward,
)

from app.modules.dashboard.schemas.department_dashboard_schema import (
    ManagementDepartmentDashboardSchema,
    ManagementDepartmentSummarySchema,
)

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardAlertSchema,
    DashboardMetricSchema,
    DashboardQuerySchema,
)

from app.modules.dashboard.schemas.management_dashboard_schema import (
    ManagementAIDashboardSchema,
    ManagementAIApprovalSummarySchema,
    ManagementAIFeatureUsageSchema,
    ManagementAIOverviewSchema,
    ManagementAIRiskSummarySchema,
    ManagementDashboardOverviewSchema,
    ManagementDashboardSchema,
    ManagementEMAROperationsSchema,
    ManagementHospitalAdmissionsSchema,
    ManagementHospitalOperationsSchema,
    ManagementMedicationReconciliationSchema,
    ManagementNursingOperationsSchema,
)

from app.modules.dashboard.services.dashboard_widget_service import (
    build_ai_aggregates,
    build_chat_summary,
    build_dashboard_context,
    build_metric,
    build_recent_activity,
    period_bounds,
    resolve_dashboard_period,
)
from app.modules.dashboard.services.hospital_dashboard_widget_service import (
    build_hospital_read_model,
)


def _build_management_hospital_operations(
    *,
    clinic_id: int,
) -> ManagementHospitalOperationsSchema:
    data = build_hospital_read_model(
        clinic_id=clinic_id,
    )

    return ManagementHospitalOperationsSchema(
        admissions=ManagementHospitalAdmissionsSchema(
            **data["admissions"],
        ),
        nursing=ManagementNursingOperationsSchema(
            **data["nursing"],
        ),
        emar=ManagementEMAROperationsSchema(
            **data["emar"],
        ),
        medication_reconciliation=(
            ManagementMedicationReconciliationSchema(
                **data["medication_reconciliation"],
            )
        ),
    )


def _management_hospital_presentation(
    *,
    hospital: ManagementHospitalOperationsSchema,
) -> tuple[
    list[DashboardMetricSchema],
    list[DashboardAlertSchema],
]:
    metrics = [
        build_metric(
            key="hospital_available_beds",
            label="Available hospital beds",
            value=hospital.admissions.available_beds,
            unit="beds",
        ),
        build_metric(
            key="urgent_nursing_tasks",
            label="Urgent nursing tasks",
            value=hospital.nursing.urgent_open_tasks,
            unit="tasks",
        ),
        build_metric(
            key="emar_due",
            label="Medications due",
            value=hospital.emar.due,
            unit="administrations",
        ),
        build_metric(
            key="draft_discharge_reconciliations",
            label="Draft discharge reconciliations",
            value=(
                hospital
                .medication_reconciliation
                .draft_discharge
            ),
            unit="reconciliations",
        ),
    ]

    alerts: list[DashboardAlertSchema] = []

    if (
        hospital.admissions.total_beds > 0
        and hospital.admissions.available_beds == 0
    ):
        alerts.append(
            DashboardAlertSchema(
                key="hospital_bed_capacity",
                severity="critical",
                title="No hospital beds available",
                count=hospital.admissions.occupied_beds,
                description=(
                    "All configured hospital beds are currently occupied."
                ),
            )
        )

    if hospital.nursing.urgent_open_tasks:
        alerts.append(
            DashboardAlertSchema(
                key="urgent_nursing_tasks",
                severity="critical",
                title="Urgent nursing tasks",
                count=hospital.nursing.urgent_open_tasks,
                description=(
                    "Urgent nursing tasks remain open across the clinic."
                ),
            )
        )

    if hospital.nursing.overdue_open_tasks:
        alerts.append(
            DashboardAlertSchema(
                key="overdue_nursing_tasks",
                severity="warning",
                title="Overdue nursing tasks",
                count=hospital.nursing.overdue_open_tasks,
                description=(
                    "Nursing tasks are past their due time."
                ),
            )
        )

    if hospital.emar.missed:
        alerts.append(
            DashboardAlertSchema(
                key="missed_medication_administrations",
                severity="critical",
                title="Missed medication administrations",
                count=hospital.emar.missed,
                description=(
                    "Missed eMAR administrations require operational "
                    "follow-up."
                ),
            )
        )

    if (
        hospital
        .medication_reconciliation
        .draft_discharge
    ):
        alerts.append(
            DashboardAlertSchema(
                key="draft_discharge_reconciliations",
                severity="warning",
                title="Draft discharge reconciliations",
                count=(
                    hospital
                    .medication_reconciliation
                    .draft_discharge
                ),
                description=(
                    "Draft discharge medication reconciliations remain "
                    "open."
                ),
            )
        )

    return metrics, alerts


def _build_department_dashboard(
    clinic_id: int,
) -> ManagementDepartmentDashboardSchema:
    rows = db.session.execute(
        db.select(
            Department.id,
            Department.code,
            Department.name,
            Department.status,
            db.func.count(
                Staff.id,
            ).label(
                "active_staff_count",
            ),
            db.func.count(
                Staff.id,
            ).filter(
                StaffDepartment.is_primary.is_(True),
            ).label(
                "primary_staff_count",
            ),
        )
        .outerjoin(
            StaffDepartment,
            and_(
                StaffDepartment.department_id
                == Department.id,
                StaffDepartment.clinic_id
                == Department.clinic_id,
                StaffDepartment.status
                == StaffDepartmentStatus.ACTIVE,
            ),
        )
        .outerjoin(
            Staff,
            and_(
                Staff.id == StaffDepartment.staff_id,
                Staff.clinic_id
                == StaffDepartment.clinic_id,
                Staff.status == StaffStatus.ACTIVE,
            ),
        )
        .where(
            Department.clinic_id == clinic_id,
        )
        .group_by(
            Department.id,
            Department.code,
            Department.name,
            Department.status,
        )
        .order_by(
            Department.name.asc(),
            Department.id.asc(),
        )
    ).all()

    departments = [
        ManagementDepartmentSummarySchema(
            id=department_id,
            code=code,
            name=name,
            status=status,
            active_staff_count=int(
                active_staff_count or 0
            ),
            primary_staff_count=int(
                primary_staff_count or 0
            ),
        )
        for (
            department_id,
            code,
            name,
            status,
            active_staff_count,
            primary_staff_count,
        ) in rows
    ]

    return ManagementDepartmentDashboardSchema(
        total_departments=len(departments),
        active_departments=sum(
            item.status == DepartmentStatus.ACTIVE
            for item in departments
        ),
        inactive_departments=sum(
            item.status == DepartmentStatus.INACTIVE
            for item in departments
        ),
        suspended_departments=sum(
            item.status == DepartmentStatus.SUSPENDED
            for item in departments
        ),
        departments=departments,
    )


def get_management_dashboard(
    *,
    actor: User,
    query: DashboardQuerySchema | None = None,
) -> ManagementDashboardSchema:
    if actor.role is not Role.ADMIN:
        raise ValidationError(
            "Management dashboard is not available "
            "to this user"
        )

    if not actor.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    if actor.clinic_id is None or actor.clinic_id <= 0:
        raise ValidationError(
            "Authenticated administrator is not associated "
            "with a valid clinic"
        )

    clinic_id = actor.clinic_id

    period = resolve_dashboard_period(
        query,
    )

    start, end = period_bounds(
        period,
    )

    total_patients = (
        db.session.execute(
            db.select(
                db.func.count(
                    Patient.id,
                )
            ).where(
                Patient.clinic_id == clinic_id,
            )
        ).scalar_one()
        or 0
    )

    active_patients = (
        db.session.execute(
            db.select(
                db.func.count(
                    Patient.id,
                )
            ).where(
                Patient.clinic_id == clinic_id,
                Patient.is_active.is_(True),
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
            ).where(
                Staff.clinic_id == clinic_id,
            )
        ).scalar_one()
        or 0
    )

    active_staff = (
        db.session.execute(
            db.select(
                db.func.count(
                    Staff.id,
                )
            ).where(
                Staff.clinic_id == clinic_id,
                Staff.status == StaffStatus.ACTIVE,
            )
        ).scalar_one()
        or 0
    )

    appointments_today = (
        db.session.execute(
            db.select(
                db.func.count(
                    Appointment.id,
                )
            ).where(
                Appointment.clinic_id == clinic_id,
                Appointment.status.in_(
                    [
                        AppointmentStatus.SCHEDULED,
                        AppointmentStatus.CONFIRMED,
                    ]
                ),
                Appointment.scheduled_start >= start,
                Appointment.scheduled_start < end,
            )
        ).scalar_one()
        or 0
    )

    missed_appointments_today = (
        db.session.execute(
            db.select(
                db.func.count(
                    Appointment.id,
                )
            ).where(
                Appointment.clinic_id == clinic_id,
                Appointment.status
                == AppointmentStatus.NO_SHOW,
                Appointment.scheduled_start >= start,
                Appointment.scheduled_start < end,
            )
        ).scalar_one()
        or 0
    )

    active_admissions = (
        db.session.execute(
            db.select(
                db.func.count(
                    Admission.id,
                )
            )
            .join(
                Patient,
                Admission.patient_id
                == Patient.id,
            )
            .where(
                Patient.clinic_id == clinic_id,
                Admission.status
                == AdmissionStatus.ADMITTED,
            )
        ).scalar_one()
        or 0
    )

    occupied_beds = (
        db.session.execute(
            db.select(
                db.func.count(
                    Bed.id,
                )
            )
            .join(
                Ward,
                Bed.ward_id
                == Ward.id,
            )
            .where(
                Ward.clinic_id == clinic_id,
                Bed.status
                == BedStatus.OCCUPIED,
            )
        ).scalar_one()
        or 0
    )

    pending_lab_orders = (
        db.session.execute(
            db.select(
                db.func.count(
                    LabOrder.id,
                )
            ).where(
                LabOrder.clinic_id == clinic_id,
                LabOrder.status.in_(
                    [
                        LabOrderStatus.ORDERED,
                        LabOrderStatus.SAMPLE_COLLECTED,
                        LabOrderStatus.IN_PROGRESS,
                    ]
                ),
            )
        ).scalar_one()
        or 0
    )

    department_dashboard = _build_department_dashboard(
        clinic_id,
    )

    overview = ManagementDashboardOverviewSchema(
        total_patients=int(
            total_patients
        ),
        active_patients=int(
            active_patients
        ),
        total_staff=int(
            total_staff
        ),
        active_staff=int(
            active_staff
        ),
        appointments_today=int(
            appointments_today
        ),
        missed_appointments_today=int(
            missed_appointments_today
        ),
        active_admissions=int(
            active_admissions
        ),
        occupied_beds=int(
            occupied_beds
        ),
        pending_lab_orders=int(
            pending_lab_orders
        ),
    )

    hospital = _build_management_hospital_operations(
        clinic_id=clinic_id,
    )

    (
        hospital_metrics,
        hospital_alerts,
    ) = _management_hospital_presentation(
        hospital=hospital,
    )

    ai_data = build_ai_aggregates(
        clinic_id=clinic_id,
        period=period,
    )

    ai = ManagementAIDashboardSchema(
        overview=ManagementAIOverviewSchema(
            total_ai_requests=ai_data[
                "total_ai_requests"
            ],
            total_credits_used=ai_data[
                "total_credits_used"
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
            high_risk_results=ai_data[
                "high_risk_results"
            ],
            critical_risk_results=ai_data[
                "critical_risk_results"
            ],
        ),
        feature_usage=[
            ManagementAIFeatureUsageSchema(
                **item,
            )
            for item in ai_data[
                "feature_usage"
            ]
        ],
        risk_summary=[
            ManagementAIRiskSummarySchema(
                **item,
            )
            for item in ai_data[
                "risk_summary"
            ]
        ],
        approval_summary=[
            ManagementAIApprovalSummarySchema(
                **item,
            )
            for item in ai_data[
                "approval_summary"
            ]
        ],
    )

    chat = build_chat_summary(
        user_id=actor.id,
        clinic_id=clinic_id,
        period=period,
    )

    low_stock_items = (
        db.session.execute(
            db.select(
                db.func.count(
                    InventoryItem.id,
                )
            ).where(
                InventoryItem.clinic_id
                == clinic_id,
                InventoryItem.is_active.is_(True),
                InventoryItem.quantity_on_hand
                <= InventoryItem.reorder_level,
            )
        ).scalar_one()
        or 0
    )

    overdue_invoices = (
        db.session.execute(
            db.select(
                db.func.count(
                    Invoice.id,
                )
            ).where(
                Invoice.clinic_id == clinic_id,
                Invoice.status
                == InvoiceStatus.OVERDUE,
            )
        ).scalar_one()
        or 0
    )

    metrics = [
        build_metric(
            key="active_patients",
            label="Active patients",
            value=int(
                active_patients
            ),
            unit="patients",
        ),
        build_metric(
            key="active_staff",
            label="Active staff",
            value=int(
                active_staff
            ),
            unit="staff",
        ),
        build_metric(
            key="low_stock_items",
            label="Low stock items",
            value=int(
                low_stock_items
            ),
            unit="items",
        ),
        build_metric(
            key="ai_requests",
            label="AI requests",
            value=ai_data[
                "total_ai_requests"
            ],
            unit="requests",
        ),
        build_metric(
            key="missed_appointments",
            label="Missed appointments",
            value=int(
                missed_appointments_today
            ),
            unit="appointments",
        ),
        build_metric(
            key="unread_chat_messages",
            label="Unread chat messages",
            value=chat.unread_messages,
            unit="messages",
        ),
    ]

    metrics.extend(
        hospital_metrics,
    )

    alerts = []
    alerts.extend(
        hospital_alerts,
    )

    if low_stock_items:
        alerts.append(
            DashboardAlertSchema(
                key="low_stock",
                severity="warning",
                title="Low stock items",
                count=int(
                    low_stock_items
                ),
                description=(
                    "Inventory items at or below "
                    "their reorder level."
                ),
            )
        )

    if overdue_invoices:
        alerts.append(
            DashboardAlertSchema(
                key="overdue_invoices",
                severity="warning",
                title="Overdue invoices",
                count=int(
                    overdue_invoices
                ),
                description=(
                    "Invoices currently marked as overdue."
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
                    "Critical-risk AI results "
                    "recorded for this clinic."
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

    if chat.priority_messages:
        alerts.append(
            DashboardAlertSchema(
                key="priority_chat_messages",
                severity="critical",
                title="Priority chat messages",
                count=chat.priority_messages,
                description=(
                    "Urgent or STAT messages "
                    "in your active conversations."
                ),
            )
        )

    return ManagementDashboardSchema(
        context=build_dashboard_context(
            role=actor.role,
            scope="clinic",
            clinic_id=clinic_id,
        ),
        period=period,
        overview=overview,
        hospital=hospital,
        departments=department_dashboard,
        ai=ai,
        chat=chat,
        metrics=metrics,
        alerts=alerts,
        recent_activity=build_recent_activity(
            clinic_id=clinic_id,
        ),
    )