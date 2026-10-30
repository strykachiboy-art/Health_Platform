from __future__ import annotations

import os
from datetime import datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import func, select

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
from app.core.enums.ward_enums import (
    AdmissionStatus,
)
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
)
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

    database_url = _database_url()

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
        database_url
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


def _seed_scheduled_administration(
    app,
    *,
    suffix: str,
) -> dict:
    with app.app_context():
        from app import models_registry  # noqa: F401

        db.drop_all()
        db.create_all()

        clinic = Clinic(
            name=(
                "Gate 6 eMAR Clinic "
                f"{suffix}"
            ),
            status=ClinicStatus.ACTIVE,
            ai_credits=5,
        )

        db.session.add(
            clinic
        )
        db.session.flush()

        user = User(
            clinic_id=clinic.id,
            role=Role.NURSE,
            is_active=True,
            email=(
                "gate6-emar-"
                f"{suffix}@test.com"
            ),
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
            first_name="Gate",
            last_name="Nurse",
        )

        patient = Patient(
            clinic_id=clinic.id,
            first_name="Race",
            last_name="Patient",
            patient_number=(
                "G6-EMAR-"
                f"{suffix}"
            ),
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
            name=(
                "Gate 6 Ward "
                f"{suffix}"
            ),
            capacity=1,
        )

        db.session.add(
            ward
        )
        db.session.flush()

        bed = Bed(
            ward_id=ward.id,
            bed_number="1",
        )

        db.session.add(
            bed
        )
        db.session.flush()

        admission = Admission(
            patient_id=patient.id,
            bed_id=bed.id,
            admitted_by_id=staff.id,
            status=(
                AdmissionStatus
                .ADMITTED
            ),
        )

        db.session.add(
            admission
        )
        db.session.flush()

        drug = Drug(
            clinic_id=clinic.id,
            name=(
                "Gate 6 Drug "
                f"{suffix}"
            ),
            is_active=True,
        )

        db.session.add(
            drug
        )
        db.session.flush()

        prescription = Prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            status=PrescriptionStatus.ACTIVE,
        )

        db.session.add(
            prescription
        )
        db.session.flush()

        item = PrescriptionItem(
            prescription_id=prescription.id,
            drug_id=drug.id,
            dose_value=500,
            dose_unit="mg",
            route_code="oral",
            is_prn=False,
            order_priority=(
                MedicationOrderPriority
                .ROUTINE
            ),
            order_type=(
                MedicationOrderType
                .INPATIENT
            ),
            ordered_at=datetime.now(
                timezone.utc
            ),
        )

        db.session.add(
            item
        )
        db.session.flush()

        record = MedicationAdministration(
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
            status=(
                MedicationAdministrationStatus
                .SCHEDULED
            ),
            source_order_version=1,
        )

        db.session.add(
            record
        )
        db.session.commit()

        return {
            "clinic_id": clinic.id,
            "user_id": user.id,
            "staff_id": staff.id,
            "administration_id": record.id,
        }


def _cleanup_app(
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


def test_same_idempotency_key_double_administration_replays_single_winner(
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
        ids = _seed_scheduled_administration(
            app,
            suffix="same-key",
        )

        monkeypatch.setattr(
            emar_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        from app.tests.core.concurrency.concurrency_harness import (
            PostgresConcurrencyHarness,
        )

        harness = PostgresConcurrencyHarness(
            _database_url()
        )

        def administer(worker_index):
            result = (
                emar_service
                .administer_medication(
                    administration_id=(
                        ids[
                            "administration_id"
                        ]
                    ),
                    clinic_id=(
                        ids[
                            "clinic_id"
                        ]
                    ),
                    actor_staff_id=(
                        ids[
                            "staff_id"
                        ]
                    ),
                    notes=(
                        "Concurrent same-key dose"
                    ),
                    idempotency_key=(
                        "gate6-same-key"
                    ),
                    idempotency_user_id=(
                        ids[
                            "user_id"
                        ]
                    ),
                )
            )

            return {
                "worker_index": worker_index,
                "administration_id": result.id,
                "status": (
                    result.status.value
                ),
                "administered_at": (
                    result.administered_at.isoformat()
                    if result.administered_at
                    is not None
                    else None
                ),
            }

        results = harness.run_flask(
            app,
            administer,
        )

        assert len(results) == 2

        assert all(
            result.error is None
            for result in results
        )

        backend_pids = {
            result.backend_pid
            for result in results
        }

        assert len(
            backend_pids
        ) == 2

        values = [
            result.value
            for result in results
        ]

        assert {
            value[
                "administration_id"
            ]
            for value in values
        } == {
            ids[
                "administration_id"
            ]
        }

        assert {
            value[
                "status"
            ]
            for value in values
        } == {
            (
                MedicationAdministrationStatus
                .ADMINISTERED
                .value
            )
        }

        assert len(
            {
                value[
                    "administered_at"
                ]
                for value in values
            }
        ) == 1

        with app.app_context():
            record = db.session.execute(
                select(
                    MedicationAdministration
                ).where(
                    MedicationAdministration.id
                    == ids[
                        "administration_id"
                    ]
                )
            ).scalar_one()

            assert (
                record.status
                == MedicationAdministrationStatus
                .ADMINISTERED
            )

            ledger_count = (
                db.session.execute(
                    select(
                        func.count(
                            IdempotencyRecord.id
                        )
                    ).where(
                        IdempotencyRecord.clinic_id
                        == ids[
                            "clinic_id"
                        ],
                        IdempotencyRecord.user_id
                        == ids[
                            "user_id"
                        ],
                        IdempotencyRecord.operation
                        == "emar.administer",
                        IdempotencyRecord.idempotency_key
                        == "gate6-same-key",
                    )
                ).scalar_one()
            )

            assert ledger_count == 1

            ledger = db.session.execute(
                select(
                    IdempotencyRecord
                ).where(
                    IdempotencyRecord.clinic_id
                    == ids[
                        "clinic_id"
                    ],
                    IdempotencyRecord.user_id
                    == ids[
                        "user_id"
                    ],
                    IdempotencyRecord.operation
                    == "emar.administer",
                    IdempotencyRecord.idempotency_key
                    == "gate6-same-key",
                )
            ).scalar_one()

            assert (
                ledger.entity_type
                == "MedicationAdministration"
            )
            assert (
                ledger.entity_id
                == ids[
                    "administration_id"
                ]
            )

    finally:
        if harness is not None:
            harness.close()

        _cleanup_app(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )


def test_distinct_idempotency_keys_double_administration_has_one_winner(
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
        ids = _seed_scheduled_administration(
            app,
            suffix="distinct-key",
        )

        monkeypatch.setattr(
            emar_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        lock_barrier = Barrier(
            2
        )

        original_load = (
            emar_service
            ._load_administration
        )

        def synchronized_load(
            administration_id,
            *,
            clinic_id,
            lock=False,
        ):
            if lock:
                lock_barrier.wait(
                    timeout=15
                )

            return original_load(
                administration_id,
                clinic_id=clinic_id,
                lock=lock,
            )

        monkeypatch.setattr(
            emar_service,
            "_load_administration",
            synchronized_load,
        )

        from app.tests.core.concurrency.concurrency_harness import (
            PostgresConcurrencyHarness,
        )

        harness = PostgresConcurrencyHarness(
            _database_url()
        )

        def administer(worker_index):
            try:
                result = (
                    emar_service
                    .administer_medication(
                        administration_id=(
                            ids[
                                "administration_id"
                            ]
                        ),
                        clinic_id=(
                            ids[
                                "clinic_id"
                            ]
                        ),
                        actor_staff_id=(
                            ids[
                                "staff_id"
                            ]
                        ),
                        notes=(
                            "Concurrent distinct-key dose"
                        ),
                        idempotency_key=(
                            "gate6-distinct-"
                            f"{worker_index}"
                        ),
                        idempotency_user_id=(
                            ids[
                                "user_id"
                            ]
                        ),
                    )
                )

                return {
                    "worker_index": worker_index,
                    "outcome": "accepted",
                    "administration_id": (
                        result.id
                    ),
                    "status": (
                        result.status.value
                    ),
                }

            except Exception as exc:
                return {
                    "worker_index": worker_index,
                    "outcome": "rejected",
                    "error_type": (
                        type(exc).__name__
                    ),
                    "error": str(
                        exc
                    ),
                }

        results = harness.run_flask(
            app,
            administer,
        )

        assert len(results) == 2

        assert all(
            result.error is None
            for result in results
        )

        backend_pids = {
            result.backend_pid
            for result in results
        }

        assert len(
            backend_pids
        ) == 2

        values = [
            result.value
            for result in results
        ]

        accepted = [
            value
            for value in values
            if value[
                "outcome"
            ] == "accepted"
        ]

        rejected = [
            value
            for value in values
            if value[
                "outcome"
            ] == "rejected"
        ]

        assert len(
            accepted
        ) == 1

        assert (
            accepted[0][
                "administration_id"
            ]
            == ids[
                "administration_id"
            ]
        )

        assert (
            accepted[0][
                "status"
            ]
            == MedicationAdministrationStatus
            .ADMINISTERED
            .value
        )

        assert len(
            rejected
        ) == 1

        assert (
            rejected[0][
                "error_type"
            ]
            == ConflictError.__name__
        )

        assert (
            "already administered"
            in rejected[0][
                "error"
            ]
        )

        with app.app_context():
            record = db.session.execute(
                select(
                    MedicationAdministration
                ).where(
                    MedicationAdministration.id
                    == ids[
                        "administration_id"
                    ]
                )
            ).scalar_one()

            assert (
                record.status
                == MedicationAdministrationStatus
                .ADMINISTERED
            )

            ledger_rows = (
                db.session.execute(
                    select(
                        IdempotencyRecord
                    ).where(
                        IdempotencyRecord.clinic_id
                        == ids[
                            "clinic_id"
                        ],
                        IdempotencyRecord.user_id
                        == ids[
                            "user_id"
                        ],
                        IdempotencyRecord.operation
                        == "emar.administer",
                    )
                )
                .scalars()
                .all()
            )

            assert len(
                ledger_rows
            ) == 1

            assert (
                ledger_rows[0].entity_type
                == "MedicationAdministration"
            )

            assert (
                ledger_rows[0].entity_id
                == ids[
                    "administration_id"
                ]
            )

    finally:
        if harness is not None:
            harness.close()

        _cleanup_app(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )
