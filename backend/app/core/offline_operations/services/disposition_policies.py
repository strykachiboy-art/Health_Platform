from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import IdempotencyRecord
from app.extensions import db


NoEffectVerifier = Callable[..., None]


@dataclass(frozen=True)
class OperationDispositionPolicy:
    """Per-operation authority rules for recording a no-effect disposition.

    A policy is registered only after its endpoint's idempotency and unknown-
    outcome behavior are verified. A registry entry is not a blanket replay
    permission; endpoint adapters retain their own verified replay contracts.
    """

    operation_type: str
    version: str
    allowed_roles: frozenset[str]
    verify_no_server_effect: NoEffectVerifier


def _verify_appointment_create_no_effect(
    *, clinic_id: int, user_id: int, idempotency_key: str
) -> None:
    record = db.session.execute(
        db.select(IdempotencyRecord)
        .where(
            IdempotencyRecord.clinic_id == clinic_id,
            IdempotencyRecord.user_id == user_id,
            IdempotencyRecord.operation == "appointment.create",
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if record is not None:
        raise ConflictError(
            "A server idempotency record exists; reconcile the operation before disposing it",
            code="OFFLINE_OPERATION_OUTCOME_REQUIRES_RECONCILIATION",
        )


_CLINICAL_MUTATION_ROLES = frozenset(
    {
        Role.ADMIN.value,
        Role.DOCTOR.value,
        Role.NURSE.value,
        Role.RECEPTIONIST.value,
    }
)


# Extend only with a policy whose server-side effect check and permission set
# have been implemented and tested for that exact operation type.
DISPOSITION_POLICIES: dict[str, OperationDispositionPolicy] = {
    "appointment.create": OperationDispositionPolicy(
        operation_type="appointment.create",
        version="1",
        allowed_roles=_CLINICAL_MUTATION_ROLES,
        verify_no_server_effect=_verify_appointment_create_no_effect,
    ),
}


def resolve_disposition_policy(
    operation_type: str,
) -> OperationDispositionPolicy | None:
    if not isinstance(operation_type, str):
        return None
    return DISPOSITION_POLICIES.get(operation_type)


def supported_disposition_operation_types() -> frozenset[str]:
    return frozenset(DISPOSITION_POLICIES)