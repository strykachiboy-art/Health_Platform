from __future__ import annotations

import hmac
import os

from flask import Blueprint, current_app, jsonify, request

from app.core.autoscaling.drain_state import (
    DRAIN_STATE_KEY,
    BackendDrainState,
)

from app.core.observability import health


health_bp = Blueprint(
    "health",
    __name__,
    url_prefix="/health",
)
_DRAIN_TOKEN_ENV = "PHASE9_BACKEND_DRAIN_TOKEN"
_MIN_DRAIN_TOKEN_LENGTH = 32


def _authorized_backend_drain_state():
    token = os.environ.get(_DRAIN_TOKEN_ENV, "")

    if len(token) < _MIN_DRAIN_TOKEN_LENGTH:
        return None, (
            jsonify(
                {
                    "success": False,
                    "error": "drain_control_unavailable",
                }
            ),
            503,
        )

    supplied = request.headers.get("Authorization", "")
    expected = f"Bearer {token}"

    if not hmac.compare_digest(supplied, expected):
        return None, (
            jsonify(
                {
                    "success": False,
                    "error": "unauthorized",
                }
            ),
            401,
        )

    state = current_app.extensions.get(DRAIN_STATE_KEY)
    if not isinstance(state, BackendDrainState):
        return None, (
            jsonify(
                {
                    "success": False,
                    "error": "drain_control_unavailable",
                }
            ),
            503,
        )

    return state, None


@health_bp.get("/drain-status")
def backend_drain_status():
    state, error = _authorized_backend_drain_state()
    if error is not None:
        return error

    return jsonify(
        {
            "success": True,
            **state.snapshot(),
        }
    ), 200


@health_bp.post("/drain")
def begin_backend_drain():
    state, error = _authorized_backend_drain_state()
    if error is not None:
        return error

    return jsonify(
        {
            "success": True,
            **state.begin_drain(),
        }
    ), 200


@health_bp.post("/undrain")
def cancel_backend_drain():
    state, error = _authorized_backend_drain_state()
    if error is not None:
        return error

    return jsonify(
        {
            "success": True,
            **state.cancel_drain(),
        }
    ), 200


@health_bp.get("/live")
def liveness():
    return jsonify(
        {
            "success": True,
            "status": "ok",
        }
    ), 200


@health_bp.get("/ready")
def readiness():
    state = current_app.extensions.get(DRAIN_STATE_KEY)
    if (
        isinstance(state, BackendDrainState)
        and state.snapshot()["draining"]
    ):
        return jsonify(
            {
                "success": False,
                "status": "draining",
            }
        ), 503

    result = health.collect_readiness()

    if result["ready"]:
        return jsonify(
            {
                "success": True,
                "status": "ready",
            }
        ), 200

    return jsonify(
        {
            "success": False,
            "status": "not_ready",
        }
    ), 503
