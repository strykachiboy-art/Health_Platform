from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app import create_app
from app.config import config_by_name
from app.core.auth.user.models.user_model import User
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskStatus,
    NursingHandoffStatus,
    NursingNoteStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import (
    AdmissionStatus,
    BedStatus,
    WardType,
)
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.nursing.models.nursing_model import (
    NursingCarePlan,
    NursingCareTask,
    NursingHandoff,
    NursingNote,
)
from app.modules.nursing.services import nursing_service
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    Ward,
)


SAME_KEY_KINDS = (
    "care_plan",
    "care_task",
    "note",
    "handoff",
)

DISTINCT_KEY_KINDS = (
    "care_plan",
    "care_task",
    "handoff",
)

OPERATIONS = {
    "care_plan": "nursing.care_plan.complete",
    "care_task": "nursing.care_task.complete",
    "note": "nursing.note.sign",
    "handoff": "nursing.handoff.acknowledge",
}

ENTITY_TYPES = {
    "care_plan": "NursingCarePlan",
    "care_task": "NursingCareTask",
    "note": "NursingNote",
    "handoff": "NursingHandoff",
}

FINAL_STATUSES = {
    "care_plan": NursingCarePlanStatus.COMPLETED,
    "care_task": NursingCareTaskStatus.COMPLETED,
    "note": NursingNoteStatus.SIGNED,
    "handoff": NursingHandoffStatus.ACKNOWLEDGED,
}

MODELS = {
    "care_plan": NursingCarePlan,
    "care_task": NursingCareTask,
    "note": NursingNote,
    "handoff": NursingHandoff,
}

LOADERS = {
    "care_plan": "_get_care_plan",
    "care_task": "_get_care_task",
    "note": "_get_note",
    "handoff": "_get_handoff",
}

TIMESTAMPS = {
    "care_plan": "completed_at",
    "care_task": "completed_at",
    "note": "signed_at",
    "handoff": "acknowledged_at",
}


def _database_url() -> str:
    value = os.getenv(
        "CONCURRENCY_TEST_DATABASE_URL"
    )

    if not value:
        pytest.fail(
            "CONCURRENCY_TEST_DATABASE_URL is required"
        )

    if not value.startswith(
        "postgresql"
    ):
        pytest.fail(
            "Concurrency tests require PostgreSQL"
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


def _now():
    return datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )


def _seed_target(
    app,
    *,
    kind: str,
    suffix: str,
) -> dict:
    with app.app_context():
        from app import models_registry  # noqa: F401

        db.drop_all()
        db.create_all()

        clinic = Clinic(
            name=(
                "Gate 8 Concurrency "
                f"{kind} {suffix}"
            ),
            status=ClinicStatus.ACTIVE,
            ai_credits=5,
        )

        db.session.add(
            clinic
        )
        db.session.flush()

        primary_user = User(
            clinic_id=clinic.id,
            role=Role.NURSE,
            is_active=True,
            email=(
                "gate8-primary-"
                f"{kind}-{suffix}@test.com"
            ),
        )
        primary_user.set_password(
            "supersecret"
        )

        incoming_user = User(
            clinic_id=clinic.id,
            role=Role.NURSE,
            is_active=True,
            email=(
                "gate8-incoming-"
                f"{kind}-{suffix}@test.com"
            ),
        )
        incoming_user.set_password(
            "supersecret"
        )

        db.session.add_all(
            [
                primary_user,
                incoming_user,
            ]
        )
        db.session.flush()

        primary_staff = Staff(
            clinic_id=clinic.id,
            user_id=primary_user.id,
            first_name="Primary",
            last_name="Nurse",
            status=StaffStatus.ACTIVE,
        )

        incoming_staff = Staff(
            clinic_id=clinic.id,
            user_id=incoming_user.id,
            first_name="Incoming",
            last_name="Nurse",
            status=StaffStatus.ACTIVE,
        )

        patient = Patient(
            clinic_id=clinic.id,
            first_name="Gate",
            last_name="Patient",
            patient_number=(
                "G8-CONC-"
                f"{kind}-{suffix}"
            ),
            is_active=True,
        )

        db.session.add_all(
            [
                primary_staff,
                incoming_staff,
                patient,
            ]
        )
        db.session.flush()

        ward = Ward(
            clinic_id=clinic.id,
            name=(
                "Gate 8 Ward "
                f"{kind}-{suffix}"
            ),
            ward_type=WardType.GENERAL,
            capacity=2,
        )

        db.session.add(
            ward
        )
        db.session.flush()

        bed = Bed(
            ward_id=ward.id,
            bed_number="C-001",
            status=BedStatus.OCCUPIED,
        )

        db.session.add(
            bed
        )
        db.session.flush()

        admission = Admission(
            patient_id=patient.id,
            bed_id=bed.id,
            admitted_by_id=primary_staff.id,
            status=AdmissionStatus.ADMITTED,
            reason="Gate 8 concurrency proof",
            admitted_at=_now(),
        )

        db.session.add(
            admission
        )
        db.session.commit()

        if kind == "care_plan":
            target = nursing_service.create_care_plan(
                admission_id=admission.id,
                clinic_id=clinic.id,
                actor_staff_id=primary_staff.id,
                title="Concurrency plan",
                problem="Double completion risk",
                goal="Complete exactly once",
            )

            actor_staff = primary_staff
            actor_user = primary_user

        elif kind == "care_task":
            plan = nursing_service.create_care_plan(
                admission_id=admission.id,
                clinic_id=clinic.id,
                actor_staff_id=primary_staff.id,
                title="Concurrency task plan",
                problem="Double task risk",
                goal="Complete task exactly once",
            )

            target = nursing_service.create_care_task(
                care_plan_id=plan.id,
                clinic_id=clinic.id,
                actor_staff_id=primary_staff.id,
                description="Complete once",
            )

            actor_staff = primary_staff
            actor_user = primary_user

        elif kind == "note":
            target = nursing_service.create_nursing_note(
                admission_id=admission.id,
                clinic_id=clinic.id,
                actor_staff_id=primary_staff.id,
                note_text=(
                    "Concurrency signing proof."
                ),
            )

            actor_staff = primary_staff
            actor_user = primary_user

        elif kind == "handoff":
            start = _now()

            target = nursing_service.create_handoff(
                admission_id=admission.id,
                clinic_id=clinic.id,
                actor_staff_id=primary_staff.id,
                incoming_staff_id=incoming_staff.id,
                shift_start_at=start,
                shift_end_at=(
                    start
                    + timedelta(hours=8)
                ),
                situation="Stable",
                assessment="No acute distress",
                recommendation="Continue care",
            )

            nursing_service.mark_handoff_ready(
                target.id,
                clinic_id=clinic.id,
                actor_staff_id=primary_staff.id,
            )

            actor_staff = incoming_staff
            actor_user = incoming_user

        else:
            raise AssertionError(
                kind
            )

        return {
            "clinic_id": clinic.id,
            "user_id": actor_user.id,
            "staff_id": actor_staff.id,
            "entity_id": target.id,
        }


def _invoke(
    kind: str,
    ids: dict,
    key: str,
):
    kwargs = {
        "clinic_id": ids["clinic_id"],
        "actor_staff_id": ids["staff_id"],
        "idempotency_key": key,
        "idempotency_user_id": ids["user_id"],
    }

    if kind == "care_plan":
        return nursing_service.complete_care_plan(
            ids["entity_id"],
            **kwargs,
        )

    if kind == "care_task":
        return nursing_service.complete_care_task(
            ids["entity_id"],
            **kwargs,
        )

    if kind == "note":
        return nursing_service.sign_nursing_note(
            ids["entity_id"],
            **kwargs,
        )

    if kind == "handoff":
        return nursing_service.acknowledge_handoff(
            ids["entity_id"],
            **kwargs,
        )

    raise AssertionError(
        kind
    )


@pytest.mark.parametrize(
    "kind",
    SAME_KEY_KINDS,
)
def test_same_key_concurrent_terminal_transition_replays_single_winner(
    kind,
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
        monkeypatch.setattr(
            nursing_service,
            "create_audit_log",
            lambda **_kwargs: None,
        )

        ids = _seed_target(
            app,
            kind=kind,
            suffix="same-key",
        )

        from app.tests.core.concurrency.concurrency_harness import (
            PostgresConcurrencyHarness,
        )

        harness = PostgresConcurrencyHarness(
            _database_url()
        )

        key = (
            "gate8-concurrency-"
            f"{kind}-same"
        )

        def operation(
            worker_index,
        ):
            result = _invoke(
                kind,
                ids,
                key,
            )

            return {
                "worker_index": worker_index,
                "entity_id": result.id,
                "status": result.status.value,
                "version": result.version,
                "timestamp": (
                    getattr(
                        result,
                        TIMESTAMPS[kind],
                    ).isoformat()
                ),
            }

        results = harness.run_flask(
            app,
            operation,
        )

        assert len(results) == 2

        assert all(
            result.error is None
            for result in results
        )

        assert len({
            result.backend_pid
            for result in results
        }) == 2

        values = [
            result.value
            for result in results
        ]

        assert {
            value["entity_id"]
            for value in values
        } == {
            ids["entity_id"]
        }

        assert {
            value["status"]
            for value in values
        } == {
            FINAL_STATUSES[kind].value
        }

        assert len({
            value["timestamp"]
            for value in values
        }) == 1

        assert len({
            value["version"]
            for value in values
        }) == 1

        with app.app_context():
            record = db.session.get(
                MODELS[kind],
                ids["entity_id"],
            )

            assert record is not None
            assert (
                record.status
                == FINAL_STATUSES[kind]
            )

            ledger_rows = db.session.execute(
                select(
                    IdempotencyRecord
                ).where(
                    IdempotencyRecord.clinic_id
                    == ids["clinic_id"],
                    IdempotencyRecord.user_id
                    == ids["user_id"],
                    IdempotencyRecord.operation
                    == OPERATIONS[kind],
                    IdempotencyRecord.idempotency_key
                    == key,
                )
            ).scalars().all()

            assert len(
                ledger_rows
            ) == 1

            ledger = ledger_rows[0]

            assert (
                ledger.entity_type
                == ENTITY_TYPES[kind]
            )
            assert (
                ledger.entity_id
                == ids["entity_id"]
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


@pytest.mark.parametrize(
    "kind",
    DISTINCT_KEY_KINDS,
)
def test_distinct_keys_concurrent_terminal_transition_has_one_winner(
    kind,
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
        monkeypatch.setattr(
            nursing_service,
            "create_audit_log",
            lambda **_kwargs: None,
        )

        ids = _seed_target(
            app,
            kind=kind,
            suffix="distinct-key",
        )

        loader_name = (
            LOADERS[kind]
        )
        original_loader = getattr(
            nursing_service,
            loader_name,
        )

        lock_barrier = Barrier(
            2
        )

        def synchronized_loader(
            entity_id,
            clinic_id,
            *,
            lock=False,
        ):
            if lock:
                lock_barrier.wait(
                    timeout=15
                )

            return original_loader(
                entity_id,
                clinic_id,
                lock=lock,
            )

        monkeypatch.setattr(
            nursing_service,
            loader_name,
            synchronized_loader,
        )

        from app.tests.core.concurrency.concurrency_harness import (
            PostgresConcurrencyHarness,
        )

        harness = PostgresConcurrencyHarness(
            _database_url()
        )

        def operation(
            worker_index,
        ):
            try:
                result = _invoke(
                    kind,
                    ids,
                    (
                        "gate8-concurrency-"
                        f"{kind}-distinct-"
                        f"{worker_index}"
                    ),
                )

                return {
                    "worker_index": worker_index,
                    "outcome": "accepted",
                    "entity_id": result.id,
                    "status": result.status.value,
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
            operation,
        )

        assert len(results) == 2

        assert all(
            result.error is None
            for result in results
        )

        assert len({
            result.backend_pid
            for result in results
        }) == 2

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

        assert len(
            accepted
        ) == 1

        assert len(
            rejected
        ) == 1

        assert (
            accepted[0]["entity_id"]
            == ids["entity_id"]
        )

        assert (
            accepted[0]["status"]
            == FINAL_STATUSES[kind].value
        )

        assert (
            rejected[0]["error_type"]
            == ConflictError.__name__
        )

        with app.app_context():
            record = db.session.get(
                MODELS[kind],
                ids["entity_id"],
            )

            assert record is not None
            assert (
                record.status
                == FINAL_STATUSES[kind]
            )

            ledger_count = db.session.execute(
                select(
                    func.count(
                        IdempotencyRecord.id
                    )
                ).where(
                    IdempotencyRecord.clinic_id
                    == ids["clinic_id"],
                    IdempotencyRecord.user_id
                    == ids["user_id"],
                    IdempotencyRecord.operation
                    == OPERATIONS[kind],
                )
            ).scalar_one()

            assert ledger_count == 1

            ledger = db.session.execute(
                select(
                    IdempotencyRecord
                ).where(
                    IdempotencyRecord.clinic_id
                    == ids["clinic_id"],
                    IdempotencyRecord.user_id
                    == ids["user_id"],
                    IdempotencyRecord.operation
                    == OPERATIONS[kind],
                )
            ).scalar_one()

            assert (
                ledger.entity_type
                == ENTITY_TYPES[kind]
            )
            assert (
                ledger.entity_id
                == ids["entity_id"]
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
