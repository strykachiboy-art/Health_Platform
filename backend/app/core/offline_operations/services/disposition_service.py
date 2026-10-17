from __future__ import annotations

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError, ValidationError
from app.core.idempotency.models.idempotency_model import IdempotencyRecord
from app.core.offline_operations.models.offline_operation_disposition_model import (
    OfflineOperationDisposition,
)
from app.core.offline_operations.services.disposition_policies import (
    resolve_disposition_policy,
)
from app.core.utils.decorators import transactional
from app.core.auth.user.models.user_model import User
from app.extensions import db


RESOLUTION_CONFIRMED_NO_EFFECT = "confirmed_no_effect"
REASON_CODES = frozenset(
    {"verified_not_sent", "manual_reconciliation_no_effect"}
)


def _lock_active_user(*, user_id: int) -> None:
    locked_user = db.session.execute(
        db.select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()
    if locked_user is None or not locked_user.is_active:
        raise ValidationError("Authenticated user is unavailable")


def assert_operation_not_disposed(
    *, clinic_id: int, user_id: int, operation_type: str, idempotency_key: str
) -> None:
    """Fail closed when this operation key already has an authoritative disposition.

    Must be called inside the operation's transaction before reserving its
    idempotency key or producing a side effect. The shared user row lock
    serializes this check against creation of a disposition record.
    """
    _lock_active_user(user_id=user_id)
    disposed = db.session.execute(
        db.select(OfflineOperationDisposition).where(
            OfflineOperationDisposition.clinic_id == clinic_id,
            OfflineOperationDisposition.user_id == user_id,
            OfflineOperationDisposition.operation_type == operation_type,
            OfflineOperationDisposition.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if disposed is not None:
        raise ConflictError(
            "This offline operation was disposed and cannot be replayed",
            code="OFFLINE_OPERATION_DISPOSED",
        )


@transactional
def record_confirmed_no_effect_disposition(
    *,
    operation_id: str,
    operation_type: str,
    idempotency_key: str,
    reason_code: str,
    user_id: int,
    clinic_id: int,
) -> tuple[OfflineOperationDisposition, bool]:
    """Use a registered operation policy to authorize a durable no-effect decision."""
    if not isinstance(operation_type, str) or not operation_type:
        raise ValidationError("Unsupported offline operation type")
    policy = resolve_disposition_policy(operation_type)
    if policy is None:
        raise ValidationError("Unsupported offline operation type")
    if reason_code not in REASON_CODES:
        raise ValidationError("Invalid offline disposition reason code")
    if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128:
        raise ValidationError("Invalid offline operation ID")
    if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 255:
        raise ValidationError("Invalid idempotency key")

    _lock_active_user(user_id=user_id)

    existing_by_id = db.session.execute(
        db.select(OfflineOperationDisposition).where(
            OfflineOperationDisposition.clinic_id == clinic_id,
            OfflineOperationDisposition.user_id == user_id,
            OfflineOperationDisposition.operation_id == operation_id,
        )
    ).scalar_one_or_none()
    if existing_by_id is not None:
        same_identity = (
            existing_by_id.operation_type == operation_type
            and existing_by_id.idempotency_key == idempotency_key
            and existing_by_id.reason_code == reason_code
            and existing_by_id.resolution == RESOLUTION_CONFIRMED_NO_EFFECT
        )
        if not same_identity:
            raise ConflictError(
                "Offline operation ID was already disposed with different metadata",
                code="OFFLINE_OPERATION_DISPOSITION_IDENTITY_CONFLICT",
            )
        return existing_by_id, False

    existing_by_key = db.session.execute(
        db.select(OfflineOperationDisposition).where(
            OfflineOperationDisposition.clinic_id == clinic_id,
            OfflineOperationDisposition.user_id == user_id,
            OfflineOperationDisposition.operation_type == operation_type,
            OfflineOperationDisposition.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        raise ConflictError(
            "Idempotency key was already disposed for another offline operation",
            code="OFFLINE_OPERATION_IDEMPOTENCY_KEY_ALREADY_DISPOSED",
        )

    # The registered policy checks the authoritative server-side ledger for
    # this exact operation type; client confirmation alone is never evidence.
    policy.verify_no_server_effect(
        clinic_id=clinic_id,
        user_id=user_id,
        idempotency_key=idempotency_key,
    )

    disposition = OfflineOperationDisposition(
        operation_id=operation_id,
        operation_type=operation_type,
        idempotency_key=idempotency_key,
        resolution=RESOLUTION_CONFIRMED_NO_EFFECT,
        reason_code=reason_code,
        policy_version=policy.version,
        user_id=user_id,
        clinic_id=clinic_id,
    )
    db.session.add(disposition)
    db.session.flush()

    # Audit and disposition are committed or rolled back together.
    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="OfflineOperationDisposition",
        entity_id=disposition.id,
        description="Offline operation disposition recorded after policy verification of no server effect",
        new_value={
            "operation_type": operation_type,
            "resolution": RESOLUTION_CONFIRMED_NO_EFFECT,
            "reason_code": reason_code,
            "policy_version": policy.version,
        },
        user_id=user_id,
        clinic_id=clinic_id,
    )
    return disposition, True