"""CT4B2A: physically interrupt a dedicated Redis 8 container, never shared Redis.

One real Docker stop/start, one Flask testing DB, one durable case-chat
request. This is NOT a Celery worker-crash proof. Those need separate,
persistent-Postgres-backed worker orchestration (CT4B2B).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from urllib.parse import urlsplit

import pytest
import redis

from app.core.enums.role_enums import Role
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.services import case_chat_assignment_event_service as events
from app.modules.chat.services import case_conversation_creation_service as creation
from app.modules.chat.services import case_chat_membership_sync_service as sync
from app.modules.chat.tasks import case_chat_sync_task as worker
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.services import care_team_assignment_service as assignment
from app.tests.modules.chat.test_case_chat_scheduled_retry import (
    _case,
    _assign,
)


def _docker(*args: str) -> str:
    completed = subprocess.run(
        ["docker", *args], check=True, capture_output=True,
        text=True, timeout=30,
    )
    return completed.stdout.strip()


def _owned_redis() -> tuple[str, redis.Redis]:
    container = os.environ.get("CT4B2A_REDIS_CONTAINER", "")
    guard = os.environ.get("CT4B2A_OWNER_GUARD", "")
    url = os.environ.get("TEST_REDIS_URL", "")
    assert container.startswith("ct4b2a-redis-")
    assert len(guard) >= 16
    parts = urlsplit(url)
    assert parts.scheme == "redis"
    assert parts.hostname == "127.0.0.1"
    assert parts.path == "/15"
    assert isinstance(parts.port, int) and parts.port > 0
    metadata = json.loads(_docker("inspect", container))
    assert len(metadata) == 1
    info = metadata[0]
    assert info["Name"] == "/" + container
    assert info["Config"]["Labels"].get("org.openai.ct4b2a.owner") == guard
    assert info["State"]["Running"] is True
    ports = info["NetworkSettings"]["Ports"]["6379/tcp"]
    assert len(ports) == 1
    assert ports[0]["HostIp"] == "127.0.0.1"
    assert int(ports[0]["HostPort"]) == parts.port
    client = redis.Redis.from_url(
        url, socket_connect_timeout=0.8, socket_timeout=0.8,
        retry_on_timeout=False,
    )
    assert client.ping()
    return container, client


def _pending(ids) -> AdmissionCaseChatSyncRequest:
    row = db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == ids["clinic"],
            AdmissionCaseChatSyncRequest.admission_id == ids["admission"],
        )
    ).scalar_one()
    return row


def _ready_after_restart(
    client: redis.Redis, container: str, expected_port: int,
    guard: str,
) -> None:
    """Verify Docker is running, the original published port, and host Redis.

    The host port is explicitly pinned by the PowerShell runner. We require
    the *same* owned container, its exact loopback port, Redis readiness inside
    Docker, and a fresh client connection from the Windows Python process.
    """
    last_state = "no-attempt"
    for attempt in range(90):
        try:
            inspected = json.loads(_docker("inspect", container))
            assert len(inspected) == 1
            metadata = inspected[0]
            if metadata["Name"] != "/" + container:
                pytest.fail("CT4B2A_RESTART_CONTAINER_NAME_MISMATCH")
            if metadata["Config"]["Labels"].get("org.openai.ct4b2a.owner") != guard:
                pytest.fail("CT4B2A_RESTART_OWNER_LABEL_MISMATCH")
            state = metadata["State"]
            ports = metadata["NetworkSettings"]["Ports"] or {}
            mapping = ports.get("6379/tcp") or []
            port_ok = (
                len(mapping) == 1
                and mapping[0]["HostIp"] == "127.0.0.1"
                and int(mapping[0]["HostPort"]) == expected_port
            )
            last_state = (
                f"running={state['Running']} status={state.get('Status')} "
                f"exit={state.get('ExitCode')} mapping={mapping!r}"
            )
            if state["Running"] and port_ok:
                # Also require the Redis process, not only Docker, to be ready.
                if _docker("exec", container, "redis-cli", "-n", "15", "ping") == "PONG":
                    client.connection_pool.disconnect()  # discard pre-stop sockets
                    if client.ping():
                        print("CT4B2A_RESTART_DOCKER_RUNNING=PASS", flush=True)
                        print("CT4B2A_RESTART_LOOPBACK_PORT_UNCHANGED=PASS", flush=True)
                        print("CT4B2A_RESTART_REDIS_HOST_AND_CONTAINER_PING=PASS", flush=True)
                        return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                redis.exceptions.ConnectionError, redis.exceptions.TimeoutError,
                KeyError, ValueError, TypeError, AssertionError) as exc:
            last_state = f"{type(exc).__name__}: {str(exc)[:250]}"
        time.sleep(0.5)
    print("CT4B2A_RESTART_LAST_STATE=" + last_state, flush=True)
    try:
        print("CT4B2A_RESTART_DOCKER_LOG_TAIL=" + _docker("logs", "--tail", "12", container)[-1000:], flush=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("CT4B2A_RESTART_DOCKER_LOG_UNAVAILABLE", flush=True)
    pytest.fail("Owned Redis failed to recover at its pinned 127.0.0.1 host port")


def test_real_redis_stop_fails_closed_jwt_preserves_clinical_queue_and_recovers(
    client, clinic, make_patient, make_staff, auth_headers_for, monkeypatch,
):
    """The CLI stop/start is real; the chat outage is deliberately simulated.

    The actual Celery worker is *not* killed. Recovery calls the existing task
    synchronously after Redis returns, and should not be described otherwise.
    """
    for module in (assignment, creation, sync):
        monkeypatch.setattr(module, "create_audit_log", lambda **_kw: None)

    container, redis_client = _owned_redis()
    ids = _case(clinic, make_patient, make_staff)
    nurse = make_staff(clinic, role=Role.NURSE)
    db.session.commit()
    headers = auth_headers_for(nurse.user, role=Role.NURSE)
    test_url = "/api/v1/nursing/handoffs/999999999"

    before = client.get(test_url, headers=headers)
    assert before.status_code == 404, before.get_data(as_text=True)[:200]

    original_create = events.ensure_admission_case_conversation

    def chat_unavailable(**_kwargs):
        raise RuntimeError("CT4B2A injected chat delivery error")

    monkeypatch.setattr(events, "ensure_admission_case_conversation", chat_unavailable)
    assigned = _assign(ids)
    assert db.session.get(AdmissionCareAssignment, assigned.id) is not None
    assert (_pending(ids).request_sequence, _pending(ids).processed_sequence) == (1, 0)
    assert db.session.query(AdmissionCaseConversation).count() == 0

    print("CT4B2A_REAL_REDIS_STOP=START", flush=True)
    _docker("stop", "--timeout=2", container)
    print("CT4B2A_OWNED_REDIS_STOP_COMMAND=PASS", flush=True)
    try:
        with pytest.raises((redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)):
            redis_client.ping()
        during = client.get(test_url, headers=headers)
        assert during.status_code == 401, during.get_data(as_text=True)[:200]
        db.session.expire_all()
        assert db.session.get(AdmissionCareAssignment, assigned.id) is not None
        assert (_pending(ids).request_sequence, _pending(ids).processed_sequence) == (1, 0)
        print("CT4B2A_AUTH_FAIL_CLOSED_DURING_REAL_OUTAGE=PASS", flush=True)
        print("CT4B2A_DURABLE_CASE_CHAT_QUEUE_SURVIVES_OUTAGE=PASS", flush=True)
    finally:
        started = _docker("start", container)
        assert started == container, f"CT4B2A_RESTART_WRONG_CONTAINER={started}"
        _ready_after_restart(
            redis_client, container, urlsplit(os.environ["TEST_REDIS_URL"]).port,
            os.environ["CT4B2A_OWNER_GUARD"],
        )
        print("CT4B2A_REAL_REDIS_RESTART=PASS", flush=True)

    recovered = client.get(test_url, headers=headers)
    assert recovered.status_code == 404, recovered.get_data(as_text=True)[:200]
    monkeypatch.setattr(events, "ensure_admission_case_conversation", original_create)
    outcome = worker.process_pending_case_chat_sync.run(limit=10)
    assert outcome == {"claimed": 1, "processed": 1, "failed": 0, "still_pending": 0}
    assert (_pending(ids).request_sequence, _pending(ids).processed_sequence) == (1, 1)
    assert db.session.query(AdmissionCaseConversation).count() == 1
    assert db.session.query(AdmissionCareAssignment).count() == 1
    print("CT4B2A_CASE_CHAT_RETRY_AFTER_REDIS_RECOVERY=PASS", flush=True)
