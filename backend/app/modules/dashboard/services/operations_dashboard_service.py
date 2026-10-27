from __future__ import annotations

from app.extensions import db

from app.core.auth.user.models.user_model import User
from app.core.enums.ambulance_enums import (
    TripStatus,
    VehicleStatus,
)
from app.core.enums.appointment_enums import (
    AppointmentStatus,
)
from app.core.enums.department_enums import DepartmentStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_department_enums import StaffDepartmentStatus
from app.core.enums.staff_enums import (
    StaffStatus,
)
from app.core.exceptions import ValidationError

from app.modules.ambulance.models.ambulance_model import (
    AmbulanceTrip,
    AmbulanceVehicle,
)
from app.modules.appointment.models.appointment_model import (
    Appointment,
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

from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardAlertSchema,
    DashboardQuerySchema,
)

from app.modules.dashboard.schemas.operations_dashboard_schema import (
    OperationsDashboardOverviewSchema,
    OperationsDashboardSchema,
)

from app.modules.dashboard.services.dashboard_widget_service import (
    build_chat_summary,
    build_dashboard_context,
    build_metric,
    build_recent_activity,
    period_bounds,
    resolve_dashboard_period,
)


OPERATIONS_ROLES = {
    Role.RECEPTIONIST,
    Role.DRIVER,
    Role.AMBULANCE_DISPATCHER,
    Role.AMBULANCE_COORDINATOR,
    Role.OTHER,
}


def get_operations_dashboard(
    *,
    actor: User,
    query: DashboardQuerySchema | None = None,
) -> OperationsDashboardSchema:
    if actor.role not in OPERATIONS_ROLES:
        raise ValidationError(
            "Operations dashboard is not available "
            "to this user"
        )

    if not actor.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    if actor.clinic_id is None or actor.clinic_id <= 0:
        raise ValidationError(
            "Operations user is not associated "
            "with a valid clinic"
        )

    clinic_id = actor.clinic_id

    period = resolve_dashboard_period(
        query,
    )

    start, end = period_bounds(
        period,
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
                Appointment.status == AppointmentStatus.NO_SHOW,
                Appointment.scheduled_start >= start,
                Appointment.scheduled_start < end,
            )
        ).scalar_one()
        or 0
    )

    scheduled_appointments_today = (
        db.session.execute(
            db.select(
                db.func.count(
                    Appointment.id,
                )
            ).where(
                Appointment.clinic_id == clinic_id,
                Appointment.status
                == AppointmentStatus.SCHEDULED,
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
                Appointment.status
                == AppointmentStatus.CONFIRMED,
                Appointment.scheduled_start >= start,
                Appointment.scheduled_start < end,
            )
        ).scalar_one()
        or 0
    )

    active_ambulance_trips = (
        db.session.execute(
            db.select(
                db.func.count(
                    AmbulanceTrip.id,
                )
            ).where(
                AmbulanceTrip.clinic_id
                == clinic_id,
                AmbulanceTrip.status.in_(
                    [
                        TripStatus.REQUESTED,
                        TripStatus.DISPATCHED,
                        TripStatus.EN_ROUTE_TO_PICKUP,
                        TripStatus.AT_PICKUP,
                        TripStatus.PATIENT_ON_BOARD,
                        TripStatus.EN_ROUTE_TO_DESTINATION,
                    ]
                ),
            )
        ).scalar_one()
        or 0
    )

    pending_ambulance_requests = (
        db.session.execute(
            db.select(
                db.func.count(
                    AmbulanceTrip.id,
                )
            ).where(
                AmbulanceTrip.clinic_id
                == clinic_id,
                AmbulanceTrip.status
                == TripStatus.REQUESTED,
            )
        ).scalar_one()
        or 0
    )

    active_departments = (
        db.session.execute(
            db.select(
                db.func.count(
                    Department.id,
                )
            ).where(
                Department.clinic_id == clinic_id,
                Department.status
                == DepartmentStatus.ACTIVE,
            )
        ).scalar_one()
        or 0
    )

    active_membership_exists = (
        db.select(
            StaffDepartment.id,
        )
        .where(
            StaffDepartment.staff_id == Staff.id,
            StaffDepartment.clinic_id == clinic_id,
            StaffDepartment.status
            == StaffDepartmentStatus.ACTIVE,
        )
        .exists()
    )

    unassigned_active_staff = (
        db.session.execute(
            db.select(
                db.func.count(
                    Staff.id,
                )
            ).where(
                Staff.clinic_id == clinic_id,
                Staff.status == StaffStatus.ACTIVE,
                ~active_membership_exists,
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
                Staff.status
                == StaffStatus.ACTIVE,
            )
        ).scalar_one()
        or 0
    )

    available_vehicles = (
        db.session.execute(
            db.select(
                db.func.count(
                    AmbulanceVehicle.id,
                )
            ).where(
                AmbulanceVehicle.clinic_id
                == clinic_id,
                AmbulanceVehicle.status
                == VehicleStatus.AVAILABLE,
            )
        ).scalar_one()
        or 0
    )

    maintenance_vehicles = (
        db.session.execute(
            db.select(
                db.func.count(
                    AmbulanceVehicle.id,
                )
            ).where(
                AmbulanceVehicle.clinic_id
                == clinic_id,
                AmbulanceVehicle.status
                == VehicleStatus.MAINTENANCE,
            )
        ).scalar_one()
        or 0
    )

    overview = OperationsDashboardOverviewSchema(
        appointments_today=int(
            appointments_today
        ),
        missed_appointments_today=int(
            missed_appointments_today
        ),
        scheduled_appointments_today=int(
            scheduled_appointments_today
        ),
        confirmed_appointments_today=int(
            confirmed_appointments_today
        ),
        active_ambulance_trips=int(
            active_ambulance_trips
        ),
        pending_ambulance_requests=int(
            pending_ambulance_requests
        ),
        active_staff=int(
            active_staff
        ),
        active_departments=int(
            active_departments
        ),
        unassigned_active_staff=int(
            unassigned_active_staff
        ),
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
            key="missed_appointments",
            label="Missed appointments",
            value=int(
                missed_appointments_today
            ),
            unit="appointments",
        ),
        build_metric(
            key="active_ambulance_trips",
            label="Active ambulance trips",
            value=int(
                active_ambulance_trips
            ),
            unit="trips",
        ),
        build_metric(
            key="available_ambulances",
            label="Available ambulances",
            value=int(
                available_vehicles
            ),
            unit="vehicles",
        ),
        build_metric(
            key="maintenance_ambulances",
            label="Ambulances in maintenance",
            value=int(
                maintenance_vehicles
            ),
            unit="vehicles",
        ),
        build_metric(
            key="unread_chat_messages",
            label="Unread chat messages",
            value=chat.unread_messages,
            unit="messages",
        ),
    ]

    alerts = []

    if pending_ambulance_requests:
        alerts.append(
            DashboardAlertSchema(
                key="pending_ambulance_requests",
                severity="critical",
                title="Pending ambulance requests",
                count=int(
                    pending_ambulance_requests
                ),
                description=(
                    "Ambulance requests waiting for dispatch."
                ),
            )
        )

    if maintenance_vehicles:
        alerts.append(
            DashboardAlertSchema(
                key="ambulances_maintenance",
                severity="warning",
                title="Ambulances in maintenance",
                count=int(
                    maintenance_vehicles
                ),
                description=(
                    "Ambulances currently unavailable "
                    "because of maintenance."
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

    return OperationsDashboardSchema(
        context=build_dashboard_context(
            role=actor.role,
            scope="clinic",
            clinic_id=clinic_id,
        ),
        period=period,
        overview=overview,
        chat=chat,
        metrics=metrics,
        alerts=alerts,
        recent_activity=build_recent_activity(
            clinic_id=clinic_id,
        ),
    )