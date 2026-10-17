from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.core.auth.user.models.user_model import User
from app.core.exceptions import DomainError, ValidationError
from app.core.offline_operations.services.disposition_policies import (
    resolve_disposition_policy,
)
from app.core.offline_operations.services.disposition_service import (
    REASON_CODES,
    record_confirmed_no_effect_disposition,
)
from app.core.utils.decorators import active_staff_required, get_current_clinic_id
from app.extensions import db


offline_operation_bp = Blueprint(
    "offline_operation_disposition",
    __name__,
    url_prefix="/offline-operations",
)

_REQUIRED_FIELDS = {
    "operation_type",
    "idempotency_key",
    "confirmed_no_effect",
    "reason_code",
}


def _validated_payload():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != _REQUIRED_FIELDS:
        return None, (
            jsonify({
                "success": False,
                "error": "Request must contain exactly the required disposition fields",
            }),
            422,
        )

    operation_type = body.get("operation_type")
    idempotency_key = body.get("idempotency_key")
    confirmed = body.get("confirmed_no_effect")
    reason_code = body.get("reason_code")

    if (
        not isinstance(operation_type, str)
        or operation_type != operation_type.strip()
        or not operation_type
        or len(operation_type) > 120
    ):
        return None, (
            jsonify({"success": False, "error": "Invalid offline operation type"}),
            422,
        )
    if (
        not isinstance(idempotency_key, str)
        or idempotency_key != idempotency_key.strip()
        or not idempotency_key
        or len(idempotency_key) > 255
    ):
        return None, (
            jsonify({"success": False, "error": "Invalid idempotency key"}),
            422,
        )
    if confirmed is not True:
        return None, (
            jsonify({
                "success": False,
                "error": "Explicit confirmation of no server effect is required",
            }),
            422,
        )
    if not isinstance(reason_code, str) or reason_code not in REASON_CODES:
        return None, (
            jsonify({"success": False, "error": "Invalid disposition reason code"}),
            422,
        )
    return body, None


@offline_operation_bp.post("/<string:operation_id>/disposition")
@active_staff_required
def create_disposition(operation_id: str):
    if (
        not isinstance(operation_id, str)
        or operation_id != operation_id.strip()
        or not operation_id
        or len(operation_id) > 128
    ):
        return jsonify({"success": False, "error": "Invalid offline operation ID"}), 422

    payload, invalid = _validated_payload()
    if invalid is not None:
        return invalid

    policy = resolve_disposition_policy(payload["operation_type"])
    if policy is None:
        return jsonify({
            "success": False,
            "error": "No verified disposition policy is registered for this operation type",
            "code": "OFFLINE_OPERATION_POLICY_UNAVAILABLE",
        }), 422

    current_role = getattr(g, "current_user_role", None)
    if current_role not in policy.allowed_roles:
        return jsonify({"success": False, "error": "Insufficient permissions"}), 403

    try:
        user_id = getattr(g, "current_user_id", None)
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValidationError("Invalid authentication identity")
        user = db.session.get(User, user_id)
        if user is None or not user.is_active:
            raise ValidationError("Authenticated user is unavailable")
        clinic_id = get_current_clinic_id(required=True)
        disposition, created = record_confirmed_no_effect_disposition(
            operation_id=operation_id,
            operation_type=payload["operation_type"],
            idempotency_key=payload["idempotency_key"],
            reason_code=payload["reason_code"],
            user_id=user_id,
            clinic_id=clinic_id,
        )
        return jsonify({
            "success": True,
            "data": {
                "id": disposition.id,
                "operation_id": disposition.operation_id,
                "operation_type": disposition.operation_type,
                "resolution": disposition.resolution,
                "reason_code": disposition.reason_code,
                "policy_version": disposition.policy_version,
                "created_at": disposition.created_at.isoformat(),
            },
        }), 201 if created else 200
    except DomainError as exc:
        db.session.rollback()
        body = {"success": False, "error": str(exc)}
        if exc.code is not None:
            body["code"] = exc.code
        return jsonify(body), exc.status_code