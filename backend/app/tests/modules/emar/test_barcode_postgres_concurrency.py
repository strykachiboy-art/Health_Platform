from __future__ import annotations

from datetime import datetime, timezone
import os

import pytest
from sqlalchemy import func, select

from app import create_app, extensions
from app.config import config_by_name
from app.core.enums.emar_enums import (
    BarcodeVerificationResult,
    MedicationAdministrationStatus,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.extensions import db
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
    MedicationAdministrationEvent,
)
import app.modules.emar.services.barcode_verification_service as barcode_service
import app.modules.emar.services.emar_service as emar_service
from app.modules.staff.models.staff_model import Staff
from app.tests.core.concurrency.concurrency_harness import (
    PostgresConcurrencyHarness,
)
from app.tests.modules.emar.test_barcode_verification_service import (
    _context,
)


@pytest.fixture(scope="function")
def app():
    database_url = os.getenv(
        "CONCURRENCY_TEST_DATABASE_URL"
    )

    if not database_url:
        pytest.fail(
            "CONCURRENCY_TEST_DATABASE_URL is required"
        )

    original_database_url = (
        config_by_name["testing"].SQLALCHEMY_DATABASE_URI
    )
    original_redis_client = (
        extensions.redis_client
    )

    config_by_name["testing"].SQLALCHEMY_DATABASE_URI = (
        database_url
    )

    flask_app = create_app(
        "testing"
    )

    extensions.redis_client = (
        original_redis_client
    )

    try:
        with flask_app.app_context():
            from app import models_registry  # noqa: F401

            db.session.rollback()
            db.session.remove()
            db.drop_all()
            db.create_all()

        yield flask_app

    finally:
        with flask_app.app_context():
            db.session.rollback()
            db.session.remove()
            db.drop_all()

            try:
                db.engine.dispose()
            except Exception:
                pass

        config_by_name["testing"].SQLALCHEMY_DATABASE_URI = (
            original_database_url
        )
        extensions.redis_client = (
            original_redis_client
        )


def _patch_audits(
    monkeypatch,
):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )


def _setup(
    *,
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(
        monkeypatch
    )

    (
        actor,
        drug,
        admission,
        prescription,
        item,
        record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    values = {
        "clinic_id": clinic.id,
        "patient_id": patient.id,
        "patient_barcode": patient.barcode,
        "drug_id": drug.id,
        "medication_barcode": drug.barcode,
        "admission_id": admission.id,
        "prescription_id": prescription.id,
        "item_id": item.id,
        "administration_id": record.id,
        "actor_id": actor.id,
        "actor_user_id": actor.user_id,
        "scheduled_at": scheduled_at,
    }

    db.session.commit()

    return values


def _harness():
    database_url = os.environ[
        "CONCURRENCY_TEST_DATABASE_URL"
    ]

    return PostgresConcurrencyHarness(
        database_url,
        workers=2,
        barrier_timeout_seconds=20.0,
    )


def _verify(
    *,
    values,
    actor_id,
    operation_identity,
    scan_device,
):
    event = (
        barcode_service
        .verify_barcode_medication_administration(
            clinic_id=values["clinic_id"],
            actor_staff_id=actor_id,
            patient_barcode=(
                values["patient_barcode"]
            ),
            medication_barcode=(
                values["medication_barcode"]
            ),
            scan_timestamp=(
                values["scheduled_at"]
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
        )
    )

    return {
        "event_id": event.id,
        "result": event.verification_result.value,
        "actor_staff_id": event.actor_staff_id,
        "scan_device": event.scan_device,
    }


def _assert_two_postgres_sessions(
    results,
):
    assert len(
        results
    ) == 2

    backend_pids = {
        result.backend_pid
        for result in results
    }

    assert None not in backend_pids
    assert len(
        backend_pids
    ) == 2


def test_same_scan_operation_converges_to_one_event(
    monkeypatch,
    app,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    values = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    harness = _harness()

    try:
        results = harness.run_flask(
            app,
            lambda _worker_index: _verify(
                values=values,
                actor_id=values["actor_id"],
                operation_identity=(
                    "gate7-s4-same-scan"
                ),
                scan_device="rover-same",
            ),
        )

        _assert_two_postgres_sessions(
            results
        )

        assert all(
            result.error is None
            for result in results
        )

        event_ids = {
            result.value["event_id"]
            for result in results
        }

        assert len(
            event_ids
        ) == 1

        with app.app_context():
            count = db.session.execute(
                select(
                    func.count(
                        MedicationAdministrationEvent.id
                    )
                ).where(
                    MedicationAdministrationEvent.clinic_id
                    == values["clinic_id"],
                    MedicationAdministrationEvent.operation_identity
                    == "gate7-s4-same-scan",
                )
            ).scalar_one()

            assert count == 1

    finally:
        harness.close()


def test_same_scan_operation_payload_mismatch_fails_closed(
    monkeypatch,
    app,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    values = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    harness = _harness()

    try:
        results = harness.run_flask(
            app,
            lambda worker_index: _verify(
                values=values,
                actor_id=values["actor_id"],
                operation_identity=(
                    "gate7-s4-payload-race"
                ),
                scan_device=(
                    f"rover-payload-{worker_index}"
                ),
            ),
        )

        _assert_two_postgres_sessions(
            results
        )

        successes = [
            result
            for result in results
            if result.error is None
        ]
        failures = [
            result
            for result in results
            if result.error is not None
        ]

        assert len(
            successes
        ) == 1
        assert len(
            failures
        ) == 1
        assert isinstance(
            failures[0].error,
            ConflictError,
        )
        assert (
            getattr(
                failures[0].error,
                "code",
                None,
            )
            == "BARCODE_OPERATION_PAYLOAD_MISMATCH"
        )

        with app.app_context():
            event = db.session.execute(
                select(
                    MedicationAdministrationEvent
                ).where(
                    MedicationAdministrationEvent.clinic_id
                    == values["clinic_id"],
                    MedicationAdministrationEvent.operation_identity
                    == "gate7-s4-payload-race",
                )
            ).scalar_one()

            record = db.session.get(
                MedicationAdministration,
                values["administration_id"],
            )

            assert record is not None
            assert (
                record.scan_device
                == event.scan_device
            )
            assert (
                record.verification_result
                == BarcodeVerificationResult
                .BARCODE_VERIFIED
            )

    finally:
        harness.close()


def test_two_nurses_devices_leave_only_latest_verification_authoritative(
    monkeypatch,
    app,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    values = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    second_actor, _headers = (
        make_authenticated_staff(
            clinic,
            Role.NURSE,
        )
    )

    second_actor_id = (
        second_actor.id
    )
    second_user_id = (
        second_actor.user_id
    )

    db.session.commit()

    actor_user_ids = {
        values["actor_id"]: (
            values["actor_user_id"]
        ),
        second_actor_id: (
            second_user_id
        ),
    }

    actor_ids = [
        values["actor_id"],
        second_actor_id,
    ]

    harness = _harness()

    try:
        results = harness.run_flask(
            app,
            lambda worker_index: _verify(
                values=values,
                actor_id=(
                    actor_ids[
                        worker_index
                    ]
                ),
                operation_identity=(
                    f"gate7-s4-nurse-{worker_index}"
                ),
                scan_device=(
                    f"rover-nurse-{worker_index}"
                ),
            ),
        )

        _assert_two_postgres_sessions(
            results
        )

        assert all(
            result.error is None
            for result in results
        )

        with app.app_context():
            events = db.session.execute(
                select(
                    MedicationAdministrationEvent
                )
                .where(
                    MedicationAdministrationEvent
                    .medication_administration_id
                    == values[
                        "administration_id"
                    ],
                    MedicationAdministrationEvent
                    .operation_identity
                    .in_(
                        (
                            "gate7-s4-nurse-0",
                            "gate7-s4-nurse-1",
                        )
                    ),
                )
                .order_by(
                    MedicationAdministrationEvent
                    .scan_timestamp.desc(),
                    MedicationAdministrationEvent
                    .id.desc(),
                )
            ).scalars().all()

            assert len(
                events
            ) == 2

            latest = events[0]
            older = events[1]

            record = db.session.get(
                MedicationAdministration,
                values["administration_id"],
            )

            assert record is not None
            assert (
                record.scan_device
                == latest.scan_device
            )
            assert (
                record.barcode_verified_by_staff_id
                == latest.actor_staff_id
            )

            latest_id = latest.id
            latest_actor_id = (
                latest.actor_staff_id
            )
            older_id = older.id
            older_actor_id = (
                older.actor_staff_id
            )

        with app.app_context():
            with pytest.raises(
                ConflictError,
            ):
                (
                    emar_service
                    .administer_barcode_verified_medication(
                        administration_id=(
                            values[
                                "administration_id"
                            ]
                        ),
                        clinic_id=values[
                            "clinic_id"
                        ],
                        actor_staff_id=(
                            older_actor_id
                        ),
                        barcode_verification_event_id=(
                            older_id
                        ),
                        administered_at=values[
                            "scheduled_at"
                        ],
                        idempotency_key=(
                            "gate7-s4-older-event"
                        ),
                        idempotency_user_id=(
                            actor_user_ids[
                                older_actor_id
                            ]
                        ),
                    )
                )

        with app.app_context():
            administered = (
                emar_service
                .administer_barcode_verified_medication(
                    administration_id=values[
                        "administration_id"
                    ],
                    clinic_id=values[
                        "clinic_id"
                    ],
                    actor_staff_id=(
                        latest_actor_id
                    ),
                    barcode_verification_event_id=(
                        latest_id
                    ),
                    administered_at=values[
                        "scheduled_at"
                    ],
                    idempotency_key=(
                        "gate7-s4-latest-event"
                    ),
                    idempotency_user_id=(
                        actor_user_ids[
                            latest_actor_id
                        ]
                    ),
                )
            )

            assert (
                administered.status
                == MedicationAdministrationStatus
                .ADMINISTERED
            )
            assert (
                administered
                .barcode_verification_event_id
                == latest_id
            )

    finally:
        harness.close()


def test_same_admin_idempotency_key_concurrent_replay(
    monkeypatch,
    app,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    values = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    with app.app_context():
        verification = (
            barcode_service
            .verify_barcode_medication_administration(
                clinic_id=values[
                    "clinic_id"
                ],
                actor_staff_id=values[
                    "actor_id"
                ],
                patient_barcode=values[
                    "patient_barcode"
                ],
                medication_barcode=values[
                    "medication_barcode"
                ],
                scan_timestamp=values[
                    "scheduled_at"
                ],
                scan_device="rover-admin-same",
                operation_identity=(
                    "gate7-s4-admin-verify"
                ),
            )
        )

        verification_id = (
            verification.id
        )

    harness = _harness()

    try:
        def administer(
            _worker_index,
        ):
            record = (
                emar_service
                .administer_barcode_verified_medication(
                    administration_id=values[
                        "administration_id"
                    ],
                    clinic_id=values[
                        "clinic_id"
                    ],
                    actor_staff_id=values[
                        "actor_id"
                    ],
                    barcode_verification_event_id=(
                        verification_id
                    ),
                    administered_at=values[
                        "scheduled_at"
                    ],
                    idempotency_key=(
                        "gate7-s4-same-admin-key"
                    ),
                    idempotency_user_id=(
                        values[
                            "actor_user_id"
                        ]
                    ),
                )
            )

            return {
                "record_id": record.id,
                "status": record.status.value,
                "verification_event_id": (
                    record
                    .barcode_verification_event_id
                ),
            }

        results = harness.run_flask(
            app,
            administer,
        )

        _assert_two_postgres_sessions(
            results
        )

        assert all(
            result.error is None
            for result in results
        )

        values_seen = [
            result.value
            for result in results
        ]

        assert len({
            value["record_id"]
            for value in values_seen
        }) == 1

        assert {
            value["status"]
            for value in values_seen
        } == {
            "administered"
        }

        assert {
            value["verification_event_id"]
            for value in values_seen
        } == {
            verification_id
        }

        with app.app_context():
            ledger_count = (
                db.session.execute(
                    select(
                        func.count(
                            IdempotencyRecord.id
                        )
                    ).where(
                        IdempotencyRecord.clinic_id
                        == values["clinic_id"],
                        IdempotencyRecord.user_id
                        == values[
                            "actor_user_id"
                        ],
                        IdempotencyRecord.operation
                        == "emar.administer",
                        IdempotencyRecord.idempotency_key
                        == "gate7-s4-same-admin-key",
                    )
                ).scalar_one()
            )

            assert ledger_count == 1

    finally:
        harness.close()


def test_distinct_admin_keys_cannot_double_administer(
    monkeypatch,
    app,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    values = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    with app.app_context():
        verification = (
            barcode_service
            .verify_barcode_medication_administration(
                clinic_id=values[
                    "clinic_id"
                ],
                actor_staff_id=values[
                    "actor_id"
                ],
                patient_barcode=values[
                    "patient_barcode"
                ],
                medication_barcode=values[
                    "medication_barcode"
                ],
                scan_timestamp=values[
                    "scheduled_at"
                ],
                scan_device=(
                    "rover-distinct-admin"
                ),
                operation_identity=(
                    "gate7-s4-distinct-verify"
                ),
            )
        )

        verification_id = (
            verification.id
        )

    harness = _harness()

    try:
        results = harness.run_flask(
            app,
            lambda worker_index: (
                emar_service
                .administer_barcode_verified_medication(
                    administration_id=values[
                        "administration_id"
                    ],
                    clinic_id=values[
                        "clinic_id"
                    ],
                    actor_staff_id=values[
                        "actor_id"
                    ],
                    barcode_verification_event_id=(
                        verification_id
                    ),
                    administered_at=values[
                        "scheduled_at"
                    ],
                    idempotency_key=(
                        f"gate7-s4-distinct-admin-{worker_index}"
                    ),
                    idempotency_user_id=(
                        values[
                            "actor_user_id"
                        ]
                    ),
                )
            ),
        )

        _assert_two_postgres_sessions(
            results
        )

        successes = [
            result
            for result in results
            if result.error is None
        ]
        failures = [
            result
            for result in results
            if result.error is not None
        ]

        assert len(
            successes
        ) == 1
        assert len(
            failures
        ) == 1
        assert isinstance(
            failures[0].error,
            ConflictError,
        )

        with app.app_context():
            record = db.session.get(
                MedicationAdministration,
                values["administration_id"],
            )

            assert record is not None
            assert (
                record.status
                == MedicationAdministrationStatus
                .ADMINISTERED
            )
            assert (
                record
                .barcode_verification_event_id
                == verification_id
            )

            ledger_count = (
                db.session.execute(
                    select(
                        func.count(
                            IdempotencyRecord.id
                        )
                    ).where(
                        IdempotencyRecord.clinic_id
                        == values["clinic_id"],
                        IdempotencyRecord.operation
                        == "emar.administer",
                        IdempotencyRecord.idempotency_key
                        .in_(
                            (
                                "gate7-s4-distinct-admin-0",
                                "gate7-s4-distinct-admin-1",
                            )
                        ),
                    )
                ).scalar_one()
            )

            assert ledger_count == 1

    finally:
        harness.close()


def test_committed_admin_reconciles_after_simulated_lost_response(
    monkeypatch,
    app,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    values = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    with app.app_context():
        verification = (
            barcode_service
            .verify_barcode_medication_administration(
                clinic_id=values[
                    "clinic_id"
                ],
                actor_staff_id=values[
                    "actor_id"
                ],
                patient_barcode=values[
                    "patient_barcode"
                ],
                medication_barcode=values[
                    "medication_barcode"
                ],
                scan_timestamp=values[
                    "scheduled_at"
                ],
                scan_device=(
                    "rover-lost-response"
                ),
                operation_identity=(
                    "gate7-s4-lost-verify"
                ),
            )
        )

        verification_id = (
            verification.id
        )

        first = (
            emar_service
            .administer_barcode_verified_medication(
                administration_id=values[
                    "administration_id"
                ],
                clinic_id=values[
                    "clinic_id"
                ],
                actor_staff_id=values[
                    "actor_id"
                ],
                barcode_verification_event_id=(
                    verification_id
                ),
                administered_at=values[
                    "scheduled_at"
                ],
                idempotency_key=(
                    "gate7-s4-lost-admin"
                ),
                idempotency_user_id=(
                    values[
                        "actor_user_id"
                    ]
                ),
            )
        )

        first_id = first.id

        # Simulate a response being lost after the commit. A retry reaches
        # the backend in a fresh scoped session.
        db.session.remove()

        replay = (
            emar_service
            .administer_barcode_verified_medication(
                administration_id=values[
                    "administration_id"
                ],
                clinic_id=values[
                    "clinic_id"
                ],
                actor_staff_id=values[
                    "actor_id"
                ],
                barcode_verification_event_id=(
                    verification_id
                ),
                administered_at=values[
                    "scheduled_at"
                ],
                idempotency_key=(
                    "gate7-s4-lost-admin"
                ),
                idempotency_user_id=(
                    values[
                        "actor_user_id"
                    ]
                ),
            )
        )

        assert replay.id == first_id
        assert (
            replay.status
            == MedicationAdministrationStatus
            .ADMINISTERED
        )
        assert (
            replay.barcode_verification_event_id
            == verification_id
        )

        ledger_count = (
            db.session.execute(
                select(
                    func.count(
                        IdempotencyRecord.id
                    )
                ).where(
                    IdempotencyRecord.clinic_id
                    == values["clinic_id"],
                    IdempotencyRecord.user_id
                    == values[
                        "actor_user_id"
                    ],
                    IdempotencyRecord.operation
                    == "emar.administer",
                    IdempotencyRecord.idempotency_key
                    == "gate7-s4-lost-admin",
                )
            ).scalar_one()
        )

        assert ledger_count == 1
