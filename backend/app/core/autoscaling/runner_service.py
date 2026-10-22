"""Explicit host-process entry point for local Compose API autoscaling.

The service is disabled by default and never starts from Flask/Gunicorn.
Run it as one explicitly managed process on the Docker host. Its Redis URL
must connect to the same Redis instance used by the API replicas. Scale-in is
disabled until the retiring backend can be explicitly excluded from
proxy routing before drain. Health/readiness alone does not prove per-replica
routing withdrawal in the current Nginx config.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import redis

from app.core.autoscaling.api_controller import (
    APIBackendAutoscalingController,
    ComposeBackendReplicaReader,
)
from app.core.autoscaling.api_observation import (
    APIObservationAdapter,
    APIObservationConfig,
)
from app.core.autoscaling.compose_adapter import (
    ComposeScalingAdapter,
    ComposeScalingAdapterConfig,
    ENV_FILE_NAME,
)
from app.core.autoscaling.controller_lease import (
    AutoscalingControllerRunner,
    ControllerRunnerStatus,
    LeaseGuardedAcknowledgement,
    LeaseGuardedActuator,
    RedisControllerLease,
)
from app.core.autoscaling.coordinator import AutoscalingCoordinator
from app.core.autoscaling.decision_engine import AutoscalingDecisionEngine, AutoscalingPolicy
from app.core.autoscaling.live_actuator import (
    ComposeLiveScalingActuator,
    DockerExecBackendDrainAdapter,
)
from app.core.autoscaling.nginx_routing import NginxBackendRoutingAdapter
from app.core.autoscaling.reconciliation_loop import AutoscalingReconciliationLoop
from app.core.observability.api_request_metrics import API_REQUEST_METRICS_BUCKET_SECONDS


_LOG = logging.getLogger(__name__)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_PREFIX = "PHASE9_AUTOSCALING_"
_REQUIRED_FILES = (".env.phase6", "compose.yaml", "compose.phase6.yaml", "compose.phase9.yaml")


@dataclass(slots=True)
class AutoscalingRuntime:
    """Wired runtime resources; creation itself does not execute Docker commands."""

    runner: AutoscalingControllerRunner
    redis_client: Any
    replica_reader: ComposeBackendReplicaReader
    controller: APIBackendAutoscalingController
    coordinator: AutoscalingCoordinator
    actuator: ComposeLiveScalingActuator
    lease: RedisControllerLease
    project_name: str
    project_root: Path
    min_replicas: int
    max_replicas: int

    def close(self) -> None:
        close = getattr(self.redis_client, "close", None)
        if callable(close):
            close()


def _boolean(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
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


def _number(env: Mapping[str, str], name: str) -> float:
    raw = _required(env, name)
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if not __import__("math").isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _strong_drain_token_configured(env_file: Path) -> bool:
    """Check token presence without returning or logging its value."""
    try:
        content = env_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False

    matches = re.findall(
        r"(?m)^[ ]*PHASE9_BACKEND_DRAIN_TOKEN[ ]*=(.*)$",
        content,
    )
    if len(matches) != 1:
        return False

    value = matches[0].strip()
    if len(value) >= 2 and (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        value = value[1:-1]
    return len(value) >= 32


def build_runtime(
    environ: Mapping[str, str] | None = None,
    *,
    redis_client_factory: Callable[..., Any] | None = None,
) -> AutoscalingRuntime | None:
    """Build the complete API autoscaling graph only after explicit opt-in."""
    env = os.environ if environ is None else environ
    if not _boolean(env, _PREFIX + "ENABLED", default=False):
        return None
    if not _boolean(env, _PREFIX + "LIVE_ENABLED", default=False):
        raise RuntimeError("autoscaling is enabled but live actuation is not explicitly enabled")
    scale_in_enabled = _boolean(env, _PREFIX + "SCALE_IN_ENABLED", default=False)
    if scale_in_enabled and not _boolean(
        env, _PREFIX + "SCALE_IN_LIVE_ROUTE_VERIFIED", default=False
    ):
        raise RuntimeError(
            "scale-in requires PHASE9_AUTOSCALING_SCALE_IN_LIVE_ROUTE_VERIFIED=true "
            "after the live route-withdrawal test passes"
        )

    project_name = _required(env, _PREFIX + "PROJECT_NAME")
    redis_url = _required(env, _PREFIX + "REDIS_URL")
    project_root_value = env.get(_PREFIX + "PROJECT_ROOT", str(_REPOSITORY_ROOT))
    project_root = Path(project_root_value).resolve()
    if not project_root.is_dir():
        raise ValueError("autoscaling project root must be an existing directory")
    missing = [name for name in _REQUIRED_FILES if not (project_root / name).is_file()]
    if missing:
        raise ValueError("Compose project files are missing: " + ",".join(missing))
    routing_file = project_root / "runtime" / "phase9" / "nginx-upstream" / "backend.conf"
    if scale_in_enabled:
        if not _strong_drain_token_configured(project_root / ENV_FILE_NAME):
            raise RuntimeError(
                "scale-in requires a strong PHASE9_BACKEND_DRAIN_TOKEN in .env.phase6"
            )
        if not routing_file.is_file():
            raise RuntimeError(
                "scale-in requires the generated Nginx backend upstream file"
            )

    minimum = _integer(env, _PREFIX + "MIN_REPLICAS")
    maximum = _integer(env, _PREFIX + "MAX_REPLICAS")
    target_rps = _number(env, _PREFIX + "TARGET_REQUESTS_PER_SECOND_PER_REPLICA")
    scale_out_ratio = _number(env, _PREFIX + "SCALE_OUT_LOAD_RATIO")
    scale_in_ratio = _number(env, _PREFIX + "SCALE_IN_LOAD_RATIO")
    scale_out_window = _number(env, _PREFIX + "SCALE_OUT_WINDOW_SECONDS")
    scale_in_window = _number(env, _PREFIX + "SCALE_IN_WINDOW_SECONDS")
    cooldown = _number(env, _PREFIX + "COOLDOWN_SECONDS")
    metrics_max_age = _number(env, _PREFIX + "MAX_METRICS_AGE_SECONDS")
    observation_gap = _number(env, _PREFIX + "MAX_OBSERVATION_GAP_SECONDS")
    loop_interval = _number(env, _PREFIX + "LOOP_INTERVAL_SECONDS")
    metrics_window = _integer(env, _PREFIX + "METRICS_WINDOW_SECONDS")
    metrics_sample_age = _integer(env, _PREFIX + "METRICS_MAX_SAMPLE_AGE_SECONDS")

    # Constructors validate bounds, thresholds, cooldowns, and sampling-window invariants.
    policy = AutoscalingPolicy(
        min_replicas=minimum,
        max_replicas=maximum,
        scale_out_load_ratio=scale_out_ratio,
        scale_in_load_ratio=scale_in_ratio,
        scale_out_window_seconds=scale_out_window,
        scale_in_window_seconds=scale_in_window,
        cooldown_seconds=cooldown,
        max_metrics_age_seconds=metrics_max_age,
        max_observation_gap_seconds=observation_gap,
        allow_scale_in=scale_in_enabled,
    )
    observation_config = APIObservationConfig(
        target_requests_per_second_per_replica=target_rps,
        window_seconds=metrics_window,
        bucket_seconds=API_REQUEST_METRICS_BUCKET_SECONDS,
        max_sample_age_seconds=metrics_sample_age,
    )
    if loop_interval <= 0:
        raise ValueError("PHASE9_AUTOSCALING_LOOP_INTERVAL_SECONDS must be positive")

    factory = redis_client_factory or redis.Redis.from_url
    redis_client = factory(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
        health_check_interval=15,
    )
    try:
        decision_engine = AutoscalingDecisionEngine(policy)
        preview_adapter = ComposeScalingAdapter(
            ComposeScalingAdapterConfig(
                project_root=project_root,
                min_replicas=minimum,
                max_replicas=maximum,
                project_name=project_name,
            )
        )
        replica_reader = ComposeBackendReplicaReader(
            project_root=project_root,
            project_name=project_name,
        )
        observation_adapter = APIObservationAdapter(
            redis_client=redis_client,
            config=observation_config,
        )
        coordinator = AutoscalingCoordinator(decision_engine, preview_adapter)
        controller = APIBackendAutoscalingController(
            replica_reader=replica_reader,
            observation_adapter=observation_adapter,
            coordinator=coordinator,
        )
        lease = RedisControllerLease(
            redis_client,
            key=env.get(_PREFIX + "LEASE_KEY", "clinic-system:autoscaling:controller:lease"),
            ttl_seconds=_number(env, _PREFIX + "LEASE_TTL_SECONDS") if _PREFIX + "LEASE_TTL_SECONDS" in env else 45.0,
            renew_interval_seconds=_number(env, _PREFIX + "LEASE_RENEW_INTERVAL_SECONDS") if _PREFIX + "LEASE_RENEW_INTERVAL_SECONDS" in env else 10.0,
        )
        drain_adapter = (
            DockerExecBackendDrainAdapter(project_name=project_name)
            if scale_in_enabled else None
        )
        routing_adapter = (
            NginxBackendRoutingAdapter(
                project_name=project_name,
                upstream_file=routing_file,
            )
            if scale_in_enabled else None
        )
        actuator = ComposeLiveScalingActuator(
            preview_adapter,
            live_enabled=True,
            drain_adapter=drain_adapter,
            scale_in_enabled=scale_in_enabled,
            routing_adapter=routing_adapter,
            before_scale_command=lease.mark_scale_command_started,
        )
        # Keep cooldown across controller process restarts and explicit recovery.
        decision_engine.restore_last_successful_scale(
            lease.get_last_successful_scale_at()
        )
        guarded_actuator = LeaseGuardedActuator(actuator, lease)
        guarded_acknowledgement = LeaseGuardedAcknowledgement(
            coordinator.record_scale_result,
            lease,
        )
        reconciliation = AutoscalingReconciliationLoop(
            controller=controller,
            actuator=guarded_actuator,
            acknowledge_scale_result=guarded_acknowledgement,
            enabled=True,
            interval_seconds=loop_interval,
        )
        runner = AutoscalingControllerRunner(
            reconciliation,
            lease,
            enabled=True,
            interval_seconds=loop_interval,
        )
        return AutoscalingRuntime(
            runner=runner,
            redis_client=redis_client,
            replica_reader=replica_reader,
            controller=controller,
            coordinator=coordinator,
            actuator=actuator,
            lease=lease,
            project_name=project_name,
            project_root=project_root,
            min_replicas=minimum,
            max_replicas=maximum,
        )
    except Exception:
        close = getattr(redis_client, "close", None)
        if callable(close):
            close()
        raise


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("PHASE9_AUTOSCALING_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        runtime = build_runtime()
    except Exception as exc:
        _LOG.error("autoscaling runner configuration failed: %s", type(exc).__name__)
        return 2
    if runtime is None:
        _LOG.info("autoscaling runner disabled; no Redis or Docker calls were made")
        return 0

    stop_event = threading.Event()
    try:
        if runtime.redis_client.ping() is not True:
            raise RuntimeError("redis_ping_failed")
        snapshot = runtime.replica_reader.read()
        if (
            not snapshot.healthy
            or type(snapshot.current_replicas) is not int
            or not runtime.min_replicas <= snapshot.current_replicas <= runtime.max_replicas
        ):
            raise RuntimeError("backend_replica_preflight_failed")

        _LOG.info(
            "autoscaling preflight passed project=%s replicas=%s range=%s..%s scale_in=disabled",
            runtime.project_name,
            snapshot.current_replicas,
            runtime.min_replicas,
            runtime.max_replicas,
        )

        def request_stop(_signum, _frame) -> None:
            stop_event.set()

        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)

        result = runtime.runner.run_forever(stop_event)
        _LOG.info(
            "autoscaling runner exited status=%s cycles=%s lease_released=%s detail=%s",
            result.status.value,
            result.cycles,
            result.lease_released,
            result.detail or "none",
        )
        return 0 if result.status is ControllerRunnerStatus.STOPPED else 3
    except Exception as exc:
        _LOG.error("autoscaling runner stopped safely: %s", type(exc).__name__)
        return 3
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())