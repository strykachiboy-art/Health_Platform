from __future__ import annotations

from flask import Flask, jsonify
import pytest

from app.core.autoscaling.drain_state import (
    DRAIN_STATE_KEY,
    BackendDrainState,
    init_backend_drain,
    release_socket_connection,
    reserve_socket_connection,
)
from app.core.observability.health_routes import health_bp


TOKEN = "test-drain-token-" + ("x" * 40)


def make_app(monkeypatch, *, token=TOKEN):
    if token is None:
        monkeypatch.delenv("PHASE9_BACKEND_DRAIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("PHASE9_BACKEND_DRAIN_TOKEN", token)

    app = Flask(__name__)
    app.config["TESTING"] = True
    init_backend_drain(app)
    app.register_blueprint(health_bp)

    @app.get("/work")
    def work():
        return jsonify({"success": True}), 200

    @app.get("/observe-active")
    def observe_active():
        state = app.extensions[DRAIN_STATE_KEY]
        return jsonify(state.snapshot()), 200

    return app


def bearer():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_http_request_is_counted_during_work_then_released(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()

    response = client.get("/observe-active")
    assert response.status_code == 200
    assert response.json["active_http_requests"] == 1

    state = app.extensions[DRAIN_STATE_KEY]
    assert state.snapshot()["active_http_requests"] == 0


def test_begin_drain_blocks_new_work_and_retains_health_endpoints(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()

    begin = client.post("/health/drain", headers=bearer())
    assert begin.status_code == 200
    assert begin.json["draining"] is True

    blocked = client.get("/work")
    assert blocked.status_code == 503
    assert blocked.json["error"] == "backend_draining"
    assert blocked.headers["Retry-After"] == "1"

    live = client.get("/health/live")
    assert live.status_code == 200

    status = client.get("/health/drain-status", headers=bearer())
    assert status.status_code == 200
    assert status.json["draining"] is True
    assert status.json["active_http_requests"] == 0
    assert status.json["ready_for_retirement"] is True


def test_authenticated_undrain_restores_normal_request_admission(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()

    assert client.post("/health/drain", headers=bearer()).status_code == 200
    assert client.get("/work").status_code == 503

    response = client.post("/health/undrain", headers=bearer())
    assert response.status_code == 200
    assert response.json["draining"] is False
    assert client.get("/work").status_code == 200


def test_drain_control_requires_configured_strong_token(monkeypatch):
    app = make_app(monkeypatch, token=None)
    response = app.test_client().post(
        "/health/drain",
        headers=bearer(),
    )

    assert response.status_code == 503
    assert response.json["error"] == "drain_control_unavailable"
    assert app.extensions[DRAIN_STATE_KEY].snapshot()["draining"] is False


def test_drain_control_rejects_wrong_token(monkeypatch):
    app = make_app(monkeypatch)
    response = app.test_client().post(
        "/health/drain",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401
    assert app.extensions[DRAIN_STATE_KEY].snapshot()["draining"] is False


@pytest.mark.parametrize("token", ["", "short", "x" * 31])
def test_short_token_disables_drain_control(monkeypatch, token):
    app = make_app(monkeypatch, token=token)
    response = app.test_client().post("/health/drain", headers=bearer())

    assert response.status_code == 503
    assert response.json["error"] == "drain_control_unavailable"


def test_active_socket_prevents_retirement_until_released(monkeypatch):
    app = make_app(monkeypatch)
    state = app.extensions[DRAIN_STATE_KEY]

    with app.app_context():
        assert reserve_socket_connection("sid-1") is True
        state.begin_drain()

        snapshot = state.snapshot()
        assert snapshot["draining"] is True
        assert snapshot["active_socket_connections"] == 1
        assert snapshot["ready_for_retirement"] is False

        # New connections are rejected once draining starts.
        assert reserve_socket_connection("sid-2") is False

        assert release_socket_connection("sid-1") is True
        assert release_socket_connection("sid-1") is False

    assert state.snapshot()["active_socket_connections"] == 0
    assert state.snapshot()["ready_for_retirement"] is True


def test_duplicate_socket_registration_does_not_inflate_count():
    state = BackendDrainState()

    assert state.try_register_socket("sid-1") is True
    assert state.try_register_socket("sid-1") is False
    assert state.snapshot()["active_socket_connections"] == 1

    assert state.release_socket("sid-1") is True
    assert state.release_socket("sid-1") is False
    assert state.snapshot()["active_socket_connections"] == 0


def test_inflight_request_can_finish_after_drain_begins(monkeypatch):
    app = make_app(monkeypatch)
    state = app.extensions[DRAIN_STATE_KEY]

    assert state.try_enter_http_request() is True
    state.begin_drain()

    snapshot = state.snapshot()
    assert snapshot["active_http_requests"] == 1
    assert snapshot["ready_for_retirement"] is False

    state.leave_http_request()
    snapshot = state.snapshot()
    assert snapshot["active_http_requests"] == 0
    assert snapshot["ready_for_retirement"] is True


def test_init_backend_drain_is_idempotent(monkeypatch):
    app = make_app(monkeypatch)
    first = app.extensions[DRAIN_STATE_KEY]
    second = init_backend_drain(app)

    assert first is second