from __future__ import annotations

from threading import RLock

from flask import current_app, g, jsonify, request


DRAIN_STATE_KEY = "_clinic_backend_drain_state"

# Liveness, readiness, and authenticated control endpoints remain reachable
# while normal application traffic is being drained.
_EXEMPT_PATHS = frozenset(
    {
        "/health/live",
        "/health/ready",
        "/health/drain-status",
        "/health/drain",
        "/health/undrain",
    }
)


class BackendDrainState:
    """Thread-safe, process-local admission and in-flight connection state."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._draining = False
        self._active_http_requests = 0
        self._active_socket_ids: set[str] = set()

    def try_enter_http_request(self) -> bool:
        with self._lock:
            if self._draining:
                return False

            self._active_http_requests += 1
            return True

    def leave_http_request(self) -> None:
        with self._lock:
            # Teardown hooks may run on exceptions; never let a duplicate
            # teardown corrupt the counter below zero.
            if self._active_http_requests > 0:
                self._active_http_requests -= 1

    def try_register_socket(self, sid: str) -> bool:
        if not isinstance(sid, str) or not sid.strip():
            return False

        with self._lock:
            if self._draining or sid in self._active_socket_ids:
                return False

            self._active_socket_ids.add(sid)
            return True

    def release_socket(self, sid: str) -> bool:
        if not isinstance(sid, str):
            return False

        with self._lock:
            if sid not in self._active_socket_ids:
                return False

            self._active_socket_ids.remove(sid)
            return True

    def begin_drain(self) -> dict[str, object]:
        with self._lock:
            self._draining = True
            return self._snapshot_locked()

    def cancel_drain(self) -> dict[str, object]:
        with self._lock:
            self._draining = False
            return self._snapshot_locked()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, object]:
        sockets = len(self._active_socket_ids)
        ready = (
            self._draining
            and self._active_http_requests == 0
            and sockets == 0
        )
        return {
            "draining": self._draining,
            "active_http_requests": self._active_http_requests,
            "active_socket_connections": sockets,
            "ready_for_retirement": ready,
        }


def get_backend_drain_state() -> BackendDrainState | None:
    try:
        state = current_app.extensions.get(DRAIN_STATE_KEY)
    except RuntimeError:
        return None

    return state if isinstance(state, BackendDrainState) else None


def reserve_socket_connection(sid: str) -> bool:
    """Register a connection only if this instance has not begun draining."""
    state = get_backend_drain_state()
    return state.try_register_socket(sid) if state is not None else False


def release_socket_connection(sid: str) -> bool:
    """Release a previously registered Socket.IO connection, idempotently."""
    state = get_backend_drain_state()
    return state.release_socket(sid) if state is not None else False


def init_backend_drain(app) -> BackendDrainState:
    """Register request admission and teardown accounting on a Flask app."""
    existing = app.extensions.get(DRAIN_STATE_KEY)
    if isinstance(existing, BackendDrainState):
        return existing

    state = BackendDrainState()
    app.extensions[DRAIN_STATE_KEY] = state

    @app.before_request
    def _backend_drain_admission():
        if request.path in _EXEMPT_PATHS:
            return None

        if state.try_enter_http_request():
            g._clinic_backend_drain_counted = True
            return None

        response = jsonify(
            {
                "success": False,
                "error": "backend_draining",
                "message": "This backend is temporarily not accepting new requests.",
            }
        )
        response.status_code = 503
        response.headers["Retry-After"] = "1"
        return response

    @app.teardown_request
    def _backend_drain_request_finished(_error=None):
        if getattr(g, "_clinic_backend_drain_counted", False):
            g._clinic_backend_drain_counted = False
            state.leave_http_request()

    return state