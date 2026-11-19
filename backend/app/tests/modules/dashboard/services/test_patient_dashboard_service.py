from __future__ import annotations

from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal

import pytest

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
from app.core.exceptions import ConflictError, ValidationError

from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.billing.models.billing_model import (
    Invoice,
)
from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardQuerySchema,
)
from app.modules.dashboard.services import (
    patient_dashboard_service,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
)
from app.modules.ward.models.ward_model import (
    Admission,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_appointment(
    db_session,
    clinic,
    patient,
    staff,
    *,
    scheduled_start: datetime,
    status: AppointmentStatus,
):
    appointment = Appointment(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_start + timedelta(hours=1),
        status=status,
    )

    db_session.add(appointment)
    db_session.flush()

    return appointment


def _make_prescription(
    db_session,
    clinic,
    patient,
    staff,
    *,
    status: PrescriptionStatus,
):
    prescription = Prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        status=status,
    )

    db_session.add(prescription)
    db_session.flush()

    return prescription


def _make_invoice(
    db_session,
    clinic,
    patient,
    *,
    invoice_number: str,
    total_amount,
    amount_paid,
    status: InvoiceStatus,
):
    invoice = Invoice(
        clinic_id=clinic.id,
        patient_id=patient.id,
        invoice_number=invoice_number,
        total_amount=Decimal(str(total_amount)),
        amount_paid=Decimal(str(amount_paid)),
        status=status,
    )

    db_session.add(invoice)
    db_session.flush()

    return invoice


def _make_notification(
    db_session,
    clinic,
    user,
    make_notification,
    *,
    is_read: bool,
):
    return make_notification(
        clinic_id=clinic.id,
        user_id=user.id,
        is_read=is_read,
    )


def _make_admission(
    db_session,
    patient,
    staff,
    make_ward,
    make_bed,
    *,
    status: AdmissionStatus,
    bed_number: str,
):
    ward = make_ward(
        clinic=patient.clinic,
        capacity=10,
    )

    bed = make_bed(
        ward,
        bed_number=bed_number,
    )

    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=staff.id,
        status=status,
    )

    db_session.add(admission)
    db_session.flush()

    return admission


@pytest.fixture
def patient_actor(
    make_user,
    make_patient,
    clinic,
):
    actor = make_user(
        clinic=clinic,
        role=Role.PATIENT,
    )

    patient = make_patient(
        clinic=clinic,
        user_id=actor.id,
    )

    return actor, patient


def test_rejects_non_patient_role(
    make_user,
    clinic,
):
    actor = make_user(
        clinic=clinic,
        role=Role.ADMIN,
    )

    with pytest.raises(
        ValidationError,
        match="Patient dashboard is not available",
    ):
        patient_dashboard_service.get_patient_dashboard(
            actor=actor,
        )


@pytest.mark.parametrize(
    "role",
    [
        Role.SUPER_ADMIN,
        Role.ADMIN,
        Role.DOCTOR,
        Role.NURSE,
        Role.PHARMACIST,
        Role.LAB_TECHNICIAN,
        Role.RECEPTIONIST,
        Role.ACCOUNTANT,
        Role.PARAMEDIC,
        Role.EMT,
        Role.DRIVER,
        Role.AMBULANCE_DISPATCHER,
        Role.AMBULANCE_COORDINATOR,
        Role.OTHER,
    ],
)
def test_rejects_all_non_patient_roles(
    make_user,
    clinic,
    role,
):
    actor = make_user(
        clinic=clinic,
        role=role,
    )

    with pytest.raises(
        ValidationError,
        match="Patient dashboard is not available",
    ):
        patient_dashboard_service.get_patient_dashboard(
            actor=actor,
        )


def test_accepts_patient_role(
    patient_actor,
):
    actor, patient = patient_actor

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.context.role is Role.PATIENT
    assert result.context.scope == "personal"
    assert result.context.clinic_id == patient.clinic_id


def test_rejects_inactive_patient_account(
    make_user,
    make_patient,
    clinic,
):
    actor = make_user(
        clinic=clinic,
        role=Role.PATIENT,
        is_active=False,
    )

    make_patient(
        clinic=clinic,
        user_id=actor.id,
    )

    with pytest.raises(
        ValidationError,
        match="User account is inactive",
    ):
        patient_dashboard_service.get_patient_dashboard(
            actor=actor,
        )


def test_rejects_patient_without_patient_profile(
    make_user,
    clinic,
):
    actor = make_user(
        clinic=clinic,
        role=Role.PATIENT,
    )

    assert actor.patient is None

    with pytest.raises(
        ConflictError,
        match="Clinical enrollment is pending",
    ) as error:
        patient_dashboard_service.get_patient_dashboard(
            actor=actor,
        )
    assert error.value.status_code == 409
    assert error.value.code == "PATIENT_ENROLLMENT_REQUIRED"
    assert actor.patient is None


def test_empty_dashboard_returns_zero_values(
    patient_actor,
):
    actor, patient = patient_actor

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.upcoming_appointments == 0
    assert result.overview.active_prescriptions == 0
    assert result.overview.active_admissions == 0
    assert result.overview.outstanding_balance == 0
    assert result.overview.unread_notifications == 0

    assert result.metrics


def test_context_is_personal_and_patient_scoped(
    patient_actor,
):
    actor, patient = patient_actor

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.context.role is Role.PATIENT
    assert result.context.scope == "personal"
    assert result.context.clinic_id == patient.clinic_id
    assert result.context.generated_at is not None


def test_counts_upcoming_scheduled_appointment(
    db_session,
    patient_actor,
    make_staff,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    upcoming = _utcnow() + timedelta(hours=2)

    _make_appointment(
        db_session,
        patient.clinic,
        patient,
        staff,
        scheduled_start=upcoming,
        status=AppointmentStatus.SCHEDULED,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.upcoming_appointments == 1


def test_counts_upcoming_confirmed_appointment(
    db_session,
    patient_actor,
    make_staff,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    upcoming = _utcnow() + timedelta(hours=3)

    _make_appointment(
        db_session,
        patient.clinic,
        patient,
        staff,
        scheduled_start=upcoming,
        status=AppointmentStatus.CONFIRMED,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.upcoming_appointments == 1


def test_excludes_past_appointments(
    db_session,
    patient_actor,
    make_staff,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    past = _utcnow() - timedelta(hours=2)

    _make_appointment(
        db_session,
        patient.clinic,
        patient,
        staff,
        scheduled_start=past,
        status=AppointmentStatus.CONFIRMED,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.upcoming_appointments == 0


def test_excludes_cancelled_appointments(
    db_session,
    patient_actor,
    make_staff,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    upcoming = _utcnow() + timedelta(hours=2)

    _make_appointment(
        db_session,
        patient.clinic,
        patient,
        staff,
        scheduled_start=upcoming,
        status=AppointmentStatus.CANCELLED,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.upcoming_appointments == 0


def test_excludes_other_patient_appointments(
    db_session,
    patient_actor,
    make_patient,
    make_staff,
):
    actor, patient = patient_actor

    other_patient = make_patient(
        clinic=patient.clinic,
    )

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    upcoming = _utcnow() + timedelta(hours=2)

    _make_appointment(
        db_session,
        patient.clinic,
        other_patient,
        staff,
        scheduled_start=upcoming,
        status=AppointmentStatus.CONFIRMED,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.upcoming_appointments == 0


def test_counts_active_prescriptions(
    db_session,
    patient_actor,
    make_staff,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    _make_prescription(
        db_session,
        patient.clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.active_prescriptions == 1


@pytest.mark.parametrize(
    "status",
    [
        PrescriptionStatus.COMPLETED,
        PrescriptionStatus.CANCELLED,
        PrescriptionStatus.EXPIRED,
    ],
)
def test_excludes_non_active_prescriptions(
    db_session,
    patient_actor,
    make_staff,
    status,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    _make_prescription(
        db_session,
        patient.clinic,
        patient,
        staff,
        status=status,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.active_prescriptions == 0


def test_excludes_other_patient_prescriptions(
    db_session,
    patient_actor,
    make_patient,
    make_staff,
):
    actor, patient = patient_actor

    other_patient = make_patient(
        clinic=patient.clinic,
    )

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    _make_prescription(
        db_session,
        patient.clinic,
        other_patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.active_prescriptions == 0


def test_counts_active_admission(
    db_session,
    patient_actor,
    make_staff,
    make_ward,
    make_bed,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    _make_admission(
        db_session,
        patient,
        staff,
        make_ward,
        make_bed,
        status=AdmissionStatus.ADMITTED,
        bed_number="PB-001",
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.active_admissions == 1


@pytest.mark.parametrize(
    "status",
    [
        AdmissionStatus.DISCHARGED,
        AdmissionStatus.TRANSFERRED,
    ],
)
def test_excludes_non_active_admissions(
    db_session,
    patient_actor,
    make_staff,
    make_ward,
    make_bed,
    status,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    _make_admission(
        db_session,
        patient,
        staff,
        make_ward,
        make_bed,
        status=status,
        bed_number=f"PB-{status.value}",
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.active_admissions == 0


def test_excludes_other_patient_admissions(
    db_session,
    patient_actor,
    make_patient,
    make_staff,
    make_ward,
    make_bed,
):
    actor, patient = patient_actor

    other_patient = make_patient(
        clinic=patient.clinic,
    )

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    _make_admission(
        db_session,
        other_patient,
        staff,
        make_ward,
        make_bed,
        status=AdmissionStatus.ADMITTED,
        bed_number="PB-OTHER",
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.active_admissions == 0


def test_calculates_outstanding_balance_from_issued_invoice(
    db_session,
    patient_actor,
):
    actor, patient = patient_actor

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number="INV-PAT-001",
        total_amount="1000.00",
        amount_paid="250.00",
        status=InvoiceStatus.ISSUED,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.outstanding_balance == Decimal(
        "750.00"
    )


def test_calculates_outstanding_balance_from_partially_paid_invoice(
    db_session,
    patient_actor,
):
    actor, patient = patient_actor

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number="INV-PAT-002",
        total_amount="2000.00",
        amount_paid="500.00",
        status=InvoiceStatus.PARTIALLY_PAID,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.outstanding_balance == Decimal(
        "1500.00"
    )


def test_calculates_outstanding_balance_from_overdue_invoice(
    db_session,
    patient_actor,
):
    actor, patient = patient_actor

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number="INV-PAT-003",
        total_amount="750.00",
        amount_paid="100.00",
        status=InvoiceStatus.OVERDUE,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.outstanding_balance == Decimal(
        "650.00"
    )


def test_sums_multiple_outstanding_invoices(
    db_session,
    patient_actor,
):
    actor, patient = patient_actor

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number="INV-PAT-004",
        total_amount="1000.00",
        amount_paid="200.00",
        status=InvoiceStatus.ISSUED,
    )

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number="INV-PAT-005",
        total_amount="500.00",
        amount_paid="100.00",
        status=InvoiceStatus.PARTIALLY_PAID,
    )

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number="INV-PAT-006",
        total_amount="300.00",
        amount_paid="0.00",
        status=InvoiceStatus.OVERDUE,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.outstanding_balance == Decimal(
        "1500.00"
    )


@pytest.mark.parametrize(
    "status",
    [
        InvoiceStatus.DRAFT,
        InvoiceStatus.PAID,
        InvoiceStatus.CANCELLED,
    ],
)
def test_excludes_non_outstanding_invoice_statuses(
    db_session,
    patient_actor,
    status,
):
    actor, patient = patient_actor

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number=f"INV-{status.value}",
        total_amount="1000.00",
        amount_paid="0.00",
        status=status,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.outstanding_balance == 0


def test_excludes_fully_paid_outstanding_status_invoice(
    db_session,
    patient_actor,
):
    actor, patient = patient_actor

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number="INV-PAT-FULL",
        total_amount="1000.00",
        amount_paid="1000.00",
        status=InvoiceStatus.ISSUED,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.outstanding_balance == 0


def test_excludes_other_patient_invoices(
    db_session,
    patient_actor,
    make_patient,
):
    actor, patient = patient_actor

    other_patient = make_patient(
        clinic=patient.clinic,
    )

    _make_invoice(
        db_session,
        patient.clinic,
        other_patient,
        invoice_number="INV-OTHER-PATIENT",
        total_amount="1000.00",
        amount_paid="0.00",
        status=InvoiceStatus.OVERDUE,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.outstanding_balance == 0


def test_counts_unread_notifications(
    db_session,
    patient_actor,
    make_notification,
):
    actor, patient = patient_actor

    _make_notification(
        db_session=db_session,
        clinic=patient.clinic,
        user=actor,
        make_notification=make_notification,
        is_read=False,
    )

    _make_notification(
        db_session=db_session,
        clinic=patient.clinic,
        user=actor,
        make_notification=make_notification,
        is_read=False,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.unread_notifications == 2


def test_excludes_read_notifications(
    db_session,
    patient_actor,
    make_notification,
):
    actor, patient = patient_actor

    _make_notification(
        db_session=db_session,
        clinic=patient.clinic,
        user=actor,
        make_notification=make_notification,
        is_read=True,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.unread_notifications == 0


def test_excludes_notifications_for_other_users(
    db_session,
    patient_actor,
    make_user,
    make_notification,
):
    actor, patient = patient_actor

    other_user = make_user(
        clinic=patient.clinic,
        role=Role.PATIENT,
    )

    _make_notification(
        db_session=db_session,
        clinic=patient.clinic,
        user=other_user,
        make_notification=make_notification,
        is_read=False,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.unread_notifications == 0


def test_excludes_notifications_from_other_clinic(
    db_session,
    patient_actor,
    make_clinic,
    make_notification,
):
    actor, patient = patient_actor

    other_clinic = make_clinic(
        name="Other Patient Dashboard Clinic",
    )

    _make_notification(
        db_session=db_session,
        clinic=other_clinic,
        user=actor,
        make_notification=make_notification,
        is_read=False,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.unread_notifications == 0


def test_upcoming_appointment_is_scoped_to_patient_clinic(
    db_session,
    patient_actor,
    make_clinic,
    make_patient,
    make_staff,
):
    actor, patient = patient_actor

    other_clinic = make_clinic(
        name="Other Appointment Clinic",
    )

    other_patient = make_patient(
        clinic=other_clinic,
    )

    other_staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    upcoming = _utcnow() + timedelta(hours=2)

    _make_appointment(
        db_session,
        other_clinic,
        other_patient,
        other_staff,
        scheduled_start=upcoming,
        status=AppointmentStatus.CONFIRMED,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.upcoming_appointments == 0


def test_active_prescription_is_clinic_scoped(
    db_session,
    patient_actor,
    make_clinic,
    make_patient,
    make_staff,
):
    actor, patient = patient_actor

    other_clinic = make_clinic(
        name="Other Prescription Clinic",
    )

    other_patient = make_patient(
        clinic=other_clinic,
    )

    other_staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    _make_prescription(
        db_session,
        other_clinic,
        other_patient,
        other_staff,
        status=PrescriptionStatus.ACTIVE,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.active_prescriptions == 0


def test_outstanding_balance_is_clinic_scoped(
    db_session,
    patient_actor,
    make_clinic,
):
    actor, patient = patient_actor

    other_clinic = make_clinic(
        name="Other Invoice Clinic",
    )

    _make_invoice(
        db_session,
        other_clinic,
        patient,
        invoice_number="INV-OTHER-CLINIC",
        total_amount="5000.00",
        amount_paid="0.00",
        status=InvoiceStatus.OVERDUE,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.outstanding_balance == 0


def test_metrics_have_expected_keys(
    patient_actor,
):
    actor, patient = patient_actor

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert [metric.key for metric in result.metrics] == [
        "upcoming_appointments",
        "active_prescriptions",
        "outstanding_balance",
        "unread_notifications",
    ]


def test_metrics_have_expected_units(
    patient_actor,
):
    actor, patient = patient_actor

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    metrics = {
        metric.key: metric
        for metric in result.metrics
    }

    assert (
        metrics["upcoming_appointments"].unit
        == "appointments"
    )

    assert (
        metrics["active_prescriptions"].unit
        == "prescriptions"
    )

    assert (
        metrics["outstanding_balance"].unit
        == "currency"
    )

    assert (
        metrics["unread_notifications"].unit
        == "notifications"
    )


def test_metrics_reflect_overview_values(
    db_session,
    patient_actor,
    make_staff,
    make_notification,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    upcoming = _utcnow() + timedelta(hours=2)

    _make_appointment(
        db_session,
        patient.clinic,
        patient,
        staff,
        scheduled_start=upcoming,
        status=AppointmentStatus.CONFIRMED,
    )

    _make_prescription(
        db_session,
        patient.clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
    )

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number="INV-METRICS",
        total_amount="1200.00",
        amount_paid="200.00",
        status=InvoiceStatus.ISSUED,
    )

    _make_notification(
        db_session=db_session,
        clinic=patient.clinic,
        user=actor,
        make_notification=make_notification,
        is_read=False,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    metrics = {
        metric.key: metric
        for metric in result.metrics
    }

    assert metrics["upcoming_appointments"].value == 1
    assert metrics["active_prescriptions"].value == 1
    assert (
        metrics["outstanding_balance"].value
        == Decimal("1000.00")
    )
    assert metrics["unread_notifications"].value == 1


def test_multiple_dashboard_categories_can_be_populated_together(
    db_session,
    patient_actor,
    make_staff,
    make_notification,
    make_ward,
    make_bed,
):
    actor, patient = patient_actor

    staff = make_staff(
        clinic=patient.clinic,
        role=Role.DOCTOR,
    )

    upcoming = _utcnow() + timedelta(hours=4)

    _make_appointment(
        db_session,
        patient.clinic,
        patient,
        staff,
        scheduled_start=upcoming,
        status=AppointmentStatus.SCHEDULED,
    )

    _make_prescription(
        db_session,
        patient.clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
    )

    _make_admission(
        db_session,
        patient,
        staff,
        make_ward,
        make_bed,
        status=AdmissionStatus.ADMITTED,
        bed_number="PB-COMBINED",
    )

    _make_invoice(
        db_session,
        patient.clinic,
        patient,
        invoice_number="INV-COMBINED",
        total_amount="2500.00",
        amount_paid="500.00",
        status=InvoiceStatus.PARTIALLY_PAID,
    )

    _make_notification(
        db_session=db_session,
        clinic=patient.clinic,
        user=actor,
        make_notification=make_notification,
        is_read=False,
    )

    _make_notification(
        db_session=db_session,
        clinic=patient.clinic,
        user=actor,
        make_notification=make_notification,
        is_read=False,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert result.overview.upcoming_appointments == 1
    assert result.overview.active_prescriptions == 1
    assert result.overview.active_admissions == 1
    assert (
        result.overview.outstanding_balance
        == Decimal("2000.00")
    )
    assert result.overview.unread_notifications == 2


def test_query_parameter_does_not_break_patient_dashboard(
    patient_actor,
):
    actor, patient = patient_actor

    target_date = date.today() - timedelta(days=10)

    query = DashboardQuerySchema(
        date_from=target_date,
        date_to=target_date,
    )

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
        query=query,
    )

    assert result.context.scope == "personal"
    assert result.context.clinic_id == patient.clinic_id


def test_zero_values_are_returned_as_expected_types(
    patient_actor,
):
    actor, patient = patient_actor

    result = patient_dashboard_service.get_patient_dashboard(
        actor=actor,
    )

    assert isinstance(
        result.overview.upcoming_appointments,
        int,
    )

    assert isinstance(
        result.overview.active_prescriptions,
        int,
    )

    assert isinstance(
        result.overview.active_admissions,
        int,
    )

    assert isinstance(
        result.overview.outstanding_balance,
        Decimal,
    )

    assert isinstance(
        result.overview.unread_notifications,
        int,
    )