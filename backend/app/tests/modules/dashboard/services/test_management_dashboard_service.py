from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.enums.ai_enums import (
    AIFeature,
    AIApprovalStatus,
    AIRiskLevel,
)
from app.core.enums.appointment_enums import (
    AppointmentStatus,
)
from app.core.enums.billing_enums import (
    InvoiceStatus,
)
from app.core.enums.chat_enums import (
    MessagePriority,
    MessageStatus,
    ParticipantStatus,
)
from app.core.enums.lab_enums import (
    LabOrderStatus,
)
from app.core.enums.role_enums import Role
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
from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardQuerySchema,
)
from app.modules.dashboard.services.management_dashboard_service import (
    get_management_dashboard,
)
from app.tests.modules.ward.test_ward_service import (
    _create_admission,
)


# ============================================================================
# TEST HELPERS
# ============================================================================


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _date_at(
    target_date: date,
    hour: int = 12,
) -> datetime:
    return datetime.combine(
        target_date,
        time.min,
        tzinfo=timezone.utc,
    ) + timedelta(hours=hour)


# ============================================================================
# ACCESS CONTROL
# ============================================================================


@pytest.mark.parametrize(
    "role",
    [
        Role.SUPER_ADMIN,
        Role.DOCTOR,
        Role.NURSE,
        Role.PATIENT,
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
def test_get_management_dashboard_rejects_non_admin_roles(
    clinic,
    make_user,
    role,
):
    actor = make_user(
        clinic,
        role=role,
    )

    with pytest.raises(
        ValidationError,
        match="Management dashboard is not available",
    ):
        get_management_dashboard(
            actor=actor,
        )


def test_get_management_dashboard_allows_admin(
    clinic,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.context.role is Role.ADMIN
    assert result.context.scope == "clinic"
    assert result.context.clinic_id == clinic.id


def test_get_management_dashboard_rejects_inactive_admin(
    clinic,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
        is_active=False,
    )

    with pytest.raises(
        ValidationError,
        match="User account is inactive",
    ):
        get_management_dashboard(
            actor=actor,
        )


def test_get_management_dashboard_rejects_admin_without_clinic(
    make_user,
):
    actor = make_user(
        clinic=None,
        role=Role.ADMIN,
    )

    with pytest.raises(
        ValidationError,
        match="valid clinic",
    ):
        get_management_dashboard(
            actor=actor,
        )


def test_get_management_dashboard_rejects_invalid_clinic_id(
    clinic,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    actor.clinic_id = 0

    with pytest.raises(
        ValidationError,
        match="valid clinic",
    ):
        get_management_dashboard(
            actor=actor,
        )


# ============================================================================
# EMPTY DASHBOARD
# ============================================================================


def test_get_management_dashboard_returns_zeroed_dashboard_when_empty(
    clinic,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.total_patients == 0
    assert result.overview.active_patients == 0
    assert result.overview.total_staff == 0
    assert result.overview.active_staff == 0
    assert result.overview.appointments_today == 0
    assert result.overview.missed_appointments_today == 0
    assert result.overview.active_admissions == 0
    assert result.overview.occupied_beds == 0
    assert result.overview.pending_lab_orders == 0

    assert result.ai.overview.total_ai_requests == 0
    assert result.ai.overview.total_credits_used == 0
    assert result.ai.overview.total_tokens == 0
    assert result.ai.overview.estimated_cost == Decimal("0")
    assert result.ai.overview.pending_reviews == 0
    assert result.ai.overview.approved_reviews == 0
    assert result.ai.overview.rejected_reviews == 0
    assert result.ai.overview.high_risk_results == 0
    assert result.ai.overview.critical_risk_results == 0

    assert result.ai.feature_usage == []
    assert result.ai.risk_summary == []
    assert result.ai.approval_summary == []

    assert result.chat.unread_messages == 0
    assert result.chat.unread_conversations == 0
    assert result.chat.mentions == 0
    assert result.chat.priority_messages == 0
    assert result.chat.recent_messages == 0

    assert result.alerts == []
    assert result.recent_activity == []


# ============================================================================
# PATIENT COUNTS
# ============================================================================


def test_get_management_dashboard_counts_total_and_active_patients(
    clinic,
    make_patient,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_patient(
        clinic=clinic,
        is_active=True,
    )

    make_patient(
        clinic=clinic,
        is_active=True,
    )

    make_patient(
        clinic=clinic,
        is_active=False,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.total_patients == 3
    assert result.overview.active_patients == 2


def test_get_management_dashboard_patient_counts_are_clinic_scoped(
    clinic,
    make_clinic,
    make_patient,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_patient(
        clinic=clinic,
    )

    other_clinic = make_clinic()

    make_patient(
        clinic=other_clinic,
    )

    make_patient(
        clinic=other_clinic,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.total_patients == 1
    assert result.overview.active_patients == 1


# ============================================================================
# STAFF COUNTS
# ============================================================================


def test_get_management_dashboard_counts_total_and_active_staff(
    clinic,
    make_staff,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    make_staff(
        clinic=clinic,
        role=Role.NURSE,
        status=StaffStatus.ACTIVE,
    )

    make_staff(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        status=StaffStatus.TERMINATED,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.total_staff == 3
    assert result.overview.active_staff == 2


def test_get_management_dashboard_staff_counts_are_clinic_scoped(
    clinic,
    make_clinic,
    make_staff,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    other_clinic = make_clinic()

    make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    make_staff(
        clinic=other_clinic,
        role=Role.NURSE,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.total_staff == 1
    assert result.overview.active_staff == 1


# ============================================================================
# APPOINTMENTS
# ============================================================================


@pytest.mark.parametrize(
    "status",
    [
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CONFIRMED,
    ],
)
def test_get_management_dashboard_counts_active_appointments_today(
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_appointment,
    status,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    today = _utcnow().date()

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=status,
        scheduled_start=_date_at(today, 9),
        scheduled_end=_date_at(today, 10),
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.appointments_today == 1
    assert result.overview.missed_appointments_today == 0


def test_get_management_dashboard_counts_missed_appointments_today(
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_appointment,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    today = _utcnow().date()

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.NO_SHOW,
        scheduled_start=_date_at(today, 9),
        scheduled_end=_date_at(today, 10),
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.appointments_today == 0
    assert result.overview.missed_appointments_today == 1

    metrics = {
        item.key: item
        for item in result.metrics
    }

    assert metrics["missed_appointments"].value == 1
    assert metrics["missed_appointments"].unit == "appointments"


@pytest.mark.parametrize(
    "status",
    [
        AppointmentStatus.COMPLETED,
        AppointmentStatus.CANCELLED,
    ],
)
def test_get_management_dashboard_excludes_inactive_appointment_statuses(
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_appointment,
    status,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    today = _utcnow().date()

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=status,
        scheduled_start=_date_at(today, 9),
        scheduled_end=_date_at(today, 10),
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.appointments_today == 0
    assert result.overview.missed_appointments_today == 0


def test_get_management_dashboard_excludes_appointments_outside_period(
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_appointment,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    selected_date = _utcnow().date()
    previous_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.SCHEDULED,
        scheduled_start=_date_at(
            previous_date,
            10,
        ),
        scheduled_end=_date_at(
            previous_date,
            11,
        ),
    )

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.CONFIRMED,
        scheduled_start=_date_at(
            selected_date,
            10,
        ),
        scheduled_end=_date_at(
            selected_date,
            11,
        ),
    )

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.NO_SHOW,
        scheduled_start=_date_at(
            selected_date,
            12,
        ),
        scheduled_end=_date_at(
            selected_date,
            13,
        ),
    )

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.SCHEDULED,
        scheduled_start=_date_at(
            next_date,
            10,
        ),
        scheduled_end=_date_at(
            next_date,
            11,
        ),
    )

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.NO_SHOW,
        scheduled_start=_date_at(
            next_date,
            12,
        ),
        scheduled_end=_date_at(
            next_date,
            13,
        ),
    )

    result = get_management_dashboard(
        actor=actor,
        query=DashboardQuerySchema(
            date_from=selected_date,
            date_to=selected_date,
        ),
    )

    assert result.overview.appointments_today == 1
    assert result.overview.missed_appointments_today == 1


def test_get_management_dashboard_missed_appointments_are_clinic_scoped(
    clinic,
    make_clinic,
    make_user,
    make_staff,
    make_patient,
    make_appointment,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    local_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    local_patient = make_patient(
        clinic=clinic,
    )

    other_clinic = make_clinic()

    other_staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    other_patient = make_patient(
        clinic=other_clinic,
    )

    today = _utcnow().date()

    make_appointment(
        clinic=clinic,
        patient=local_patient,
        staff=local_staff,
        status=AppointmentStatus.NO_SHOW,
        scheduled_start=_date_at(today, 9),
        scheduled_end=_date_at(today, 10),
    )

    make_appointment(
        clinic=other_clinic,
        patient=other_patient,
        staff=other_staff,
        status=AppointmentStatus.NO_SHOW,
        scheduled_start=_date_at(today, 10),
        scheduled_end=_date_at(today, 11),
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.missed_appointments_today == 1


def test_get_management_dashboard_counts_multi_day_appointment_period(
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_appointment,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    start_date = _utcnow().date() - timedelta(days=2)
    end_date = _utcnow().date()

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.SCHEDULED,
        scheduled_start=_date_at(
            start_date,
            10,
        ),
        scheduled_end=_date_at(
            start_date,
            11,
        ),
    )

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.CONFIRMED,
        scheduled_start=_date_at(
            end_date,
            15,
        ),
        scheduled_end=_date_at(
            end_date,
            16,
        ),
    )

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.NO_SHOW,
        scheduled_start=_date_at(
            start_date + timedelta(days=1),
            12,
        ),
        scheduled_end=_date_at(
            start_date + timedelta(days=1),
            13,
        ),
    )

    result = get_management_dashboard(
        actor=actor,
        query=DashboardQuerySchema(
            date_from=start_date,
            date_to=end_date,
        ),
    )

    assert result.overview.appointments_today == 2
    assert result.overview.missed_appointments_today == 1


# ============================================================================
# ADMISSIONS / BEDS
# ============================================================================


def test_get_management_dashboard_counts_active_admissions_and_occupied_beds(
    clinic,
    make_user,
    make_patient,
    make_staff,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    (
        ward,
        bed,
        patient,
        admission_staff,
        admission,
    ) = _create_admission(
        clinic,
        make_patient,
        make_staff,
    )

    assert ward.clinic_id == clinic.id
    assert patient.clinic_id == clinic.id
    assert admission_staff.clinic_id == clinic.id

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.active_admissions == 1
    assert result.overview.occupied_beds == 1
    assert admission.status is AdmissionStatus.ADMITTED
    assert bed.status is BedStatus.OCCUPIED


def test_get_management_dashboard_excludes_non_admitted_admissions(
    clinic,
    make_user,
    make_patient,
    make_staff,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    (
        _ward,
        bed,
        patient,
        staff,
        admission,
    ) = _create_admission(
        clinic,
        make_patient,
        make_staff,
    )

    admission.status = AdmissionStatus.DISCHARGED
    bed.status = BedStatus.AVAILABLE

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.active_admissions == 0
    assert result.overview.occupied_beds == 0
    assert patient.clinic_id == clinic.id
    assert staff.clinic_id == clinic.id


def test_get_management_dashboard_admissions_and_beds_are_clinic_scoped(
    clinic,
    make_clinic,
    make_user,
    make_patient,
    make_staff,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    _create_admission(
        clinic,
        make_patient,
        make_staff,
    )

    other_clinic = make_clinic()

    _create_admission(
        other_clinic,
        make_patient,
        make_staff,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.active_admissions == 1
    assert result.overview.occupied_beds == 1


# ============================================================================
# LAB ORDERS
# ============================================================================


@pytest.mark.parametrize(
    "status",
    [
        LabOrderStatus.ORDERED,
        LabOrderStatus.SAMPLE_COLLECTED,
        LabOrderStatus.IN_PROGRESS,
    ],
)
def test_get_management_dashboard_counts_pending_lab_orders(
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_lab_test,
    make_lab_order,
    status,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    lab_test = make_lab_test(
        clinic=clinic,
    )

    make_lab_order(
        clinic=clinic,
        patient=patient,
        staff=staff,
        tests=[lab_test],
        status=status,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.pending_lab_orders == 1


@pytest.mark.parametrize(
    "status",
    [
        LabOrderStatus.COMPLETED,
        LabOrderStatus.CANCELLED,
    ],
)
def test_get_management_dashboard_excludes_non_pending_lab_orders(
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_lab_test,
    make_lab_order,
    status,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    lab_test = make_lab_test(
        clinic=clinic,
    )

    make_lab_order(
        clinic=clinic,
        patient=patient,
        staff=staff,
        tests=[lab_test],
        status=status,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.pending_lab_orders == 0


def test_get_management_dashboard_lab_orders_are_clinic_scoped(
    clinic,
    make_clinic,
    make_user,
    make_staff,
    make_patient,
    make_lab_test,
    make_lab_order,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    lab_test = make_lab_test(
        clinic=clinic,
    )

    make_lab_order(
        clinic=clinic,
        patient=patient,
        staff=staff,
        tests=[lab_test],
        status=LabOrderStatus.ORDERED,
    )

    other_clinic = make_clinic()

    other_staff = make_staff(
        clinic=other_clinic,
        role=Role.DOCTOR,
    )

    other_patient = make_patient(
        clinic=other_clinic,
    )

    other_lab_test = make_lab_test(
        clinic=other_clinic,
    )

    make_lab_order(
        clinic=other_clinic,
        patient=other_patient,
        staff=other_staff,
        tests=[other_lab_test],
        status=LabOrderStatus.ORDERED,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.overview.pending_lab_orders == 1


# ============================================================================
# AI DASHBOARD
# ============================================================================


def test_get_management_dashboard_builds_ai_overview(
    clinic,
    make_user,
    make_ai_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    selected_date = _utcnow().date()

    make_ai_log(
        clinic=clinic,
        user=actor,
        feature_used=AIFeature.DRUG_INTERACTION_CHECK,
        risk_level=AIRiskLevel.HIGH,
        approval_status=AIApprovalStatus.APPROVED,
        credits_used=2,
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        estimated_cost=Decimal("0.020000"),
        created_at=_date_at(selected_date, 10),
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        feature_used=AIFeature.TRIAGE_ASSISTANT,
        risk_level=AIRiskLevel.CRITICAL,
        approval_status=AIApprovalStatus.PENDING,
        credits_used=3,
        input_tokens=200,
        output_tokens=100,
        total_tokens=300,
        estimated_cost=Decimal("0.030000"),
        created_at=_date_at(selected_date, 11),
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        feature_used=AIFeature.LAB_RESULT_INTERPRETER,
        risk_level=AIRiskLevel.LOW,
        approval_status=AIApprovalStatus.REJECTED,
        credits_used=4,
        input_tokens=300,
        output_tokens=150,
        total_tokens=450,
        estimated_cost=Decimal("0.040000"),
        created_at=_date_at(selected_date, 12),
    )

    result = get_management_dashboard(
        actor=actor,
        query=DashboardQuerySchema(
            date_from=selected_date,
            date_to=selected_date,
        ),
    )

    overview = result.ai.overview

    assert overview.total_ai_requests == 3
    assert overview.total_credits_used == 9
    assert overview.total_tokens == 900
    assert overview.estimated_cost == Decimal("0.090000")
    assert overview.pending_reviews == 1
    assert overview.approved_reviews == 1
    assert overview.rejected_reviews == 1
    assert overview.high_risk_results == 1
    assert overview.critical_risk_results == 1


def test_get_management_dashboard_builds_ai_feature_summary(
    clinic,
    make_user,
    make_ai_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        feature_used=AIFeature.DRUG_INTERACTION_CHECK,
        credits_used=2,
        estimated_cost=Decimal("0.020000"),
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        feature_used=AIFeature.DRUG_INTERACTION_CHECK,
        credits_used=3,
        estimated_cost=Decimal("0.030000"),
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        feature_used=AIFeature.TRIAGE_ASSISTANT,
        credits_used=5,
        estimated_cost=Decimal("0.050000"),
    )

    result = get_management_dashboard(
        actor=actor,
    )

    usage = {
        item.feature: item
        for item in result.ai.feature_usage
    }

    assert usage[
        AIFeature.DRUG_INTERACTION_CHECK
    ].request_count == 2

    assert usage[
        AIFeature.DRUG_INTERACTION_CHECK
    ].credits_used == 5

    assert usage[
        AIFeature.DRUG_INTERACTION_CHECK
    ].estimated_cost == Decimal("0.050000")

    assert usage[
        AIFeature.TRIAGE_ASSISTANT
    ].request_count == 1

    assert usage[
        AIFeature.TRIAGE_ASSISTANT
    ].credits_used == 5

    assert usage[
        AIFeature.TRIAGE_ASSISTANT
    ].estimated_cost == Decimal("0.050000")


def test_get_management_dashboard_builds_ai_risk_summary(
    clinic,
    make_user,
    make_ai_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    for risk_level in (
        AIRiskLevel.LOW,
        AIRiskLevel.MEDIUM,
        AIRiskLevel.HIGH,
        AIRiskLevel.CRITICAL,
    ):
        make_ai_log(
            clinic=clinic,
            user=actor,
            risk_level=risk_level,
        )

    result = get_management_dashboard(
        actor=actor,
    )

    summary = {
        item.risk_level: item.count
        for item in result.ai.risk_summary
    }

    assert summary == {
        AIRiskLevel.LOW: 1,
        AIRiskLevel.MEDIUM: 1,
        AIRiskLevel.HIGH: 1,
        AIRiskLevel.CRITICAL: 1,
    }


def test_get_management_dashboard_builds_ai_approval_summary(
    clinic,
    make_user,
    make_ai_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    for approval_status in (
        AIApprovalStatus.PENDING,
        AIApprovalStatus.APPROVED,
        AIApprovalStatus.REJECTED,
    ):
        make_ai_log(
            clinic=clinic,
            user=actor,
            approval_status=approval_status,
        )

    result = get_management_dashboard(
        actor=actor,
    )

    summary = {
        item.approval_status: item.count
        for item in result.ai.approval_summary
    }

    assert summary == {
        AIApprovalStatus.PENDING: 1,
        AIApprovalStatus.APPROVED: 1,
        AIApprovalStatus.REJECTED: 1,
    }


def test_get_management_dashboard_excludes_ai_logs_outside_period(
    clinic,
    make_user,
    make_ai_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    selected_date = _utcnow().date()
    previous_date = selected_date - timedelta(days=1)

    make_ai_log(
        clinic=clinic,
        user=actor,
        credits_used=2,
        estimated_cost=Decimal("0.020000"),
        created_at=_date_at(selected_date, 10),
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        credits_used=10,
        estimated_cost=Decimal("0.100000"),
        created_at=_date_at(previous_date, 10),
    )

    result = get_management_dashboard(
        actor=actor,
        query=DashboardQuerySchema(
            date_from=selected_date,
            date_to=selected_date,
        ),
    )

    assert result.ai.overview.total_ai_requests == 1
    assert result.ai.overview.total_credits_used == 2
    assert (
        result.ai.overview.estimated_cost
        == Decimal("0.020000")
    )


def test_get_management_dashboard_ai_data_is_clinic_scoped(
    clinic,
    make_clinic,
    make_user,
    make_ai_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    other_clinic = make_clinic()

    other_actor = make_user(
        other_clinic,
        role=Role.ADMIN,
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        credits_used=2,
    )

    make_ai_log(
        clinic=other_clinic,
        user=other_actor,
        credits_used=20,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.ai.overview.total_ai_requests == 1
    assert result.ai.overview.total_credits_used == 2


# ============================================================================
# INVENTORY / LOW STOCK
# ============================================================================


def test_get_management_dashboard_counts_low_stock_items(
    clinic,
    make_user,
    make_inventory_item,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_inventory_item(
        clinic=clinic,
        quantity_on_hand=10,
        reorder_level=10,
        is_active=True,
    )

    make_inventory_item(
        clinic=clinic,
        quantity_on_hand=2,
        reorder_level=10,
        is_active=True,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.metrics[2].key == "low_stock_items"
    assert result.metrics[2].value == 2


def test_get_management_dashboard_excludes_inactive_low_stock_items(
    clinic,
    make_user,
    make_inventory_item,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_inventory_item(
        clinic=clinic,
        quantity_on_hand=0,
        reorder_level=10,
        is_active=False,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.metrics[2].value == 0


def test_get_management_dashboard_low_stock_is_clinic_scoped(
    clinic,
    make_clinic,
    make_user,
    make_inventory_item,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_inventory_item(
        clinic=clinic,
        quantity_on_hand=2,
        reorder_level=10,
        is_active=True,
    )

    other_clinic = make_clinic()

    make_inventory_item(
        clinic=other_clinic,
        quantity_on_hand=1,
        reorder_level=10,
        is_active=True,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.metrics[2].value == 1


# ============================================================================
# OVERDUE INVOICES
# ============================================================================


def test_get_management_dashboard_counts_overdue_invoices(
    db_session,
    clinic,
    patient,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    db_session.add_all(
        [
            Invoice(
                clinic_id=clinic.id,
                patient_id=patient.id,
                invoice_number="MGMT-OVERDUE-001",
                total_amount=Decimal("100.00"),
                amount_paid=Decimal("0.00"),
                status=InvoiceStatus.OVERDUE,
            ),
            Invoice(
                clinic_id=clinic.id,
                patient_id=patient.id,
                invoice_number="MGMT-OVERDUE-002",
                total_amount=Decimal("200.00"),
                amount_paid=Decimal("50.00"),
                status=InvoiceStatus.OVERDUE,
            ),
            Invoice(
                clinic_id=clinic.id,
                patient_id=patient.id,
                invoice_number="MGMT-OVERDUE-003",
                total_amount=Decimal("50.00"),
                amount_paid=Decimal("0.00"),
                status=InvoiceStatus.OVERDUE,
            ),
        ]
    )

    db_session.flush()

    result = get_management_dashboard(
        actor=actor,
    )

    overdue_alert = next(
        item
        for item in result.alerts
        if item.key == "overdue_invoices"
    )

    assert overdue_alert.count == 3


def test_get_management_dashboard_excludes_non_overdue_invoices(
    clinic,
    patient,
    make_user,
    db_session,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    db_session.add(
        Invoice(
            clinic_id=clinic.id,
            patient_id=patient.id,
            invoice_number="MGMT-ISSUED-001",
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
            status=InvoiceStatus.ISSUED,
        )
    )

    db_session.flush()

    result = get_management_dashboard(
        actor=actor,
    )

    assert not any(
        item.key == "overdue_invoices"
        for item in result.alerts
    )


def test_get_management_dashboard_overdue_invoices_are_clinic_scoped(
    clinic,
    make_clinic,
    make_patient,
    make_user,
    db_session,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    other_clinic = make_clinic()

    other_patient = make_patient(
        other_clinic,
    )

    db_session.add(
        Invoice(
            clinic_id=other_clinic.id,
            patient_id=other_patient.id,
            invoice_number="MGMT-OTHER-OVERDUE-001",
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
            status=InvoiceStatus.OVERDUE,
        )
    )

    db_session.flush()

    result = get_management_dashboard(
        actor=actor,
    )

    assert not any(
        item.key == "overdue_invoices"
        for item in result.alerts
    )


# ============================================================================
# CHAT
# ============================================================================


def test_get_management_dashboard_includes_chat_summary(
    clinic,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.chat.unread_messages == 0
    assert result.chat.unread_conversations == 0
    assert result.chat.mentions == 0
    assert result.chat.priority_messages == 0
    assert result.chat.recent_messages == 0


def test_get_management_dashboard_creates_priority_chat_alert(
    clinic,
    make_user,
    make_conversation,
    make_conversation_participant,
    make_message,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    sender = make_user(
        clinic,
        role=Role.DOCTOR,
    )

    conversation = make_conversation(
        clinic=clinic,
        created_by=actor,
    )

    make_conversation_participant(
        clinic=clinic,
        conversation=conversation,
        user=actor,
        status=ParticipantStatus.ACCEPTED,
    )

    make_conversation_participant(
        clinic=clinic,
        conversation=conversation,
        user=sender,
        status=ParticipantStatus.ACCEPTED,
    )

    make_message(
        clinic=clinic,
        conversation=conversation,
        sender=sender,
        status=MessageStatus.SENT,
        priority=MessagePriority.URGENT,
        created_at=_date_at(_utcnow().date(), 10),
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.chat.priority_messages == 1

    alert = next(
        item
        for item in result.alerts
        if item.key == "priority_chat_messages"
    )

    assert alert.severity == "critical"
    assert alert.count == 1


# ============================================================================
# ALERTS
# ============================================================================


def test_get_management_dashboard_creates_low_stock_alert(
    clinic,
    make_user,
    make_inventory_item,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_inventory_item(
        clinic=clinic,
        quantity_on_hand=5,
        reorder_level=10,
        is_active=True,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    alert = next(
        item
        for item in result.alerts
        if item.key == "low_stock"
    )

    assert alert.severity == "warning"
    assert alert.title == "Low stock items"
    assert alert.count == 1


def test_get_management_dashboard_creates_critical_ai_alert(
    clinic,
    make_user,
    make_ai_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        risk_level=AIRiskLevel.CRITICAL,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    alert = next(
        item
        for item in result.alerts
        if item.key == "critical_ai_results"
    )

    assert alert.severity == "critical"
    assert alert.title == "Critical AI results"
    assert alert.count == 1


def test_get_management_dashboard_creates_pending_ai_review_alert(
    clinic,
    make_user,
    make_ai_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        approval_status=AIApprovalStatus.PENDING,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    alert = next(
        item
        for item in result.alerts
        if item.key == "pending_ai_reviews"
    )

    assert alert.severity == "warning"
    assert alert.title == "Pending AI reviews"
    assert alert.count == 1


def test_get_management_dashboard_does_not_create_alerts_without_conditions(
    clinic,
    make_user,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.alerts == []


# ============================================================================
# METRICS
# ============================================================================


def test_get_management_dashboard_builds_expected_metrics(
    clinic,
    make_user,
    make_patient,
    make_staff,
    make_inventory_item,
    make_ai_log,
    make_appointment,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    patient = make_patient(
        clinic=clinic,
    )

    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    make_inventory_item(
        clinic=clinic,
        quantity_on_hand=2,
        reorder_level=10,
        is_active=True,
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
    )

    make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        status=AppointmentStatus.NO_SHOW,
        scheduled_start=_date_at(
            _utcnow().date(),
            10,
        ),
        scheduled_end=_date_at(
            _utcnow().date(),
            11,
        ),
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert [
        metric.key
        for metric in result.metrics
    ] == [
        "active_patients",
        "active_staff",
        "low_stock_items",
        "ai_requests",
        "missed_appointments",
        "unread_chat_messages",
        "hospital_available_beds",
        "urgent_nursing_tasks",
        "emar_due",
        "draft_discharge_reconciliations",
    ]

    assert result.metrics[0].value == 1
    assert result.metrics[0].unit == "patients"

    assert result.metrics[1].value == 1
    assert result.metrics[1].unit == "staff"

    assert result.metrics[2].value == 1
    assert result.metrics[2].unit == "items"

    assert result.metrics[3].value == 1
    assert result.metrics[3].unit == "requests"

    assert result.metrics[4].value == 1
    assert result.metrics[4].unit == "appointments"

    assert result.metrics[5].value == 0
    assert result.metrics[5].unit == "messages"


# ============================================================================
# RECENT ACTIVITY
# ============================================================================


def test_get_management_dashboard_includes_recent_activity(
    clinic,
    make_user,
    make_audit_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    log = make_audit_log(
        user=actor,
        entity_type="Invoice",
        entity_id=123,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert len(result.recent_activity) == 1
    assert result.recent_activity[0].entity_type == "Invoice"
    assert result.recent_activity[0].entity_id == 123
    assert (
        result.recent_activity[0].action
        == log.action.value
    )


def test_get_management_dashboard_excludes_other_clinic_activity(
    clinic,
    make_clinic,
    make_user,
    make_audit_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    other_clinic = make_clinic()

    other_actor = make_user(
        other_clinic,
        role=Role.ADMIN,
    )

    make_audit_log(
        user=actor,
        entity_type="Invoice",
        entity_id=100,
    )

    make_audit_log(
        user=other_actor,
        entity_type="Invoice",
        entity_id=200,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert len(result.recent_activity) == 1
    assert result.recent_activity[0].entity_id == 100


# ============================================================================
# COMPLETE INTEGRATED SUMMARY
# ============================================================================


def test_get_management_dashboard_builds_complete_summary(
    db_session,
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_inventory_item,
    make_lab_test,
    make_lab_order,
    make_ai_log,
    make_conversation,
    make_conversation_participant,
    make_message,
    make_audit_log,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    doctor = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )

    nurse = make_staff(
        clinic=clinic,
        role=Role.NURSE,
        status=StaffStatus.ACTIVE,
    )

    manager_patient = make_patient(
        clinic=clinic,
        is_active=True,
    )

    another_patient = make_patient(
        clinic=clinic,
        is_active=True,
    )

    inactive_patient = make_patient(
        clinic=clinic,
        is_active=False,
    )

    today = _utcnow().date()

    db_session.add(
        Appointment(
            clinic_id=clinic.id,
            patient_id=manager_patient.id,
            staff_id=doctor.id,
            scheduled_start=_date_at(today, 9),
            scheduled_end=_date_at(today, 10),
            status=AppointmentStatus.CONFIRMED,
        )
    )

    db_session.flush()

    db_session.add(
        Appointment(
            clinic_id=clinic.id,
            patient_id=another_patient.id,
            staff_id=doctor.id,
            scheduled_start=_date_at(today, 12),
            scheduled_end=_date_at(today, 13),
            status=AppointmentStatus.NO_SHOW,
        )
    )

    db_session.flush()

    lab_test = make_lab_test(
        clinic=clinic,
    )

    make_lab_order(
        clinic=clinic,
        patient=another_patient,
        staff=doctor,
        tests=[lab_test],
        status=LabOrderStatus.IN_PROGRESS,
    )

    _create_admission(
        clinic,
        make_patient,
        make_staff,
        actor_staff=doctor,
        patient=manager_patient,
    )

    make_inventory_item(
        clinic=clinic,
        quantity_on_hand=2,
        reorder_level=10,
        is_active=True,
    )

    make_ai_log(
        clinic=clinic,
        user=actor,
        feature_used=AIFeature.TRIAGE_ASSISTANT,
        risk_level=AIRiskLevel.CRITICAL,
        approval_status=AIApprovalStatus.PENDING,
        credits_used=3,
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        estimated_cost=Decimal("0.030000"),
        created_at=_date_at(today, 10),
    )

    sender = make_user(
        clinic,
        role=Role.DOCTOR,
    )

    conversation = make_conversation(
        clinic=clinic,
        created_by=actor,
    )

    make_conversation_participant(
        clinic=clinic,
        conversation=conversation,
        user=actor,
        status=ParticipantStatus.ACCEPTED,
    )

    make_conversation_participant(
        clinic=clinic,
        conversation=conversation,
        user=sender,
        status=ParticipantStatus.ACCEPTED,
    )

    make_message(
        clinic=clinic,
        conversation=conversation,
        sender=sender,
        status=MessageStatus.SENT,
        priority=MessagePriority.URGENT,
        created_at=_date_at(today, 11),
    )

    make_audit_log(
        user=actor,
        entity_type="Patient",
        entity_id=inactive_patient.id,
    )

    result = get_management_dashboard(
        actor=actor,
    )

    assert result.context.role is Role.ADMIN
    assert result.context.scope == "clinic"
    assert result.context.clinic_id == clinic.id

    assert result.overview.total_patients == 3
    assert result.overview.active_patients == 2
    assert result.overview.total_staff == 2
    assert result.overview.active_staff == 2
    assert result.overview.appointments_today == 1
    assert result.overview.missed_appointments_today == 1
    assert result.overview.active_admissions == 1
    assert result.overview.occupied_beds == 1
    assert result.overview.pending_lab_orders == 1

    assert result.ai.overview.total_ai_requests == 1
    assert result.ai.overview.total_credits_used == 3
    assert result.ai.overview.total_tokens == 150
    assert (
        result.ai.overview.estimated_cost
        == Decimal("0.030000")
    )
    assert result.ai.overview.pending_reviews == 1
    assert result.ai.overview.approved_reviews == 0
    assert result.ai.overview.rejected_reviews == 0
    assert result.ai.overview.high_risk_results == 0
    assert result.ai.overview.critical_risk_results == 1

    assert result.chat.priority_messages == 1

    assert [
        metric.key
        for metric in result.metrics
    ] == [
        "active_patients",
        "active_staff",
        "low_stock_items",
        "ai_requests",
        "missed_appointments",
        "unread_chat_messages",
        "hospital_available_beds",
        "urgent_nursing_tasks",
        "emar_due",
        "draft_discharge_reconciliations",
    ]

    metrics = {
        item.key: item
        for item in result.metrics
    }

    assert metrics["missed_appointments"].value == 1
    assert metrics["missed_appointments"].unit == "appointments"

    assert {
        alert.key
        for alert in result.alerts
    } == {
        "low_stock",
        "critical_ai_results",
        "pending_ai_reviews",
        "priority_chat_messages",
        "hospital_bed_capacity",
    }

    assert any(
        item.entity_type == "Patient"
        and item.entity_id == inactive_patient.id
        for item in result.recent_activity
    )
