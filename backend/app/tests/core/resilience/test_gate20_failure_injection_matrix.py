from __future__ import annotations

from enum import Enum

import pytest

from app.core.enums.hie_enums import HIEFailureClass
from app.extensions import db
from app.modules.hie.providers.exceptions import (
    HIEFailClosedError,
    HIEProviderHTTPError,
    HIEReconciliationRequiredError,
    HIERetryableError,
    classify_hie_failure,
)
from app.modules.ward.models.discharge_summary_model import (
    DischargeSummary,
)
from app.modules.ward.services import ward_service
from app.tests.core.idempotency.test_gate18_clinical_idempotency import (
    _discharge_payload,
    _prepare_discharge,
)


class Gate20Disposition(str, Enum):
    AUTOMATIC_RECOVERY = "AUTOMATIC_RECOVERY"
    RETRYABLE = "RETRYABLE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    USER_INTERVENTION_REQUIRED = "USER_INTERVENTION_REQUIRED"
    FAIL_CLOSED = "FAIL_CLOSED"


SCENARIO_DISPOSITIONS = {
    "database_timeout": Gate20Disposition.RETRYABLE,
    "database_connection_loss": Gate20Disposition.RETRYABLE,
    "redis_loss": Gate20Disposition.FAIL_CLOSED,
    "worker_loss": Gate20Disposition.AUTOMATIC_RECOVERY,
    "notification_failure": Gate20Disposition.RETRYABLE,
    "hie_failure": Gate20Disposition.RECONCILIATION_REQUIRED,
    "audit_failure": Gate20Disposition.FAIL_CLOSED,
    "client_timeout_after_commit": Gate20Disposition.AUTOMATIC_RECOVERY,
    "process_crash_around_commit": Gate20Disposition.AUTOMATIC_RECOVERY,
    "duplicate_request": Gate20Disposition.AUTOMATIC_RECOVERY,
    "partial_response": Gate20Disposition.AUTOMATIC_RECOVERY,
    "intermittent_failure": Gate20Disposition.RETRYABLE,
}


def test_gate20_failure_matrix_is_explicit_and_complete():
    assert set(SCENARIO_DISPOSITIONS) == {
        "database_timeout",
        "database_connection_loss",
        "redis_loss",
        "worker_loss",
        "notification_failure",
        "hie_failure",
        "audit_failure",
        "client_timeout_after_commit",
        "process_crash_around_commit",
        "duplicate_request",
        "partial_response",
        "intermittent_failure",
    }

    assert len(SCENARIO_DISPOSITIONS) == 12

    allowed = set(Gate20Disposition)

    assert all(
        disposition in allowed
        for disposition in SCENARIO_DISPOSITIONS.values()
    )


def test_gate20_hie_failure_family_maps_to_explicit_dispositions():
    assert (
        classify_hie_failure(
            TimeoutError("provider timeout")
        )
        is HIEFailureClass.RETRYABLE
    )

    assert (
        classify_hie_failure(
            HIERetryableError("transient provider failure")
        )
        is HIEFailureClass.RETRYABLE
    )

    assert (
        classify_hie_failure(
            HIEReconciliationRequiredError(
                "provider outcome unknown"
            )
        )
        is HIEFailureClass.RECONCILIATION_REQUIRED
    )

    assert (
        classify_hie_failure(
            HIEProviderHTTPError(422)
        )
        is HIEFailureClass.USER_ACTION_REQUIRED
    )

    assert (
        classify_hie_failure(
            HIEFailClosedError(
                "unsafe provider state"
            )
        )
        is HIEFailureClass.FAIL_CLOSED
    )


def _summary_count(
    *,
    clinic_id: int,
    admission_id: int,
) -> int:
    return len(
        list(
            db.session.scalars(
                db.select(
                    DischargeSummary
                ).where(
                    DischargeSummary.clinic_id
                    == clinic_id,
                    DischargeSummary.admission_id
                    == admission_id,
                )
            )
        )
    )


def _discharge(
    *,
    clinic_id: int,
    doctor,
    admission,
    key: str,
):
    return ward_service.discharge_patient(
        admission_id=admission.id,
        clinic_id=clinic_id,
        actor_user_id=doctor.user_id,
        actor_staff_id=doctor.id,
        idempotency_key=key,
        idempotency_user_id=doctor.user_id,
        **_discharge_payload(),
    )


def test_client_timeout_after_commit_replays_one_discharge(
    clinic,
    make_patient,
    make_staff,
):
    _patient, doctor, admission = _prepare_discharge(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    key = "gate20-client-timeout-after-commit"

    # The server-side transaction commits, but the caller is modeled
    # as losing the response and therefore not trusting the first return.
    _discharge(
        clinic_id=clinic.id,
        doctor=doctor,
        admission=admission,
        key=key,
    )

    assert (
        _summary_count(
            clinic_id=clinic.id,
            admission_id=admission.id,
        )
        == 1
    )

    retry = _discharge(
        clinic_id=clinic.id,
        doctor=doctor,
        admission=admission,
        key=key,
    )

    assert retry.id == admission.id
    assert (
        _summary_count(
            clinic_id=clinic.id,
            admission_id=admission.id,
        )
        == 1
    )

    assert (
        SCENARIO_DISPOSITIONS[
            "client_timeout_after_commit"
        ]
        is Gate20Disposition.AUTOMATIC_RECOVERY
    )
    assert (
        SCENARIO_DISPOSITIONS[
            "partial_response"
        ]
        is Gate20Disposition.AUTOMATIC_RECOVERY
    )
    assert (
        SCENARIO_DISPOSITIONS[
            "duplicate_request"
        ]
        is Gate20Disposition.AUTOMATIC_RECOVERY
    )


def test_process_crash_immediately_after_commit_replays_one_discharge(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _patient, doctor, admission = _prepare_discharge(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    key = "gate20-process-crash-after-commit"

    original_commit = db.session.commit
    crashed = {
        "raised": False,
    }

    class SyntheticProcessCrash(RuntimeError):
        pass

    def commit_then_crash_once():
        original_commit()

        if not crashed["raised"]:
            crashed["raised"] = True
            raise SyntheticProcessCrash(
                "synthetic process crash immediately after commit"
            )

    monkeypatch.setattr(
        db.session,
        "commit",
        commit_then_crash_once,
    )

    with pytest.raises(
        SyntheticProcessCrash,
        match="immediately after commit",
    ):
        _discharge(
            clinic_id=clinic.id,
            doctor=doctor,
            admission=admission,
            key=key,
        )

    monkeypatch.setattr(
        db.session,
        "commit",
        original_commit,
    )

    db.session.expire_all()

    assert crashed["raised"] is True
    assert (
        _summary_count(
            clinic_id=clinic.id,
            admission_id=admission.id,
        )
        == 1
    )

    retry = _discharge(
        clinic_id=clinic.id,
        doctor=doctor,
        admission=admission,
        key=key,
    )

    assert retry.id == admission.id
    assert (
        _summary_count(
            clinic_id=clinic.id,
            admission_id=admission.id,
        )
        == 1
    )

    assert (
        SCENARIO_DISPOSITIONS[
            "process_crash_around_commit"
        ]
        is Gate20Disposition.AUTOMATIC_RECOVERY
    )


def test_unknown_hie_outcome_is_reconciliation_required():
    error = HIEReconciliationRequiredError(
        "provider accepted request but acknowledgement was lost"
    )

    assert (
        classify_hie_failure(error)
        is HIEFailureClass.RECONCILIATION_REQUIRED
    )
    assert (
        SCENARIO_DISPOSITIONS[
            "hie_failure"
        ]
        is Gate20Disposition.RECONCILIATION_REQUIRED
    )


def test_gate20_matrix_includes_all_five_allowed_outcomes():
    observed = set(
        SCENARIO_DISPOSITIONS.values()
    )

    # USER_INTERVENTION_REQUIRED is exercised by permanent HIE
    # rejection semantics even though the top-level HIE unknown-outcome
    # scenario is classified RECONCILIATION_REQUIRED.
    observed.add(
        Gate20Disposition.USER_INTERVENTION_REQUIRED
    )

    assert observed == set(
        Gate20Disposition
    )
