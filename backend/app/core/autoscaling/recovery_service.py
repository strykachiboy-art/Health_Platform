"""Operator-invoked recovery for a persisted autoscaling intent.

Nothing happens unless PHASE9_AUTOSCALING_RECOVERY_ENABLED=true and this module
is deliberately launched with ``python -m app.core.autoscaling.recovery_service``.
It does not run as part of Flask/Gunicorn or the normal autoscaling startup path.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import redis

from app.core.autoscaling.compose_adapter import (
    ComposeScalingAdapter,
    ComposeScalingAdapterConfig,
)
from app.core.autoscaling.controller_lease import RedisControllerLease
from app.core.autoscaling.live_actuator import (
    ComposeLiveScalingActuator,
    DockerExecBackendDrainAdapter,
)
from app.core.autoscaling.nginx_routing import NginxBackendRoutingAdapter
from app.core.autoscaling.pending_operation_recovery import PendingOperationRecovery

_LOG = logging.getLogger(__name__)
_PREFIX = "PHASE9_AUTOSCALING_"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_REQUIRED_FILES = (".env.phase6", "compose.yaml", "compose.phase6.yaml", "compose.phase9.yaml")


@dataclass(slots=True)
class RecoveryServiceRuntime:
    redis_client: Any
    lease: RedisControllerLease
    actuator: ComposeLiveScalingActuator

    def close(self) -> None:
        close = getattr(self.redis_client, "close", None)
        if callable(close):
            close()


def _boolean(env: Mapping[str, str], name: str) -> bool:
    raw = env.get(name, "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be an explicit boolean")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or not value.strip():
        raise ValueError(f"required setting is missing: {name}")
    return value.strip()


def _integer(env: Mapping[str, str], name: str) -> int:
    raw = _required(env, name)
    try:
        value = int(raw, 10)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    return value


def build_recovery_service(
    environ: Mapping[str, str] | None = None,
    *,
    redis_client_factory: Callable[..., Any] | None = None,
    command_runner: Callable = subprocess.run,
) -> RecoveryServiceRuntime:
    """Construct dependencies only after explicit recovery opt-in; no calls run here."""
    env = os.environ if environ is None else environ
    if not _boolean(env, _PREFIX + "RECOVERY_ENABLED"):
        raise RuntimeError("explicit pending-operation recovery opt-in is required")

    project_name = _required(env, _PREFIX + "PROJECT_NAME")
    redis_url = _required(env, _PREFIX + "REDIS_URL")
    root = Path(env.get(_PREFIX + "PROJECT_ROOT", str(_REPOSITORY_ROOT))).resolve()
    if not root.is_dir():
        raise ValueError("recovery project root must be an existing directory")
    missing = [name for name in _REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise ValueError("Compose project files are missing: " + ",".join(missing))
    routing_file = root / "runtime" / "phase9" / "nginx-upstream" / "backend.conf"
    if not routing_file.is_file():
        raise ValueError(
            "pending-operation recovery requires the generated Nginx backend upstream file"
        )

    minimum = _integer(env, _PREFIX + "MIN_REPLICAS")
    maximum = _integer(env, _PREFIX + "MAX_REPLICAS")
    if minimum < 1 or maximum < minimum:
        raise ValueError("replica bounds must satisfy 1 <= min_replicas <= max_replicas")

    factory = redis_client_factory or redis.Redis.from_url
    client = factory(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
        health_check_interval=15,
    )
    try:
        preview = ComposeScalingAdapter(
            ComposeScalingAdapterConfig(
                project_root=root,
                min_replicas=minimum,
                max_replicas=maximum,
                project_name=project_name,
            ),
            runner=command_runner,
        )
        drain = DockerExecBackendDrainAdapter(
            project_name=project_name,
            runner=command_runner,
        )
        routing = NginxBackendRoutingAdapter(
            project_name=project_name,
            upstream_file=routing_file,
            runner=command_runner,
        )
        lease = RedisControllerLease(
            client,
            key=env.get(_PREFIX + "LEASE_KEY", "clinic-system:autoscaling:controller:lease"),
            ttl_seconds=float(env.get(_PREFIX + "LEASE_TTL_SECONDS", "45")),
            renew_interval_seconds=float(env.get(_PREFIX + "LEASE_RENEW_INTERVAL_SECONDS", "10")),
        )
        actuator = ComposeLiveScalingActuator(
            preview,
            live_enabled=True,
            drain_adapter=drain,
            routing_adapter=routing,
            before_scale_command=lease.mark_scale_command_started,
            runner=command_runner,
        )
        return RecoveryServiceRuntime(client, lease, actuator)
    except Exception:
        close = getattr(client, "close", None)
        if callable(close):
            close()
        raise


def main(
    environ: Mapping[str, str] | None = None,
    *,
    redis_client_factory: Callable[..., Any] | None = None,
    command_runner: Callable = subprocess.run,
) -> int:
    env = os.environ if environ is None else environ
    try:
        enabled = _boolean(env, _PREFIX + "RECOVERY_ENABLED")
    except Exception as exc:
        _LOG.error("pending-operation recovery configuration failed: %s", type(exc).__name__)
        return 2
    if not enabled:
        _LOG.info("pending-operation recovery disabled; no Redis or Docker calls were made")
        return 0

    try:
        runtime = build_recovery_service(
            env,
            redis_client_factory=redis_client_factory,
            command_runner=command_runner,
        )
    except Exception as exc:
        _LOG.error("pending-operation recovery setup failed: %s", type(exc).__name__)
        return 2

    acquired = False
    try:
        if runtime.redis_client.ping() is not True:
            _LOG.error("pending-operation recovery Redis preflight failed")
            return 3
        acquired = bool(runtime.lease.acquire())
        if not acquired or not runtime.lease.is_owner():
            _LOG.error("pending-operation recovery could not obtain the controller lease")
            return 3
        result = PendingOperationRecovery(
            runtime.lease,
            runtime.actuator,
        ).run_once()
        _LOG.info(
            "pending_operation_recovery status=%s reason=%s operation_id=%s",
            result.status.value,
            result.reason,
            result.operation_id or "none",
        )
        return 0 if result.completed else 4
    except Exception as exc:
        _LOG.error("pending-operation recovery stopped safely: %s", type(exc).__name__)
        return 4
    finally:
        if acquired:
            runtime.lease.release()
        runtime.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("PHASE9_AUTOSCALING_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    raise SystemExit(main())
