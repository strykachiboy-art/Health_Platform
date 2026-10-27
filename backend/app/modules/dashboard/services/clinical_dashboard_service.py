from __future__ import annotations

from sqlalchemy import and_

from app.extensions import db

from app.modules.patient.models.patient_model import (
    Patient,
)

from app.core.auth.user.models.user_model import User
from app.core.enums.appointment_enums import (
    AppointmentStatus,
)
from app.core.enums.consultation_enums import (
    ConsultationStatus,
)
from app.core.enums.lab_enums import (
    LabOrderStatus,
)
from app.core.enums.prescription_enums import (
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_department_enums import StaffDepartmentStatus
from app.core.enums.staff_enums import (
    StaffStatus,
)
from app.core.enums.ward_enums import (
    AdmissionStatus,
)
from app.core.exceptions import ValidationError

from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.consultation.models.consultation_model import (
    Consultation,
)
from app.modules.lab.models.lab_model import (
    LabOrder,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
)
from app.modules.department.models.department_model import (
    Department,
)
from app.modules.staff.models.staff_department_model import (
    StaffDepartment,
)
from app.modules.staff.models.staff_model import (
    Staff,
)
from app.modules.ward.models.ward_model import (
    Admission,
)

from app.modules.dashboard.schemas.department_dashboard_schema import (
    ClinicalDepartmentSchema,
)

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardAlertSchema,
    DashboardQuerySchema,
)

from app.modules.dashboard.schemas.clinical_dashboard_schema import (
    ClinicalAIDashboardSchema,
    ClinicalAIOverviewSchema,
    ClinicalAIFeatureUsageSchema,
    ClinicalAIReviewSummarySchema,
    ClinicalAIRiskSummarySchema,
    ClinicalDashboardOverviewSchema,
    ClinicalDashboardSchema,
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


CLINICAL_ROLES = {
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.PARAMEDIC,
    Role.EMT,
}


def _get_my_departments(
    staff_id: int,
    clinic_id: int,
) -> list[ClinicalDepartmentSchema]:
    rows = db.session.execute(
        db.select(
            Department.id,
            Department.code,
            Department.name,
            StaffDepartment.status,
            StaffDepartment.is_primary,
        )
        .join(
            StaffDepartment,
            and_(
                StaffDepartment.department_id
                == Department.id,
                StaffDepartment.clinic_id
                == Department.clinic_id,
            ),
        )
        .where(
            Department.clinic_id == clinic_id,
            StaffDepartment.staff_id == staff_id,
            StaffDepartment.status
            != StaffDepartmentStatus.ENDED,
        )
        .order_by(
            StaffDepartment.is_primary.desc(),
            StaffDepartment.status.asc(),
            StaffDepartment.assigned_at.asc(),
            StaffDepartment.id.asc(),
        )
    ).all()

    return [
        ClinicalDepartmentSchema(
            department_id=department_id,
            code=code,
            name=name,
            status=status,
            is_primary=is_primary,
        )
        for (
            department_id,
            code,
            name,
            status,
            is_primary,
        ) in rows
    ]


def get_clinical_dashboard(
    *,
    actor: User,
    query: DashboardQuerySchema | None = None,
) -> ClinicalDashboardSchema:
    if actor.role not in CLINICAL_ROLES:
        raise ValidationError(
            "Clinical dashboard is not available "
            "to this user"
        )

    if not actor.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    if actor.clinic_id is None or actor.clinic_id <= 0:
        raise ValidationError(
            "Clinical user is not associated "
            "with a valid clinic"
        )

    staff = actor.staff

    if staff is None:
        raise ValidationError(
            "Clinical user is not associated "
            "with a staff profile"
        )

    if staff.status is not StaffStatus.ACTIVE:
        raise ValidationError(
            "Clinical staff profile is not active"
        )

    clinic_id = actor.clinic_id

    period = resolve_dashboard_period(
        query,
    )

    start, end = period_bounds(
        period,
    )

    my_departments = _get_my_departments(
        staff_id=staff.id,
        clinic_id=clinic_id,
    )

    appointments_today = (
        db.session.execute(
            db.select(
                db.func.count(
                    Appointment.id,
                )
            ).where(
                Appointment.clinic_id == clinic_id,
                Appointment.staff_id == staff.id,
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

    confirmed_appointments_today = (
        db.session.execute(
            db.select(
                db.func.count(
                    Appointment.id,
                )
            ).where(
                Appointment.clinic_id == clinic_id,
                Appointment.staff_id == staff.id,
                Appointment.status
                == AppointmentStatus.CONFIRMED,
                Appointment.scheduled_start >= start,
                Appointment.scheduled_start < end,
            )
        ).scalar_one()
        or 0
    )

    pending_consultations = (
        db.session.execute(
            db.select(
                db.func.count(
                    Consultation.id,
                )
            ).where(
                Consultation.clinic_id == clinic_id,
                Consultation.staff_id == staff.id,
                Consultation.status
                == ConsultationStatus.IN_PROGRESS,
            )
        ).scalar_one()
        or 0
    )

    lab_statement = (
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
    )

    if actor.role is not Role.LAB_TECHNICIAN:
        lab_statement = lab_statement.where(
            LabOrder.ordered_by_id == staff.id,
        )

    pending_lab_orders = (
        db.session.execute(
            lab_statement,
        ).scalar_one()
        or 0
    )

    prescription_statement = (
        db.select(
            db.func.count(
                Prescription.id,
            )
        ).where(
            Prescription.clinic_id
            == clinic_id,
            Prescription.status
            == PrescriptionStatus.ACTIVE,
        )
    )

    if actor.role is not Role.PHARMACIST:
        prescription_statement = (
            prescription_statement.where(
                Prescription.prescribed_by_id
                == staff.id,
            )
        )

    active_prescriptions = (
        db.session.execute(
            prescription_statement,
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
            Admission.patient_id == Patient.id,
        )
        .where(
            Patient.clinic_id == clinic_id,
            Admission.status
            == AdmissionStatus.ADMITTED,
        )
    ).scalar_one()
    or 0
   )

    overview = ClinicalDashboardOverviewSchema(
        appointments_today=int(
            appointments_today
        ),
        confirmed_appointments_today=int(
            confirmed_appointments_today
        ),
        pending_consultations=int(
            pending_consultations
        ),
        pending_lab_orders=int(
            pending_lab_orders
        ),
        active_prescriptions=int(
            active_prescriptions
        ),
        active_admissions=int(
            active_admissions
        ),
    )

    ai_data = build_ai_aggregates(
        clinic_id=clinic_id,
        user_id=actor.id,
        period=period,
    )

    ai = ClinicalAIDashboardSchema(
        overview=ClinicalAIOverviewSchema(
            ai_requests=ai_data[
                "total_ai_requests"
            ],
            pending_reviews=ai_data[
                "pending_reviews"
            ],
            high_risk_results=ai_data[
                "high_risk_results"
            ],
            critical_risk_results=ai_data[
                "critical_risk_results"
            ],
            approved_results=ai_data[
                "approved_reviews"
            ],
            rejected_results=ai_data[
                "rejected_reviews"
            ],
        ),
        feature_usage=[
            ClinicalAIFeatureUsageSchema(
                feature=item[
                    "feature"
                ],
                request_count=item[
                    "request_count"
                ],
            )
            for item in ai_data[
                "feature_usage"
            ]
        ],
        risk_summary=[
            ClinicalAIRiskSummarySchema(
                **item,
            )
            for item in ai_data[
                "risk_summary"
            ]
        ],
        review_summary=[
            ClinicalAIReviewSummarySchema(
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

    metrics = [
        build_metric(
            key="appointments",
            label="Appointments",
            value=int(
                appointments_today
            ),
            unit="appointments",
        ),
        build_metric(
            key="pending_consultations",
            label="Pending consultations",
            value=int(
                pending_consultations
            ),
            unit="consultations",
        ),
        build_metric(
            key="pending_labs",
            label="Pending laboratory orders",
            value=int(
                pending_lab_orders
            ),
            unit="orders",
        ),
        build_metric(
            key="ai_requests",
            label="My AI requests",
            value=ai_data[
                "total_ai_requests"
            ],
            unit="requests",
        ),
        build_metric(
            key="unread_chat",
            label="Unread chat messages",
            value=chat.unread_messages,
            unit="messages",
        ),
    ]

    alerts = []

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
                    "generated under your account."
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
                    "Your AI results awaiting review."
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
                    "in active conversations."
                ),
            )
        )

    return ClinicalDashboardSchema(
        context=build_dashboard_context(
            role=actor.role,
            scope="clinic",
            clinic_id=clinic_id,
        ),
        period=period,
        overview=overview,
        my_departments=my_departments,
        ai=ai,
        chat=chat,
        metrics=metrics,
        alerts=alerts,
        recent_activity=build_recent_activity(
            clinic_id=clinic_id,
        ),
    )