from __future__ import annotations

import pytest

from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.core.idempotency.services.idempotency_service import (
    reserve_idempotency_operation,
)


def test_reserves_new_operation(
    clinic,
    user,
):
    record, is_new = reserve_idempotency_operation(
        clinic_id=clinic.id,
        user_id=user.id,
        operation="consultation.start",
        idempotency_key="gate3-service-001",
        request_payload={
            "patient_id": 101,
            "staff_id": 202,
            "consultation_type": "general",
        },
    )

    assert is_new is True
    assert record.id > 0
    assert record.clinic_id == clinic.id
    assert record.user_id == user.id
    assert record.operation == "consultation.start"
    assert record.idempotency_key == "gate3-service-001"
    assert len(record.request_hash) == 64


def test_replays_same_operation_with_same_payload(
    clinic,
    user,
    db_session,
):
    payload = {
        "patient_id": 101,
        "staff_id": 202,
        "consultation_type": "general",
    }

    first, first_is_new = reserve_idempotency_operation(
        clinic_id=clinic.id,
        user_id=user.id,
        operation="consultation.start",
        idempotency_key="gate3-service-002",
        request_payload=payload,
    )

    db_session.flush()

    second, second_is_new = reserve_idempotency_operation(
        clinic_id=clinic.id,
        user_id=user.id,
        operation="consultation.start",
        idempotency_key="gate3-service-002",
        request_payload=payload,
    )

    assert first_is_new is True
    assert second_is_new is False
    assert second.id == first.id
    assert second.request_hash == first.request_hash

    count = (
        db_session.query(IdempotencyRecord)
        .filter(
            IdempotencyRecord.clinic_id == clinic.id,
            IdempotencyRecord.user_id == user.id,
            IdempotencyRecord.operation == "consultation.start",
            IdempotencyRecord.idempotency_key == "gate3-service-002",
        )
        .count()
    )

    assert count == 1


def test_rejects_same_key_with_different_payload(
    clinic,
    user,
):
    reserve_idempotency_operation(
        clinic_id=clinic.id,
        user_id=user.id,
        operation="consultation.start",
        idempotency_key="gate3-service-003",
        request_payload={
            "patient_id": 101,
            "staff_id": 202,
            "consultation_type": "general",
        },
    )

    with pytest.raises(
        ConflictError,
        match="Idempotency-Key was already used with a different request payload",
    ) as exc:
        reserve_idempotency_operation(
            clinic_id=clinic.id,
            user_id=user.id,
            operation="consultation.start",
            idempotency_key="gate3-service-003",
            request_payload={
                "patient_id": 999,
                "staff_id": 202,
                "consultation_type": "general",
            },
        )

    assert exc.value.code == "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH"
