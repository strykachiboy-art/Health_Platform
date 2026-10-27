from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db

from app.core.auth.user.models.user_model import User
from app.core.enums.appointment_enums import (
    AppointmentStatus,
)
from app.core.enums.billing_enums import (
    InvoiceStatus,
)
from app.core.enums.prescription_enums import (
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import (
    AdmissionStatus,
)
from app.core.exceptions import ValidationError

from app.core.notifications.models.notification_models import (
    Notification,
)

from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.billing.models.billing_model import (
    Invoice,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
)
from app.modules.ward.models.ward_model import (
    Admission,
)

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardQuerySchema,
)

from app.modules.dashboard.schemas.patient_dashboard_schema import (
    PatientDashboardOverviewSchema,
    PatientDashboardSchema,
)

from app.modules.dashboard.services.dashboard_widget_service import (
    build_dashboard_context,
    build_metric,
    resolve_dashboard_period,
)


def get_patient_dashboard(
    *,
    actor: User,
    query: DashboardQuerySchema | None = None,
) -> PatientDashboardSchema:
    if actor.role is not Role.PATIENT:
        raise ValidationError(
            "Patient dashboard is not available "
            "to this user"
        )

    if not actor.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    patient = actor.patient

    if patient is None:
        raise ValidationError(
            "Patient account is not associated "
            "with a patient profile"
        )

    clinic_id = patient.clinic_id

    period = resolve_dashboard_period(
        query,
    )

    now = datetime.now(
        timezone.utc,
    )

    upcoming_appointments = (
        db.session.execute(
            db.select(
                db.func.count(
                    Appointment.id,
                )
            ).where(
                Appointment.clinic_id == clinic_id,
                Appointment.patient_id == patient.id,
                Appointment.status.in_(
                    [
                        AppointmentStatus.SCHEDULED,
                        AppointmentStatus.CONFIRMED,
                    ]
                ),
                Appointment.scheduled_start >= now,
            )
        ).scalar_one()
        or 0
    )

    active_prescriptions = (
        db.session.execute(
            db.select(
                db.func.count(
                    Prescription.id,
                )
            ).where(
                Prescription.clinic_id == clinic_id,
                Prescription.patient_id == patient.id,
                Prescription.status
                == PrescriptionStatus.ACTIVE,
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
            ).where(
                Admission.patient_id == patient.id,
                Admission.status
                == AdmissionStatus.ADMITTED,
            )
        ).scalar_one()
        or 0
    )

    outstanding_balance = (
        db.session.execute(
            db.select(
                db.func.coalesce(
                    db.func.sum(
                        Invoice.total_amount
                        - Invoice.amount_paid,
                    ),
                    0,
                )
            ).where(
                Invoice.clinic_id == clinic_id,
                Invoice.patient_id == patient.id,
                Invoice.status.in_(
                    [
                        InvoiceStatus.ISSUED,
                        InvoiceStatus.PARTIALLY_PAID,
                        InvoiceStatus.OVERDUE,
                    ]
                ),
                Invoice.amount_paid
                < Invoice.total_amount,
            )
        ).scalar_one()
        or 0
    )

    unread_notifications = (
        db.session.execute(
            db.select(
                db.func.count(
                    Notification.id,
                )
            ).where(
                Notification.clinic_id == clinic_id,
                Notification.user_id == actor.id,
                Notification.is_read.is_(False),
            )
        ).scalar_one()
        or 0
    )

    overview = PatientDashboardOverviewSchema(
        upcoming_appointments=int(
            upcoming_appointments
        ),
        active_prescriptions=int(
            active_prescriptions
        ),
        active_admissions=int(
            active_admissions
        ),
        outstanding_balance=outstanding_balance,
        unread_notifications=int(
            unread_notifications
        ),
    )

    metrics = [
        build_metric(
            key="upcoming_appointments",
            label="Upcoming appointments",
            value=int(
                upcoming_appointments
            ),
            unit="appointments",
        ),
        build_metric(
            key="active_prescriptions",
            label="Active prescriptions",
            value=int(
                active_prescriptions
            ),
            unit="prescriptions",
        ),
        build_metric(
            key="outstanding_balance",
            label="Outstanding balance",
            value=outstanding_balance,
            unit="currency",
        ),
        build_metric(
            key="unread_notifications",
            label="Unread notifications",
            value=int(
                unread_notifications
            ),
            unit="notifications",
        ),
    ]

    return PatientDashboardSchema(
        context=build_dashboard_context(
            role=actor.role,
            scope="personal",
            clinic_id=clinic_id,
        ),
        period=period,
        overview=overview,
        metrics=metrics,
    )
