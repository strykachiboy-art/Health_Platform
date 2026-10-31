from __future__ import annotations

import os
import time
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app import create_app
from app.config import config_by_name
from app.core.auth.user.models.user_model import User
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.clinical_record_enums import ClinicalRecordStatus
from app.core.enums.medication_reconciliation_enums import (
    MedicationReconciliationEvent,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import (
    AdmissionStatus,
    BedStatus,
    ReservationStatus,
)
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.medication_reconciliation.models.medication_reconciliation_model import (
    MedicationReconciliation,
)
from app.modules.medication_reconciliation.services import (
    medication_reconciliation_service as med_recon_service,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.prescription.models.prescription_model import (
    Prescription,
)
from app.modules.prescription.services import prescription_service
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.discharge_summary_model import (
    DischargeSummary,
)
from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    BedReservation,
    Ward,
    WardTransfer,
)
from app.modules.ward.services import (
    discharge_service,
    ward_service,
)


def _database_url() -> str:
    value = os.getenv(
        "CONCURRENCY_TEST_DATABASE_URL"
    )

    if not value:
        pytest.fail(
            "CONCURRENCY_TEST_DATABASE_URL is required"
        )

    return value


def _build_app():
    from app import extensions

    original_database_url = (
        config_by_name[
            "testing"
        ].SQLALCHEMY_DATABASE_URI
    )
    original_redis_client = (
        extensions.redis_client
    )

    config_by_name[
        "testing"
    ].SQLALCHEMY_DATABASE_URI = (
        _database_url()
    )

    app = create_app(
        "testing"
    )

    extensions.redis_client = (
        original_redis_client
    )

    return (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    )


def _reset_schema(app) -> None:
    with app.app_context():
        from app import models_registry  # noqa: F401

        db.session.rollback()
        db.drop_all()
        db.create_all()
        db.session.commit()


def _cleanup(
    app,
    extensions,
    original_database_url,
    original_redis_client,
) -> None:
    with app.app_context():
        db.session.rollback()

        try:
            db.drop_all()
        finally:
            db.session.remove()

        try:
            db.engine.dispose()
        except Exception:
            pass

    config_by_name[
        "testing"
    ].SQLALCHEMY_DATABASE_URI = (
        original_database_url
    )
    extensions.redis_client = (
        original_redis_client
    )


def _add_staff(
    *,
    clinic: Clinic,
    role: Role,
    email: str,
    first_name: str,
) -> Staff:
    user = User(
        clinic_id=clinic.id,
        role=role,
        is_active=True,
        email=email,
    )
    user.set_password(
        "supersecret"
    )

    db.session.add(
        user
    )
    db.session.flush()

    staff = Staff(
        clinic_id=clinic.id,
        user_id=user.id,
        first_name=first_name,
        last_name="Gate19",
        status=StaffStatus.ACTIVE,
    )

    db.session.add(
        staff
    )
    db.session.flush()

    return staff


def _add_patient(
    *,
    clinic: Clinic,
    suffix: str,
) -> Patient:
    patient = Patient(
        clinic_id=clinic.id,
        first_name="Race",
        last_name=suffix,
        patient_number=f"G19-{suffix}",
        is_active=True,
    )

    db.session.add(
        patient
    )
    db.session.flush()

    return patient


def _assert_distinct_connections(
    results,
) -> None:
    assert len(results) == 2

    assert all(
        result.error is None
        for result in results
    )

    assert len(
        {
            result.backend_pid
            for result in results
        }
    ) == 2


def _harness():
    from app.tests.core.concurrency.concurrency_harness import (
        PostgresConcurrencyHarness,
    )

    return PostgresConcurrencyHarness(
        _database_url()
    )


def test_two_doctor_workers_create_one_idempotent_medication_order(
    monkeypatch,
):
    """
    Idempotency keys are intentionally user-scoped.

    This therefore models two concurrent doctor workers acting on the
    same authenticated doctor's request identity, which is the actual
    duplicate-request race that an idempotency key is designed to
    collapse.
    """
    (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    ) = _build_app()

    harness = None

    try:
        _reset_schema(
            app
        )

        monkeypatch.setattr(
            prescription_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        with app.app_context():
            clinic = Clinic(
                name="Gate 19 Rx Clinic",
                status=ClinicStatus.ACTIVE,
                ai_credits=5,
            )
            db.session.add(
                clinic
            )
            db.session.flush()

            doctor = _add_staff(
                clinic=clinic,
                role=Role.DOCTOR,
                email="gate19-rx-doctor@test.com",
                first_name="Doctor",
            )
            patient = _add_patient(
                clinic=clinic,
                suffix="RX",
            )
            drug = Drug(
                clinic_id=clinic.id,
                name="Gate 19 Medication",
                is_active=True,
            )
            db.session.add(
                drug
            )
            db.session.commit()

            clinic_id = clinic.id
            doctor_id = doctor.id
            doctor_user_id = doctor.user_id
            patient_id = patient.id
            drug_id = drug.id

        harness = _harness()

        items = [
            {
                "drug_id": drug_id,
                "dosage": "500 mg",
                "frequency": "twice daily",
                "duration": "5 days",
                "quantity": 10,
                "instructions": "After meals",
            }
        ]

        def create_order(worker_index):
            prescription, _warnings = (
                prescription_service.create_prescription(
                    clinic_id=clinic_id,
                    patient_id=patient_id,
                    prescribed_by_id=doctor_id,
                    items=items,
                    notes="Gate 19 idempotent order",
                    idempotency_key=(
                        "gate19-rx-same-key"
                    ),
                    idempotency_user_id=(
                        doctor_user_id
                    ),
                )
            )

            return {
                "worker_index": worker_index,
                "prescription_id": prescription.id,
            }

        results = harness.run_flask(
            app,
            create_order,
        )

        _assert_distinct_connections(
            results
        )

        ids = {
            result.value[
                "prescription_id"
            ]
            for result in results
        }

        assert len(ids) == 1

        with app.app_context():
            prescription_count = (
                db.session.execute(
                    select(
                        func.count(
                            Prescription.id
                        )
                    ).where(
                        Prescription.clinic_id
                        == clinic_id,
                        Prescription.patient_id
                        == patient_id,
                    )
                ).scalar_one()
            )

            ledger_count = (
                db.session.execute(
                    select(
                        func.count(
                            IdempotencyRecord.id
                        )
                    ).where(
                        IdempotencyRecord.clinic_id
                        == clinic_id,
                        IdempotencyRecord.user_id
                        == doctor_user_id,
                        IdempotencyRecord.operation
                        == "prescription.create",
                        IdempotencyRecord.idempotency_key
                        == "gate19-rx-same-key",
                    )
                ).scalar_one()
            )

            assert prescription_count == 1
            assert ledger_count == 1

    finally:
        if harness is not None:
            harness.close()

        _cleanup(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )


def test_two_users_reserving_same_bed_has_one_winner(
    monkeypatch,
):
    (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    ) = _build_app()

    harness = None

    try:
        _reset_schema(
            app
        )

        monkeypatch.setattr(
            ward_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        with app.app_context():
            clinic = Clinic(
                name="Gate 19 Reservation Clinic",
                status=ClinicStatus.ACTIVE,
                ai_credits=5,
            )
            db.session.add(
                clinic
            )
            db.session.flush()

            staff_a = _add_staff(
                clinic=clinic,
                role=Role.NURSE,
                email="gate19-reserve-a@test.com",
                first_name="ReserveA",
            )
            staff_b = _add_staff(
                clinic=clinic,
                role=Role.NURSE,
                email="gate19-reserve-b@test.com",
                first_name="ReserveB",
            )

            patient_a = _add_patient(
                clinic=clinic,
                suffix="RES-A",
            )
            patient_b = _add_patient(
                clinic=clinic,
                suffix="RES-B",
            )

            ward = Ward(
                clinic_id=clinic.id,
                name="Reservation Ward",
                capacity=1,
            )
            db.session.add(
                ward
            )
            db.session.flush()

            bed = Bed(
                ward_id=ward.id,
                bed_number="R1",
                status=BedStatus.AVAILABLE,
            )
            db.session.add(
                bed
            )
            db.session.commit()

            clinic_id = clinic.id
            bed_id = bed.id
            workers = (
                (
                    patient_a.id,
                    staff_a.id,
                    staff_a.user_id,
                ),
                (
                    patient_b.id,
                    staff_b.id,
                    staff_b.user_id,
                ),
            )

        gate = Barrier(
            2
        )
        original_get_bed = (
            ward_service._get_bed
        )

        def synchronized_get_bed(
            bed_arg,
            clinic_id=None,
            lock=False,
        ):
            if (
                lock
                and bed_arg == bed_id
            ):
                gate.wait(
                    timeout=15
                )

            return original_get_bed(
                bed_arg,
                clinic_id=clinic_id,
                lock=lock,
            )

        monkeypatch.setattr(
            ward_service,
            "_get_bed",
            synchronized_get_bed,
        )

        harness = _harness()

        def reserve(worker_index):
            (
                patient_id,
                staff_id,
                user_id,
            ) = workers[
                worker_index
            ]

            try:
                reservation = (
                    ward_service.reserve_bed(
                        patient_id=patient_id,
                        bed_id=bed_id,
                        reserved_by_id=staff_id,
                        clinic_id=clinic_id,
                        actor_user_id=user_id,
                        reason="Gate 19 race",
                    )
                )

                return {
                    "outcome": "accepted",
                    "reservation_id": reservation.id,
                    "patient_id": patient_id,
                }

            except Exception as exc:
                return {
                    "outcome": "rejected",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "patient_id": patient_id,
                }

        results = harness.run_flask(
            app,
            reserve,
        )

        _assert_distinct_connections(
            results
        )

        values = [
            result.value
            for result in results
        ]
        accepted = [
            value
            for value in values
            if value["outcome"]
            == "accepted"
        ]
        rejected = [
            value
            for value in values
            if value["outcome"]
            == "rejected"
        ]

        assert len(accepted) == 1
        assert len(rejected) == 1
        assert rejected[0][
            "error_type"
        ] == ConflictError.__name__

        with app.app_context():
            final_bed = db.session.get(
                Bed,
                bed_id,
            )

            pending = list(
                db.session.execute(
                    select(
                        BedReservation
                    ).where(
                        BedReservation.bed_id
                        == bed_id,
                        BedReservation.status
                        == ReservationStatus.PENDING,
                    )
                ).scalars()
            )

            assert final_bed.status == BedStatus.RESERVED
            assert len(pending) == 1
            assert (
                pending[0].patient_id
                == accepted[0][
                    "patient_id"
                ]
            )

    finally:
        if harness is not None:
            harness.close()

        _cleanup(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )


def test_two_transfers_targeting_same_bed_have_one_winner(
    monkeypatch,
):
    (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    ) = _build_app()

    harness = None

    try:
        _reset_schema(
            app
        )

        monkeypatch.setattr(
            ward_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        with app.app_context():
            clinic = Clinic(
                name="Gate 19 Transfer Clinic",
                status=ClinicStatus.ACTIVE,
                ai_credits=5,
            )
            db.session.add(
                clinic
            )
            db.session.flush()

            actor = _add_staff(
                clinic=clinic,
                role=Role.NURSE,
                email="gate19-transfer@test.com",
                first_name="Transfer",
            )
            patient_a = _add_patient(
                clinic=clinic,
                suffix="TR-A",
            )
            patient_b = _add_patient(
                clinic=clinic,
                suffix="TR-B",
            )

            ward = Ward(
                clinic_id=clinic.id,
                name="Transfer Ward",
                capacity=3,
            )
            db.session.add(
                ward
            )
            db.session.flush()

            source_a = Bed(
                ward_id=ward.id,
                bed_number="T1",
                status=BedStatus.OCCUPIED,
            )
            source_b = Bed(
                ward_id=ward.id,
                bed_number="T2",
                status=BedStatus.OCCUPIED,
            )
            target = Bed(
                ward_id=ward.id,
                bed_number="T3",
                status=BedStatus.AVAILABLE,
            )
            db.session.add_all(
                [
                    source_a,
                    source_b,
                    target,
                ]
            )
            db.session.flush()

            admission_a = Admission(
                patient_id=patient_a.id,
                bed_id=source_a.id,
                admitted_by_id=actor.id,
                status=AdmissionStatus.ADMITTED,
            )
            admission_b = Admission(
                patient_id=patient_b.id,
                bed_id=source_b.id,
                admitted_by_id=actor.id,
                status=AdmissionStatus.ADMITTED,
            )
            db.session.add_all(
                [
                    admission_a,
                    admission_b,
                ]
            )
            db.session.commit()

            clinic_id = clinic.id
            target_id = target.id
            admission_ids = (
                admission_a.id,
                admission_b.id,
            )
            source_ids = (
                source_a.id,
                source_b.id,
            )

        # Synchronize before either worker begins its transfer lock
        # acquisition. Do not place a barrier on the destination-bed
        # lookup: _get_bed(..., lock=True) joins Ward and PostgreSQL's
        # unqualified FOR UPDATE also locks that shared ward row. A
        # target-bed barrier would therefore make worker A hold the ward
        # row while waiting for worker B, even though worker B cannot
        # reach that barrier until A releases the ward row.
        gate = Barrier(
            2
        )
        original_get_admission = (
            ward_service._get_admission
        )

        def synchronized_get_admission(
            admission_arg,
            clinic_id=None,
            lock=False,
        ):
            if lock:
                gate.wait(
                    timeout=15
                )

            return original_get_admission(
                admission_arg,
                clinic_id=clinic_id,
                lock=lock,
            )

        monkeypatch.setattr(
            ward_service,
            "_get_admission",
            synchronized_get_admission,
        )

        harness = _harness()

        def transfer(worker_index):
            admission_id = (
                admission_ids[
                    worker_index
                ]
            )

            try:
                row = (
                    ward_service.transfer_bed(
                        admission_id=admission_id,
                        to_bed_id=target_id,
                        clinic_id=clinic_id,
                        reason="Gate 19 target race",
                    )
                )

                return {
                    "outcome": "accepted",
                    "admission_id": admission_id,
                    "transfer_id": row.id,
                }

            except Exception as exc:
                return {
                    "outcome": "rejected",
                    "admission_id": admission_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

        results = harness.run_flask(
            app,
            transfer,
        )

        _assert_distinct_connections(
            results
        )

        values = [
            result.value
            for result in results
        ]
        accepted = [
            value
            for value in values
            if value["outcome"]
            == "accepted"
        ]
        rejected = [
            value
            for value in values
            if value["outcome"]
            == "rejected"
        ]

        assert len(accepted) == 1
        assert len(rejected) == 1
        assert rejected[0][
            "error_type"
        ] == ConflictError.__name__

        with app.app_context():
            final_target = db.session.get(
                Bed,
                target_id,
            )
            final_admissions = [
                db.session.get(
                    Admission,
                    admission_id,
                )
                for admission_id
                in admission_ids
            ]

            transfer_count = (
                db.session.execute(
                    select(
                        func.count(
                            WardTransfer.id
                        )
                    ).where(
                        WardTransfer.to_bed_id
                        == target_id
                    )
                ).scalar_one()
            )

            assert final_target.status == BedStatus.OCCUPIED
            assert transfer_count == 1

            target_occupants = [
                admission.id
                for admission in final_admissions
                if (
                    admission.bed_id
                    == target_id
                    and admission.status
                    == AdmissionStatus.ADMITTED
                )
            ]

            assert target_occupants == [
                accepted[0][
                    "admission_id"
                ]
            ]

            loser_index = (
                0
                if admission_ids[0]
                == rejected[0][
                    "admission_id"
                ]
                else 1
            )

            assert (
                final_admissions[
                    loser_index
                ].bed_id
                == source_ids[
                    loser_index
                ]
            )

    finally:
        if harness is not None:
            harness.close()

        _cleanup(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )


def _seed_discharge_ready_admission(
    *,
    app,
    monkeypatch,
) -> dict:
    monkeypatch.setattr(
        ward_service,
        "create_audit_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        discharge_service,
        "create_audit_log",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        med_recon_service,
        "create_audit_log",
        lambda *args, **kwargs: None,
    )

    with app.app_context():
        clinic = Clinic(
            name="Gate 19 Discharge Clinic",
            status=ClinicStatus.ACTIVE,
            ai_credits=5,
        )
        db.session.add(
            clinic
        )
        db.session.flush()

        doctor = _add_staff(
            clinic=clinic,
            role=Role.DOCTOR,
            email="gate19-discharge@test.com",
            first_name="Discharge",
        )
        patient = _add_patient(
            clinic=clinic,
            suffix="DISCHARGE",
        )

        ward = Ward(
            clinic_id=clinic.id,
            name="Discharge Ward",
            capacity=2,
        )
        db.session.add(
            ward
        )
        db.session.flush()

        source = Bed(
            ward_id=ward.id,
            bed_number="D1",
            status=BedStatus.OCCUPIED,
        )
        target = Bed(
            ward_id=ward.id,
            bed_number="D2",
            status=BedStatus.AVAILABLE,
        )
        db.session.add_all(
            [
                source,
                target,
            ]
        )
        db.session.flush()

        admission = Admission(
            patient_id=patient.id,
            bed_id=source.id,
            admitted_by_id=doctor.id,
            status=AdmissionStatus.ADMITTED,
        )
        db.session.add(
            admission
        )
        db.session.commit()

        reconciliation = (
            med_recon_service
            .create_medication_reconciliation(
                clinic_id=clinic.id,
                admission_id=admission.id,
                event_type=(
                    MedicationReconciliationEvent
                    .DISCHARGE
                ),
                actor_staff_id=doctor.id,
                no_medications_reported=True,
                items=[],
            )
        )

        med_recon_service.finalize_medication_reconciliation(
            reconciliation.id,
            clinic_id=clinic.id,
            actor_staff_id=doctor.id,
            expected_version=1,
        )

        return {
            "clinic_id": clinic.id,
            "doctor_staff_id": doctor.id,
            "doctor_user_id": doctor.user_id,
            "admission_id": admission.id,
            "source_bed_id": source.id,
            "target_bed_id": target.id,
        }


def test_discharge_racing_with_transfer_is_serially_valid(
    monkeypatch,
):
    (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    ) = _build_app()

    harness = None

    try:
        _reset_schema(
            app
        )

        ids = _seed_discharge_ready_admission(
            app=app,
            monkeypatch=monkeypatch,
        )

        gate = Barrier(
            2
        )

        original_discharge_admission = (
            discharge_service
            ._active_admission
        )
        original_transfer_admission = (
            ward_service
            ._get_admission
        )

        def synchronized_discharge_admission(
            *,
            admission_id,
            clinic_id,
        ):
            gate.wait(
                timeout=15
            )

            return original_discharge_admission(
                admission_id=admission_id,
                clinic_id=clinic_id,
            )

        def synchronized_transfer_admission(
            admission_id,
            clinic_id=None,
            lock=False,
        ):
            if lock:
                gate.wait(
                    timeout=15
                )

            return original_transfer_admission(
                admission_id,
                clinic_id=clinic_id,
                lock=lock,
            )

        monkeypatch.setattr(
            discharge_service,
            "_active_admission",
            synchronized_discharge_admission,
        )
        monkeypatch.setattr(
            ward_service,
            "_get_admission",
            synchronized_transfer_admission,
        )

        harness = _harness()

        def race(worker_index):
            if worker_index == 0:
                try:
                    admission = (
                        ward_service
                        .discharge_patient(
                            admission_id=ids[
                                "admission_id"
                            ],
                            clinic_id=ids[
                                "clinic_id"
                            ],
                            actor_user_id=ids[
                                "doctor_user_id"
                            ],
                            actor_staff_id=ids[
                                "doctor_staff_id"
                            ],
                            reason="Gate 19 race",
                            primary_diagnosis=(
                                "Resolved illness"
                            ),
                            secondary_diagnoses=[],
                            condition_at_discharge=(
                                "Stable"
                            ),
                            instructions=(
                                "Return if symptoms recur"
                            ),
                            follow_up_plan=(
                                "Clinic review in seven days"
                            ),
                            destination="Home",
                            idempotency_key=(
                                "gate19-discharge-transfer"
                            ),
                            idempotency_user_id=ids[
                                "doctor_user_id"
                            ],
                        )
                    )

                    return {
                        "kind": "discharge",
                        "outcome": "accepted",
                        "admission_id": admission.id,
                        "completed_ns": time.monotonic_ns(),
                    }

                except Exception as exc:
                    return {
                        "kind": "discharge",
                        "outcome": "rejected",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "completed_ns": time.monotonic_ns(),
                    }

            try:
                transfer = (
                    ward_service
                    .transfer_bed(
                        admission_id=ids[
                            "admission_id"
                        ],
                        to_bed_id=ids[
                            "target_bed_id"
                        ],
                        clinic_id=ids[
                            "clinic_id"
                        ],
                        reason="Gate 19 transfer race",
                    )
                )

                return {
                    "kind": "transfer",
                    "outcome": "accepted",
                    "transfer_id": transfer.id,
                    "completed_ns": time.monotonic_ns(),
                }

            except Exception as exc:
                return {
                    "kind": "transfer",
                    "outcome": "rejected",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "completed_ns": time.monotonic_ns(),
                }

        results = harness.run_flask(
            app,
            race,
        )

        _assert_distinct_connections(
            results
        )

        values = [
            result.value
            for result in results
        ]

        discharge = next(
            value
            for value in values
            if value["kind"]
            == "discharge"
        )
        transfer = next(
            value
            for value in values
            if value["kind"]
            == "transfer"
        )

        assert discharge[
            "outcome"
        ] == "accepted"

        if (
            transfer[
                "outcome"
            ]
            == "accepted"
        ):
            assert (
                transfer[
                    "completed_ns"
                ]
                <= discharge[
                    "completed_ns"
                ]
            )
        else:
            assert transfer[
                "error_type"
            ] == ConflictError.__name__
            assert (
                discharge[
                    "completed_ns"
                ]
                <= transfer[
                    "completed_ns"
                ]
            )

        with app.app_context():
            admission = db.session.get(
                Admission,
                ids[
                    "admission_id"
                ],
            )
            source = db.session.get(
                Bed,
                ids[
                    "source_bed_id"
                ],
            )
            target = db.session.get(
                Bed,
                ids[
                    "target_bed_id"
                ],
            )

            summary_count = (
                db.session.execute(
                    select(
                        func.count(
                            DischargeSummary.id
                        )
                    ).where(
                        DischargeSummary.admission_id
                        == ids[
                            "admission_id"
                        ]
                    )
                ).scalar_one()
            )

            transfer_count = (
                db.session.execute(
                    select(
                        func.count(
                            WardTransfer.id
                        )
                    ).where(
                        WardTransfer.admission_id
                        == ids[
                            "admission_id"
                        ]
                    )
                ).scalar_one()
            )

            assert (
                admission.status
                == AdmissionStatus.DISCHARGED
            )
            assert admission.discharged_at is not None
            assert source.status == BedStatus.AVAILABLE
            assert target.status == BedStatus.AVAILABLE
            assert summary_count == 1
            assert transfer_count in {
                0,
                1,
            }

            if transfer[
                "outcome"
            ] == "accepted":
                assert transfer_count == 1
            else:
                assert transfer_count == 0

    finally:
        if harness is not None:
            harness.close()

        _cleanup(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )


def test_reconciliation_finalize_racing_with_update_is_serially_valid(
    monkeypatch,
):
    (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    ) = _build_app()

    harness = None

    try:
        _reset_schema(
            app
        )

        monkeypatch.setattr(
            med_recon_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        with app.app_context():
            clinic = Clinic(
                name="Gate 19 MedRec Clinic",
                status=ClinicStatus.ACTIVE,
                ai_credits=5,
            )
            db.session.add(
                clinic
            )
            db.session.flush()

            doctor = _add_staff(
                clinic=clinic,
                role=Role.DOCTOR,
                email="gate19-medrec@test.com",
                first_name="MedRec",
            )
            patient = _add_patient(
                clinic=clinic,
                suffix="MEDREC",
            )

            ward = Ward(
                clinic_id=clinic.id,
                name="MedRec Ward",
                capacity=1,
            )
            db.session.add(
                ward
            )
            db.session.flush()

            bed = Bed(
                ward_id=ward.id,
                bed_number="M1",
                status=BedStatus.OCCUPIED,
            )
            db.session.add(
                bed
            )
            db.session.flush()

            admission = Admission(
                patient_id=patient.id,
                bed_id=bed.id,
                admitted_by_id=doctor.id,
                status=AdmissionStatus.ADMITTED,
            )
            db.session.add(
                admission
            )
            db.session.commit()

            reconciliation = (
                med_recon_service
                .create_medication_reconciliation(
                    clinic_id=clinic.id,
                    admission_id=admission.id,
                    event_type=(
                        MedicationReconciliationEvent
                        .ADMISSION
                    ),
                    actor_staff_id=doctor.id,
                    no_medications_reported=True,
                    notes="Original draft",
                    items=[],
                )
            )

            clinic_id = clinic.id
            doctor_id = doctor.id
            doctor_user_id = doctor.user_id
            reconciliation_id = reconciliation.id

        gate = Barrier(
            2
        )
        original_get_record = (
            med_recon_service
            ._get_record
        )

        def synchronized_get_record(
            reconciliation_arg,
            *,
            clinic_id,
            lock=False,
        ):
            if lock:
                gate.wait(
                    timeout=15
                )

            return original_get_record(
                reconciliation_arg,
                clinic_id=clinic_id,
                lock=lock,
            )

        monkeypatch.setattr(
            med_recon_service,
            "_get_record",
            synchronized_get_record,
        )

        harness = _harness()

        def race(worker_index):
            if worker_index == 0:
                try:
                    record = (
                        med_recon_service
                        .finalize_medication_reconciliation(
                            reconciliation_id,
                            clinic_id=clinic_id,
                            actor_staff_id=doctor_id,
                            expected_version=1,
                            idempotency_key=(
                                "gate19-medrec-finalize"
                            ),
                            idempotency_user_id=(
                                doctor_user_id
                            ),
                        )
                    )

                    return {
                        "kind": "finalize",
                        "outcome": "accepted",
                        "status": record.record_status,
                        "completed_ns": time.monotonic_ns(),
                    }

                except Exception as exc:
                    return {
                        "kind": "finalize",
                        "outcome": "rejected",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "completed_ns": time.monotonic_ns(),
                    }

            try:
                record = (
                    med_recon_service
                    .update_medication_reconciliation_draft(
                        reconciliation_id,
                        clinic_id=clinic_id,
                        actor_staff_id=doctor_id,
                        no_medications_reported=True,
                        notes="Concurrent draft update",
                        items=[],
                    )
                )

                return {
                    "kind": "update",
                    "outcome": "accepted",
                    "notes": record.notes,
                    "completed_ns": time.monotonic_ns(),
                }

            except Exception as exc:
                return {
                    "kind": "update",
                    "outcome": "rejected",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "completed_ns": time.monotonic_ns(),
                }

        results = harness.run_flask(
            app,
            race,
        )

        _assert_distinct_connections(
            results
        )

        values = [
            result.value
            for result in results
        ]

        finalize = next(
            value
            for value in values
            if value["kind"]
            == "finalize"
        )
        update = next(
            value
            for value in values
            if value["kind"]
            == "update"
        )

        assert finalize[
            "outcome"
        ] == "accepted"
        assert finalize[
            "status"
        ] == ClinicalRecordStatus.FINAL.value

        if (
            update[
                "outcome"
            ]
            == "accepted"
        ):
            assert (
                update[
                    "completed_ns"
                ]
                <= finalize[
                    "completed_ns"
                ]
            )
        else:
            assert update[
                "error_type"
            ] == ConflictError.__name__
            assert (
                finalize[
                    "completed_ns"
                ]
                <= update[
                    "completed_ns"
                ]
            )

        with app.app_context():
            record = db.session.get(
                MedicationReconciliation,
                reconciliation_id,
            )

            assert (
                record.record_status
                == ClinicalRecordStatus.FINAL.value
            )
            assert record.record_version == 1

            if (
                update[
                    "outcome"
                ]
                == "accepted"
            ):
                assert (
                    record.notes
                    == "Concurrent draft update"
                )
            else:
                assert (
                    record.notes
                    == "Original draft"
                )

            ledger_count = (
                db.session.execute(
                    select(
                        func.count(
                            IdempotencyRecord.id
                        )
                    ).where(
                        IdempotencyRecord.clinic_id
                        == clinic_id,
                        IdempotencyRecord.user_id
                        == doctor_user_id,
                        IdempotencyRecord.operation
                        == (
                            "medication_reconciliation.finalize"
                        ),
                        IdempotencyRecord.idempotency_key
                        == "gate19-medrec-finalize",
                    )
                ).scalar_one()
            )

            assert ledger_count == 1

    finally:
        if harness is not None:
            harness.close()

        _cleanup(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )
