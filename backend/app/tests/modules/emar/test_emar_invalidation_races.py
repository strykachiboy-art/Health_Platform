from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import select

from app import create_app
from app.config import config_by_name
from app.core.auth.user.models.user_model import User
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.core.enums.prescription_enums import (
    MedicationOrderPriority,
    MedicationOrderType,
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.emar.services import emar_service
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    Ward,
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


def _seed(
    app,
    *,
    suffix: str,
) -> dict:
    with app.app_context():
        from app import models_registry  # noqa: F401

        db.drop_all()
        db.create_all()

        clinic = Clinic(
            name=f"Gate 6 Race Clinic {suffix}",
            status=ClinicStatus.ACTIVE,
            ai_credits=5,
        )
        db.session.add(clinic)
        db.session.flush()

        user = User(
            clinic_id=clinic.id,
            role=Role.NURSE,
            is_active=True,
            email=f"gate6-race-{suffix}@test.com",
        )
        user.set_password(
            "supersecret"
        )
        db.session.add(user)
        db.session.flush()

        staff = Staff(
            clinic_id=clinic.id,
            user_id=user.id,
            first_name="Race",
            last_name="Nurse",
        )

        patient = Patient(
            clinic_id=clinic.id,
            first_name="Race",
            last_name="Patient",
            patient_number=f"G6-RACE-{suffix}",
            is_active=True,
        )

        db.session.add_all(
            [
                staff,
                patient,
            ]
        )
        db.session.flush()

        ward = Ward(
            clinic_id=clinic.id,
            name=f"Gate 6 Ward {suffix}",
            capacity=1,
        )
        db.session.add(ward)
        db.session.flush()

        bed = Bed(
            ward_id=ward.id,
            bed_number="1",
        )
        db.session.add(bed)
        db.session.flush()

        admission = Admission(
            patient_id=patient.id,
            bed_id=bed.id,
            admitted_by_id=staff.id,
            status=AdmissionStatus.ADMITTED,
        )
        db.session.add(admission)
        db.session.flush()

        drug = Drug(
            clinic_id=clinic.id,
            name=f"Gate 6 Drug {suffix}",
            is_active=True,
        )
        db.session.add(drug)
        db.session.flush()

        prescription = Prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            status=PrescriptionStatus.ACTIVE,
        )
        db.session.add(prescription)
        db.session.flush()

        item = PrescriptionItem(
            prescription_id=prescription.id,
            drug_id=drug.id,
            dose_value=500,
            dose_unit="mg",
            route_code="oral",
            is_prn=False,
            order_priority=MedicationOrderPriority.ROUTINE,
            order_type=MedicationOrderType.INPATIENT,
            ordered_at=datetime.now(
                timezone.utc
            ),
        )
        db.session.add(item)
        db.session.flush()

        administration = MedicationAdministration(
            clinic_id=clinic.id,
            patient_id=patient.id,
            admission_id=admission.id,
            prescription_id=prescription.id,
            prescription_item_id=item.id,
            scheduled_at=datetime.now(
                timezone.utc
            ),
            dose_value=item.dose_value,
            dose_unit=item.dose_unit,
            route_code=item.route_code,
            status=MedicationAdministrationStatus.SCHEDULED,
            source_order_version=1,
        )
        db.session.add(administration)
        db.session.commit()

        return {
            "clinic_id": clinic.id,
            "user_id": user.id,
            "staff_id": staff.id,
            "admission_id": admission.id,
            "prescription_id": prescription.id,
            "administration_id": administration.id,
        }


def _cleanup(
    app,
    extensions,
    original_database_url,
    original_redis_client,
) -> None:
    with app.app_context():
        db.session.rollback()
        db.drop_all()
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


def _ledger_for_key(
    *,
    clinic_id: int,
    user_id: int,
    key: str,
):
    return (
        db.session.execute(
            select(
                IdempotencyRecord
            ).where(
                IdempotencyRecord.clinic_id
                == clinic_id,
                IdempotencyRecord.user_id
                == user_id,
                IdempotencyRecord.operation
                == "emar.administer",
                IdempotencyRecord.idempotency_key
                == key,
            )
        )
        .scalars()
        .all()
    )


def test_prescription_cancellation_race_serializes_with_administration(
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
        ids = _seed(
            app,
            suffix="cancel",
        )

        monkeypatch.setattr(
            emar_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        barrier = Barrier(
            2
        )

        original_get_order_context = (
            emar_service
            ._get_order_context
        )

        def synchronized_get_order_context(
            *args,
            **kwargs,
        ):
            if kwargs.get(
                "lock",
                False,
            ):
                barrier.wait(
                    timeout=15
                )

            return original_get_order_context(
                *args,
                **kwargs,
            )

        monkeypatch.setattr(
            emar_service,
            "_get_order_context",
            synchronized_get_order_context,
        )

        from app.tests.core.concurrency.concurrency_harness import (
            PostgresConcurrencyHarness,
        )

        harness = PostgresConcurrencyHarness(
            _database_url()
        )

        def worker(worker_index):
            if worker_index == 0:
                try:
                    result = (
                        emar_service
                        .administer_medication(
                            administration_id=ids[
                                "administration_id"
                            ],
                            clinic_id=ids[
                                "clinic_id"
                            ],
                            actor_staff_id=ids[
                                "staff_id"
                            ],
                            notes="Cancellation race",
                            idempotency_key=(
                                "gate6-cancel-race"
                            ),
                            idempotency_user_id=ids[
                                "user_id"
                            ],
                        )
                    )

                    return {
                        "kind": "administer",
                        "outcome": "accepted",
                        "status": result.status.value,
                        "completed_ns": time.monotonic_ns(),
                    }

                except Exception as exc:
                    return {
                        "kind": "administer",
                        "outcome": "rejected",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "completed_ns": time.monotonic_ns(),
                    }

            barrier.wait(
                timeout=15
            )

            prescription = (
                db.session.execute(
                    select(
                        Prescription
                    )
                    .where(
                        Prescription.id
                        == ids[
                            "prescription_id"
                        ]
                    )
                    .with_for_update()
                )
                .scalar_one()
            )

            prescription.status = (
                PrescriptionStatus.CANCELLED
            )
            db.session.commit()

            return {
                "kind": "cancel",
                "outcome": "accepted",
                "completed_ns": time.monotonic_ns(),
            }

        results = harness.run_flask(
            app,
            worker,
        )

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

        values = [
            result.value
            for result in results
        ]

        admin = next(
            value
            for value in values
            if value["kind"] == "administer"
        )
        cancel = next(
            value
            for value in values
            if value["kind"] == "cancel"
        )

        assert cancel[
            "outcome"
        ] == "accepted"

        if (
            admin[
                "outcome"
            ]
            == "accepted"
        ):
            assert (
                admin[
                    "status"
                ]
                == MedicationAdministrationStatus
                .ADMINISTERED
                .value
            )
            assert (
                admin[
                    "completed_ns"
                ]
                <= cancel[
                    "completed_ns"
                ]
            )
        else:
            assert (
                admin[
                    "error_type"
                ]
                == ConflictError.__name__
            )
            assert (
                "is not active"
                in admin[
                    "error"
                ]
            )
            assert (
                cancel[
                    "completed_ns"
                ]
                <= admin[
                    "completed_ns"
                ]
            )

        with app.app_context():
            prescription = db.session.get(
                Prescription,
                ids[
                    "prescription_id"
                ],
            )
            administration = db.session.get(
                MedicationAdministration,
                ids[
                    "administration_id"
                ],
            )

            assert (
                prescription.status
                == PrescriptionStatus.CANCELLED
            )

            if (
                admin[
                    "outcome"
                ]
                == "accepted"
            ):
                assert (
                    administration.status
                    == MedicationAdministrationStatus
                    .ADMINISTERED
                )
                assert len(
                    _ledger_for_key(
                        clinic_id=ids[
                            "clinic_id"
                        ],
                        user_id=ids[
                            "user_id"
                        ],
                        key="gate6-cancel-race",
                    )
                ) == 1
            else:
                assert (
                    administration.status
                    == MedicationAdministrationStatus
                    .SCHEDULED
                )
                assert (
                    _ledger_for_key(
                        clinic_id=ids[
                            "clinic_id"
                        ],
                        user_id=ids[
                            "user_id"
                        ],
                        key="gate6-cancel-race",
                    )
                    == []
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


def test_discharge_race_serializes_with_administration(
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
        ids = _seed(
            app,
            suffix="discharge",
        )

        monkeypatch.setattr(
            emar_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        barrier = Barrier(
            2
        )

        original_get_active_admission = (
            emar_service
            ._get_active_admission
        )

        def synchronized_get_active_admission(
            *args,
            **kwargs,
        ):
            if kwargs.get(
                "lock",
                False,
            ):
                barrier.wait(
                    timeout=15
                )

            return original_get_active_admission(
                *args,
                **kwargs,
            )

        monkeypatch.setattr(
            emar_service,
            "_get_active_admission",
            synchronized_get_active_admission,
        )

        from app.tests.core.concurrency.concurrency_harness import (
            PostgresConcurrencyHarness,
        )

        harness = PostgresConcurrencyHarness(
            _database_url()
        )

        def worker(worker_index):
            if worker_index == 0:
                try:
                    result = (
                        emar_service
                        .administer_medication(
                            administration_id=ids[
                                "administration_id"
                            ],
                            clinic_id=ids[
                                "clinic_id"
                            ],
                            actor_staff_id=ids[
                                "staff_id"
                            ],
                            notes="Discharge race",
                            idempotency_key=(
                                "gate6-discharge-race"
                            ),
                            idempotency_user_id=ids[
                                "user_id"
                            ],
                        )
                    )

                    return {
                        "kind": "administer",
                        "outcome": "accepted",
                        "status": result.status.value,
                        "completed_ns": time.monotonic_ns(),
                    }

                except Exception as exc:
                    return {
                        "kind": "administer",
                        "outcome": "rejected",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "completed_ns": time.monotonic_ns(),
                    }

            barrier.wait(
                timeout=15
            )

            admission = (
                db.session.execute(
                    select(
                        Admission
                    )
                    .where(
                        Admission.id
                        == ids[
                            "admission_id"
                        ]
                    )
                    .with_for_update()
                )
                .scalar_one()
            )

            admission.status = (
                AdmissionStatus.DISCHARGED
            )
            admission.discharged_at = (
                datetime.now(
                    timezone.utc
                )
            )
            db.session.commit()

            return {
                "kind": "discharge",
                "outcome": "accepted",
                "completed_ns": time.monotonic_ns(),
            }

        results = harness.run_flask(
            app,
            worker,
        )

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

        values = [
            result.value
            for result in results
        ]

        admin = next(
            value
            for value in values
            if value["kind"] == "administer"
        )
        discharge = next(
            value
            for value in values
            if value["kind"] == "discharge"
        )

        assert discharge[
            "outcome"
        ] == "accepted"

        if (
            admin[
                "outcome"
            ]
            == "accepted"
        ):
            assert (
                admin[
                    "status"
                ]
                == MedicationAdministrationStatus
                .ADMINISTERED
                .value
            )
            assert (
                admin[
                    "completed_ns"
                ]
                <= discharge[
                    "completed_ns"
                ]
            )
        else:
            assert (
                admin[
                    "error_type"
                ]
                == ConflictError.__name__
            )
            assert (
                "active admission"
                in admin[
                    "error"
                ]
            )
            assert (
                discharge[
                    "completed_ns"
                ]
                <= admin[
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
            administration = db.session.get(
                MedicationAdministration,
                ids[
                    "administration_id"
                ],
            )

            assert (
                admission.status
                == AdmissionStatus.DISCHARGED
            )
            assert (
                admission.discharged_at
                is not None
            )

            if (
                admin[
                    "outcome"
                ]
                == "accepted"
            ):
                assert (
                    administration.status
                    == MedicationAdministrationStatus
                    .ADMINISTERED
                )
                assert len(
                    _ledger_for_key(
                        clinic_id=ids[
                            "clinic_id"
                        ],
                        user_id=ids[
                            "user_id"
                        ],
                        key="gate6-discharge-race",
                    )
                ) == 1
            else:
                assert (
                    administration.status
                    == MedicationAdministrationStatus
                    .SCHEDULED
                )
                assert (
                    _ledger_for_key(
                        clinic_id=ids[
                            "clinic_id"
                        ],
                        user_id=ids[
                            "user_id"
                        ],
                        key="gate6-discharge-race",
                    )
                    == []
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
