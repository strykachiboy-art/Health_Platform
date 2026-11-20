from __future__ import annotations

from datetime import datetime, timedelta

from app.core.enums.appointment_enums import (
    AppointmentStatus,
    AppointmentType,
)
from app.core.enums.notification_enums import (
    NotificationChannel,
    NotificationStatus,
)
from app.modules.appointment.models.appointment_model import Appointment
from app.modules.appointment.services import appointment_service
from app.core.notifications.services import notification_service
from app.modules.clinic.models.organization_model import (
    TenantMembership, ClinicMembership,
)


def test_appointment_background_job_requires_matching_clinic(
    db,
    make_clinic,
    make_patient,
    make_staff,
):
    clinic_a = make_clinic(name="Background Job Clinic A")
    clinic_b = make_clinic(name="Background Job Clinic B")
    patient_b = make_patient(clinic_b)
    staff_b = make_staff(clinic_b)

    appointment = Appointment(
        clinic_id=clinic_b.id,
        patient_id=patient_b.id,
        staff_id=staff_b.id,
        scheduled_start=datetime.now(),
        scheduled_end=datetime.now() + timedelta(hours=1),
        status=AppointmentStatus.SCHEDULED,
        appointment_type=AppointmentType.IN_PERSON,
        reminder_sent=False,
    )

    db.session.add(appointment)
    db.session.flush()

    appointment_service.send_appointment_reminder(
        clinic_a.id,
        appointment.id,
    )

    db.session.refresh(appointment)

    assert appointment.clinic_id == clinic_b.id
    assert appointment.reminder_sent is False

    appointment_service.send_appointment_reminder(
        clinic_b.id,
        appointment.id,
    )

    db.session.refresh(appointment)

    assert appointment.reminder_sent is True


def test_notification_background_job_requires_matching_clinic(
    make_clinic,
    make_staff,
    make_notification,
    monkeypatch,
    db_session,
):
    clinic_a = make_clinic(name="Notification Clinic A")
    clinic_b = make_clinic(name="Notification Clinic B")
    staff_b = make_staff(clinic_b)
    # Legitimate own-clinic delivery requires an ACTIVE recipient grant.
    # The foreign-clinic delivery must remain denied before provider egress.
    db_session.add(TenantMembership(
        user_id=staff_b.user.id, organization_id=clinic_b.organization_id,
        role='member', status='active', is_active=True,
    ))
    db_session.flush()
    db_session.add(ClinicMembership(
        user_id=staff_b.user.id, organization_id=clinic_b.organization_id,
        clinic_id=clinic_b.id, role=staff_b.user.role.value, is_active=True,
    ))
    db_session.flush()

    notification = make_notification(
        clinic_id=clinic_b.id,
        user_id=staff_b.user.id,
        channel=NotificationChannel.EMAIL,
        status=NotificationStatus.PENDING,
    )

    delivered = []

    def fake_delivery(notification_obj):
        delivered.append(
            (
                notification_obj.id,
                notification_obj.clinic_id,
            )
        )
        return True

    monkeypatch.setattr(
        notification_service,
        "_deliver_with_provider",
        fake_delivery,
    )

    result = notification_service.deliver_notification(
        clinic_a.id,
        notification.id,
    )

    db_session.refresh(notification)

    assert result is False
    assert delivered == []
    assert notification.status == NotificationStatus.PENDING

    result = notification_service.deliver_notification(
        clinic_b.id,
        notification.id,
    )

    db_session.refresh(notification)

    assert result is True
    assert delivered == [
        (notification.id, clinic_b.id),
    ]
    assert notification.status == NotificationStatus.DELIVERED