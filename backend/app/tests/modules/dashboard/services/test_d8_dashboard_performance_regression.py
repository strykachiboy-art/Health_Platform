from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskPriority,
    NursingCareTaskStatus,
)
from app.core.enums.prescription_enums import (
    MedicationOrderPriority,
    MedicationOrderType,
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus, BedStatus
from app.extensions import db
from app.modules.dashboard.services import clinical_dashboard_service
from app.modules.dashboard.services import management_dashboard_service
from app.modules.dashboard.services import operations_dashboard_service
from app.modules.dashboard.services.hospital_dashboard_widget_service import (
    build_hospital_read_model,
    build_patient_order_aggregates,
)
from app.modules.nursing.models.nursing_model import (
    NursingCarePlan,
    NursingCareTask,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.ward.models.ward_model import Admission


FULL_HOSPITAL_QUERY_BUDGET = 22
PHARMACIST_QUERY_BUDGET = 5
RECEPTIONIST_QUERY_BUDGET = 4
PATIENT_ORDER_QUERY_BUDGET = 8

WARMUPS = 3
RUNS = 20

# Deliberately generous local-test ceiling. Query-count constancy is the
# primary N+1 regression guard; this ceiling catches catastrophic slowdowns
# without making the test sensitive to normal workstation variance.
FULL_HOSPITAL_P95_LIMIT_MS = 500.0


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower

    return (
        ordered[lower]
        + (
            ordered[upper] - ordered[lower]
        )
        * fraction
    )


def _measure_call(function):
    query_count = 0

    def before_cursor_execute(
        conn,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        nonlocal query_count
        query_count += 1

    event.listen(
        db.engine,
        "before_cursor_execute",
        before_cursor_execute,
    )

    started = time.perf_counter()

    try:
        result = function()
    finally:
        elapsed_ms = (
            time.perf_counter() - started
        ) * 1000.0

        event.remove(
            db.engine,
            "before_cursor_execute",
            before_cursor_execute,
        )

    return result, query_count, elapsed_ms


def _create_admission(
    *,
    clinic,
    patient,
    staff,
    make_ward,
    make_bed,
    db_session,
):
    ward = make_ward(clinic)

    occupied_bed = make_bed(
        ward,
        status=BedStatus.OCCUPIED,
    )

    admission = Admission(
        patient_id=patient.id,
        bed_id=occupied_bed.id,
        admitted_by_id=staff.id,
        status=AdmissionStatus.ADMITTED,
    )

    db_session.add(admission)
    db_session.flush()

    return admission


def _create_care_plan(
    *,
    clinic,
    patient,
    admission,
    staff,
    db_session,
):
    care_plan = NursingCarePlan(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        created_by_staff_id=staff.id,
        title="D8 performance plan",
        problem="Performance seed",
        goal="Stable query budget",
        status=NursingCarePlanStatus.ACTIVE,
    )

    db_session.add(care_plan)
    db_session.flush()

    return care_plan


def _add_tasks(
    *,
    count,
    care_plan,
    clinic,
    patient,
    admission,
    staff,
    db_session,
):
    now = datetime(
        2026,
        10,
        31,
        10,
        0,
        0,
    )

    for index in range(count):
        db_session.add(
            NursingCareTask(
                care_plan_id=care_plan.id,
                clinic_id=clinic.id,
                patient_id=patient.id,
                admission_id=admission.id,
                created_by_staff_id=staff.id,
                assigned_to_staff_id=staff.id,
                description=f"D8 task {index}",
                priority=(
                    NursingCareTaskPriority.URGENT
                    if index % 5 == 0
                    else NursingCareTaskPriority.ROUTINE
                ),
                status=(
                    NursingCareTaskStatus.PENDING
                    if index % 2 == 0
                    else NursingCareTaskStatus.IN_PROGRESS
                ),
                due_at=(
                    now - timedelta(minutes=5)
                    if index % 3 == 0
                    else now + timedelta(hours=1)
                ),
            )
        )

    db_session.flush()


def _create_inpatient_medication_order(
    *,
    clinic,
    patient,
    staff,
    drug,
    db_session,
):
    prescription = Prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        status=PrescriptionStatus.ACTIVE,
    )

    db_session.add(prescription)
    db_session.flush()

    item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dose_value=500,
        dose_unit="mg",
        route_code="oral",
        order_priority=MedicationOrderPriority.ROUTINE,
        order_type=MedicationOrderType.INPATIENT,
        ordered_at=datetime.now(timezone.utc),
    )

    db_session.add(item)
    db_session.flush()

    return prescription, item


def test_full_hospital_query_budget_is_constant_as_rows_grow(
    clinic,
    make_staff,
    make_patient,
    make_ward,
    make_bed,
    db_session,
):
    nurse = make_staff(
        clinic,
        role=Role.NURSE,
    )
    patient = make_patient(clinic)

    admission = _create_admission(
        clinic=clinic,
        patient=patient,
        staff=nurse,
        make_ward=make_ward,
        make_bed=make_bed,
        db_session=db_session,
    )

    care_plan = _create_care_plan(
        clinic=clinic,
        patient=patient,
        admission=admission,
        staff=nurse,
        db_session=db_session,
    )

    _add_tasks(
        count=1,
        care_plan=care_plan,
        clinic=clinic,
        patient=patient,
        admission=admission,
        staff=nurse,
        db_session=db_session,
    )

    _, small_queries, _ = _measure_call(
        lambda: build_hospital_read_model(
            clinic_id=clinic.id,
        )
    )

    _add_tasks(
        count=50,
        care_plan=care_plan,
        clinic=clinic,
        patient=patient,
        admission=admission,
        staff=nurse,
        db_session=db_session,
    )

    _, large_queries, _ = _measure_call(
        lambda: build_hospital_read_model(
            clinic_id=clinic.id,
        )
    )

    assert small_queries == FULL_HOSPITAL_QUERY_BUDGET
    assert large_queries == FULL_HOSPITAL_QUERY_BUDGET


def test_role_integrations_have_bounded_hospital_query_budgets(
    clinic,
):
    role_expectations = [
        (
            "doctor",
            lambda: clinical_dashboard_service._build_hospital_dashboard(
                role=Role.DOCTOR,
                clinic_id=clinic.id,
            ),
            FULL_HOSPITAL_QUERY_BUDGET,
        ),
        (
            "nurse",
            lambda: clinical_dashboard_service._build_hospital_dashboard(
                role=Role.NURSE,
                clinic_id=clinic.id,
            ),
            FULL_HOSPITAL_QUERY_BUDGET,
        ),
        (
            "pharmacist",
            lambda: clinical_dashboard_service._build_hospital_dashboard(
                role=Role.PHARMACIST,
                clinic_id=clinic.id,
            ),
            PHARMACIST_QUERY_BUDGET,
        ),
        (
            "lab_technician",
            lambda: clinical_dashboard_service._build_hospital_dashboard(
                role=Role.LAB_TECHNICIAN,
                clinic_id=clinic.id,
            ),
            0,
        ),
        (
            "management_admin",
            lambda: (
                management_dashboard_service
                ._build_management_hospital_operations(
                    clinic_id=clinic.id,
                )
            ),
            FULL_HOSPITAL_QUERY_BUDGET,
        ),
        (
            "receptionist",
            lambda: (
                operations_dashboard_service
                ._build_operations_hospital_capacity(
                    role=Role.RECEPTIONIST,
                    clinic_id=clinic.id,
                )
            ),
            RECEPTIONIST_QUERY_BUDGET,
        ),
        (
            "driver",
            lambda: (
                operations_dashboard_service
                ._build_operations_hospital_capacity(
                    role=Role.DRIVER,
                    clinic_id=clinic.id,
                )
            ),
            0,
        ),
    ]

    observed = {}

    for label, function, expected_queries in role_expectations:
        _, queries, elapsed_ms = _measure_call(function)

        observed[label] = {
            "queries": queries,
            "elapsed_ms": elapsed_ms,
        }

        assert queries == expected_queries

    print(
        "D8_ROLE_QUERY_BUDGETS="
        + ",".join(
            f"{label}:{data['queries']}"
            for label, data in observed.items()
        )
    )


def test_full_hospital_warm_p95_is_bounded(
    clinic,
):
    for _ in range(WARMUPS):
        build_hospital_read_model(
            clinic_id=clinic.id,
        )

    timings = []
    query_counts = []

    for _ in range(RUNS):
        _, queries, elapsed_ms = _measure_call(
            lambda: build_hospital_read_model(
                clinic_id=clinic.id,
            )
        )

        timings.append(elapsed_ms)
        query_counts.append(queries)

    average = statistics.mean(timings)
    median = statistics.median(timings)
    p95 = _percentile(
        timings,
        0.95,
    )
    p99 = _percentile(
        timings,
        0.99,
    )

    print(
        "D8_HOSPITAL_PERF="
        f"runs:{RUNS},"
        f"avg_ms:{average:.3f},"
        f"median_ms:{median:.3f},"
        f"p95_ms:{p95:.3f},"
        f"p99_ms:{p99:.3f},"
        f"fastest_ms:{min(timings):.3f},"
        f"slowest_ms:{max(timings):.3f},"
        f"queries:{query_counts[0]}"
    )

    assert set(query_counts) == {
        FULL_HOSPITAL_QUERY_BUDGET,
    }
    assert p95 <= FULL_HOSPITAL_P95_LIMIT_MS


def test_patient_order_query_budget_is_constant_as_orders_grow(
    clinic,
    make_staff,
    make_patient,
    make_drug,
    db_session,
):
    doctor = make_staff(
        clinic,
        role=Role.DOCTOR,
    )
    patient = make_patient(clinic)
    drug = make_drug(
        clinic,
        is_active=True,
    )

    _create_inpatient_medication_order(
        clinic=clinic,
        patient=patient,
        staff=doctor,
        drug=drug,
        db_session=db_session,
    )

    small, small_queries, _ = _measure_call(
        lambda: build_patient_order_aggregates(
            clinic_id=clinic.id,
            patient_id=patient.id,
        )
    )

    for _ in range(20):
        _create_inpatient_medication_order(
            clinic=clinic,
            patient=patient,
            staff=doctor,
            drug=drug,
            db_session=db_session,
        )

    large, large_queries, _ = _measure_call(
        lambda: build_patient_order_aggregates(
            clinic_id=clinic.id,
            patient_id=patient.id,
        )
    )

    assert small["total"] == 1
    assert large["total"] == 21

    assert small_queries == PATIENT_ORDER_QUERY_BUDGET
    assert large_queries == PATIENT_ORDER_QUERY_BUDGET
