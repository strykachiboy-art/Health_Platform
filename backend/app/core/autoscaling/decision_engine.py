from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ScalingAction(str, Enum):
    """Decision recommended by the policy engine."""

    HOLD = "hold"
    SCALE_OUT = "scale_out"
    SCALE_IN = "scale_in"


@dataclass(frozen=True, slots=True)
class AutoscalingPolicy:
    """Explicit policy inputs; values are not production-tuned defaults."""

    min_replicas: int
    max_replicas: int
    scale_out_load_ratio: float
    scale_in_load_ratio: float
    scale_out_window_seconds: float
    scale_in_window_seconds: float
    cooldown_seconds: float
    max_metrics_age_seconds: float
    max_observation_gap_seconds: float

    def __post_init__(self) -> None:
        if type(self.min_replicas) is not int or self.min_replicas < 1:
            raise ValueError("min_replicas must be an integer >= 1")
        if (
            type(self.max_replicas) is not int
            or self.max_replicas < self.min_replicas
        ):
            raise ValueError(
                "max_replicas must be an integer >= min_replicas"
            )

        scale_out = _finite_number(
            "scale_out_load_ratio", self.scale_out_load_ratio
        )
        scale_in = _finite_number(
            "scale_in_load_ratio", self.scale_in_load_ratio
        )
        if scale_in < 0 or scale_out <= scale_in:
            raise ValueError(
                "load-ratio thresholds must satisfy "
                "0 <= scale_in_load_ratio < scale_out_load_ratio"
            )

        for name in (
            "scale_out_window_seconds",
            "scale_in_window_seconds",
            "max_metrics_age_seconds",
            "max_observation_gap_seconds",
        ):
            value = _finite_number(name, getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")

        cooldown = _finite_number(
            "cooldown_seconds", self.cooldown_seconds
        )
        if cooldown < 0:
            raise ValueError("cooldown_seconds must be >= 0")


@dataclass(frozen=True, slots=True)
class ScalingObservation:
    """Timestamped demand observation supplied by a future metrics adapter."""

    current_replicas: int
    load_ratio: float | None
    observed_at: datetime | None
    healthy: bool = True


@dataclass(frozen=True, slots=True)
class ScalingDecision:
    """A recommendation only; this object never changes infrastructure."""

    action: ScalingAction
    current_replicas: int | None
    target_replicas: int | None
    reason: str
    load_ratio: float | None = None


class AutoscalingDecisionEngine:
    """Fail-safe scaling decisions with windows and acknowledgement semantics.

    A caller must acknowledge a recommendation using record_scale_result().
    Repeated calls therefore cannot issue duplicate recommendations while an
    actuator is still carrying out, or recovering from, a scaling attempt.
    """

    def __init__(self, policy: AutoscalingPolicy) -> None:
        if not isinstance(policy, AutoscalingPolicy):
            raise TypeError("policy must be an AutoscalingPolicy")

        self.policy = policy
        self._high_demand_since: datetime | None = None
        self._low_demand_since: datetime | None = None
        self._last_sample_at: datetime | None = None
        self._last_successful_scale_at: datetime | None = None
        self._pending_target_replicas: int | None = None
        self._pending_recommendation_at: datetime | None = None

    @property
    def pending_target_replicas(self) -> int | None:
        """Target awaiting an actuator result, if any."""
        return self._pending_target_replicas

    def evaluate(
        self,
        observation: ScalingObservation,
        *,
        evaluated_at: datetime,
    ) -> ScalingDecision:
        """Recommend scaling or HOLD; never perform infrastructure changes."""
        _require_aware_datetime("evaluated_at", evaluated_at)

        if not isinstance(observation, ScalingObservation):
            raise TypeError("observation must be a ScalingObservation")

        replicas = observation.current_replicas
        ratio_input = observation.load_ratio

        if type(replicas) is not int:
            return self._invalid("invalid_replica_count", None, None)

        if not (
            self.policy.min_replicas
            <= replicas
            <= self.policy.max_replicas
        ):
            return self._invalid(
                "replicas_out_of_bounds", replicas, ratio_input
            )

        if type(observation.healthy) is not bool:
            return self._invalid(
                "invalid_metrics_health", replicas, ratio_input
            )

        if not observation.healthy:
            return self._invalid(
                "metrics_unhealthy", replicas, ratio_input
            )

        if ratio_input is None:
            return self._invalid("missing_load_ratio", replicas, None)

        if not _is_finite_non_negative_number(ratio_input):
            return self._invalid("invalid_load_ratio", replicas, None)

        ratio = float(ratio_input)
        observed_at = observation.observed_at

        if observed_at is None:
            return self._invalid("missing_observed_at", replicas, ratio)

        if not _is_aware_datetime(observed_at):
            return self._invalid("invalid_observed_at", replicas, ratio)

        age_seconds = (evaluated_at - observed_at).total_seconds()

        if age_seconds < 0:
            return self._invalid("future_observation", replicas, ratio)

        if age_seconds > self.policy.max_metrics_age_seconds:
            return self._invalid("stale_metrics", replicas, ratio)

        # Never repeat a recommendation before the actuator reports a result.
        if self._pending_target_replicas is not None:
            return self._hold("scale_action_pending", replicas, ratio)

        if self._last_sample_at is not None:
            if observed_at == self._last_sample_at:
                return self._hold(
                    "duplicate_observation", replicas, ratio
                )

            if observed_at < self._last_sample_at:
                self._reset_streaks()
                return self._hold(
                    "non_monotonic_observation", replicas, ratio
                )

            gap = (
                observed_at - self._last_sample_at
            ).total_seconds()

            if gap > self.policy.max_observation_gap_seconds:
                # Sparse data cannot prove a continuous demand window.
                self._reset_streaks()

        self._last_sample_at = observed_at

        if self._last_successful_scale_at is not None:
            since_scale = (
                evaluated_at - self._last_successful_scale_at
            ).total_seconds()

            if since_scale < 0:
                self._reset_streaks()
                return self._hold(
                    "clock_before_last_scale", replicas, ratio
                )

        if ratio >= self.policy.scale_out_load_ratio:
            self._low_demand_since = None

            if self._high_demand_since is None:
                self._high_demand_since = observed_at

            sustained = (
                observed_at - self._high_demand_since
            ).total_seconds()

            if sustained < self.policy.scale_out_window_seconds:
                return self._hold(
                    "scale_out_window_not_met", replicas, ratio
                )

            if replicas >= self.policy.max_replicas:
                return self._hold(
                    "maximum_replicas_reached", replicas, ratio
                )

            if self._cooldown_active(evaluated_at):
                return self._hold("cooldown_active", replicas, ratio)

            return self._recommend(
                ScalingAction.SCALE_OUT,
                replicas,
                replicas + 1,
                "scale_out_recommended",
                ratio,
                evaluated_at,
            )

        if ratio <= self.policy.scale_in_load_ratio:
            self._high_demand_since = None

            if self._low_demand_since is None:
                self._low_demand_since = observed_at

            sustained = (
                observed_at - self._low_demand_since
            ).total_seconds()

            if sustained < self.policy.scale_in_window_seconds:
                return self._hold(
                    "scale_in_window_not_met", replicas, ratio
                )

            if replicas <= self.policy.min_replicas:
                return self._hold(
                    "minimum_replicas_reached", replicas, ratio
                )

            if self._cooldown_active(evaluated_at):
                return self._hold("cooldown_active", replicas, ratio)

            return self._recommend(
                ScalingAction.SCALE_IN,
                replicas,
                replicas - 1,
                "scale_in_recommended",
                ratio,
                evaluated_at,
            )

        # The hysteresis band separates scale-out from scale-in.
        self._reset_streaks()
        return self._hold("within_hysteresis_band", replicas, ratio)

    def record_scale_result(
        self,
        *,
        succeeded: bool,
        at: datetime,
    ) -> None:
        """Acknowledge a recommendation after the actuator reports its result.

        Set succeeded=True only after the actuator confirms the requested
        replica change was applied. A failed attempt does not start cooldown.
        Both outcomes reset the demand window before another recommendation.
        """
        _require_aware_datetime("at", at)

        if type(succeeded) is not bool:
            raise ValueError("succeeded must be a bool")

        if (
            self._pending_target_replicas is None
            or self._pending_recommendation_at is None
        ):
            raise RuntimeError(
                "there is no pending scale recommendation to acknowledge"
            )

        if at < self._pending_recommendation_at:
            raise ValueError(
                "scale result timestamp cannot precede its recommendation"
            )

        if succeeded:
            self._last_successful_scale_at = at

        self._pending_target_replicas = None
        self._pending_recommendation_at = None
        self._reset_streaks()

    def _cooldown_active(self, now: datetime) -> bool:
        if self._last_successful_scale_at is None:
            return False

        elapsed = (
            now - self._last_successful_scale_at
        ).total_seconds()

        return 0 <= elapsed < self.policy.cooldown_seconds

    def _recommend(
        self,
        action: ScalingAction,
        current: int,
        target: int,
        reason: str,
        ratio: float,
        at: datetime,
    ) -> ScalingDecision:
        self._pending_target_replicas = target
        self._pending_recommendation_at = at
        self._reset_streaks()

        return ScalingDecision(
            action, current, target, reason, ratio
        )

    def _invalid(
        self,
        reason: str,
        current: int | None,
        ratio: object,
    ) -> ScalingDecision:
        self._reset_streaks()
        self._last_sample_at = None

        safe_ratio = (
            float(ratio)
            if _is_finite_non_negative_number(ratio)
            else None
        )

        return self._hold(reason, current, safe_ratio)

    def _hold(
        self,
        reason: str,
        current: int | None,
        ratio: float | None,
    ) -> ScalingDecision:
        return ScalingDecision(
            ScalingAction.HOLD, current, current, reason, ratio
        )

    def _reset_streaks(self) -> None:
        self._high_demand_since = None
        self._low_demand_since = None


def _finite_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")

    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{name} must be a finite number") from None

    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")

    return number


def _is_finite_non_negative_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False

    try:
        number = float(value)
    except (OverflowError, ValueError):
        return False

    return math.isfinite(number) and number >= 0


def _is_aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _require_aware_datetime(name: str, value: object) -> None:
    if not _is_aware_datetime(value):
        raise ValueError(
            f"{name} must be a timezone-aware datetime"
        )
