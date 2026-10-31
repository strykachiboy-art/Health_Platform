from __future__ import annotations

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.core.emergency_access.services.emergency_access_service import (
    assert_emergency_access,
)


def authorize_emergency_read_if_requested(
    *,
    patient_id: int,
    resource_type: str,
    resource_id: int,
):
    """
    Validate an optional break-glass header for one exact clinical
    resource read.

    Ordinary role-authorized reads do not require an emergency grant.
    When X-Emergency-Access-ID is supplied, the request must satisfy
    the existing emergency-access grant, patient, clinic, resource,
    action, expiry, requester, and audit checks.
    """
    raw_emergency_access_id = request.headers.get(
        "X-Emergency-Access-ID"
    )

    if raw_emergency_access_id is None:
        return None

    try:
        emergency_access_id = int(
            raw_emergency_access_id
        )
        actor_id = int(
            get_jwt_identity()
        )
    except (
        TypeError,
        ValueError,
    ):
        return jsonify({
            "success": False,
            "message": "Emergency access is not authorized",
        }), 403

    if (
        emergency_access_id <= 0
        or actor_id <= 0
    ):
        return jsonify({
            "success": False,
            "message": "Emergency access is not authorized",
        }), 403

    allowed = assert_emergency_access(
        actor_id=actor_id,
        patient_id=patient_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action="read",
        emergency_access_id=emergency_access_id,
    )

    if not allowed:
        return jsonify({
            "success": False,
            "message": "Emergency access is not authorized",
        }), 403

    return None
