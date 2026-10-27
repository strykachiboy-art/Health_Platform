from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from app.core.exceptions import (
    ServiceUnavailableError,
)
from app.core.observability.failure_events import (
    record_failure_event,
)
from app.core.resilience.circuit_breaker_config import (
    CircuitBreakerSettings,
)
from app.core.resilience.circuit_breaker_store import (
    CircuitBreakerStoreError,
    CircuitPermit,
    CircuitState,
    CircuitTransition,
    RedisCircuitBreakerStore,
    validate_circuit_name,
)


logger = logging.getLogger(__name__)

T = TypeVar("T")
FailureClassifier = Callable[[Exception], str | None]
EventRecorder = Callable[..., dict[str, Any] | None]


class CircuitBreakerOpenError(
    ServiceUnavailableError
):
    def __init__(
        self,
        *,
        circuit_name: str,
        retry_after_seconds: float | None,
    ) -> None:
        super().__init__(
            "Dependency is temporarily unavailable",
            code="dependency_circuit_open",
        )
        self.circuit_name = circuit_name
        self.retry_after_seconds = (
            retry_after_seconds
        )


class CircuitBreaker:
    def __init__(
        self,
        *,
        name: str,
        store: RedisCircuitBreakerStore | None,
        settings: CircuitBreakerSettings,
        failure_classifier: FailureClassifier | None = None,
        provider: str | None = None,
        operation: str | None = None,
        event_recorder: EventRecorder | None = (
            record_failure_event
        ),
    ) -> None:
        self.name = validate_circuit_name(name)
        self.store = store
        self.settings = settings
        self.failure_classifier = (
            failure_classifier
            or self._default_failure_classifier
        )
        self.provider = provider
        self.operation = operation
        self.event_recorder = event_recorder

        if (
            store is not None
            and store.circuit_name != self.name
        ):
            raise ValueError(
                "Circuit breaker store name does not "
                "match circuit name"
            )

    @staticmethod
    def _default_failure_classifier(
        error: Exception,
    ) -> str:
        return type(error).__name__

    def acquire(self) -> CircuitPermit:
        if not self.settings.enabled:
            return CircuitPermit(
                allowed=True,
                state=CircuitState.CLOSED,
                generation=0,
                store_available=True,
            )

        if self.store is None:
            return self._degraded_permit(
                operation="acquire",
            )

        try:
            permit = self.store.acquire(
                self.settings
            )
        except CircuitBreakerStoreError:
            return self._degraded_permit(
                operation="acquire",
            )

        if (
            permit.transitioned
            and permit.state
            is CircuitState.HALF_OPEN
        ):
            self._emit_event(
                event_type="circuit.half_open",
                severity="warning",
                previous_state=(
                    CircuitState.OPEN
                ),
                new_state=(
                    CircuitState.HALF_OPEN
                ),
                generation=permit.generation,
            )

        if (
            not permit.allowed
            and permit.emit_rejected_event
        ):
            self._emit_event(
                event_type="circuit.rejected",
                severity="warning",
                previous_state=permit.state,
                new_state=permit.state,
                generation=permit.generation,
            )

        return permit

    def record_success(
        self,
        permit: CircuitPermit,
    ) -> CircuitTransition | None:
        if (
            not self.settings.enabled
            or not permit.store_available
            or self.store is None
        ):
            return None

        try:
            transition = (
                self.store.record_success(
                    permit=permit,
                    settings=self.settings,
                )
            )
        except CircuitBreakerStoreError:
            self._record_store_degraded(
                operation="success",
            )
            return None

        if (
            transition.changed
            and transition.previous_state
            is CircuitState.HALF_OPEN
            and transition.new_state
            is CircuitState.CLOSED
        ):
            self._emit_transition(
                "circuit.closed",
                "info",
                transition,
            )

        return transition

    def record_failure(
        self,
        permit: CircuitPermit,
        *,
        failure_class: str,
    ) -> CircuitTransition | None:
        if not isinstance(
            failure_class,
            str,
        ) or not failure_class.strip():
            raise ValueError(
                "failure_class must be a non-empty string"
            )

        if (
            not self.settings.enabled
            or not permit.store_available
            or self.store is None
        ):
            return None

        try:
            transition = (
                self.store.record_failure(
                    permit=permit,
                    settings=self.settings,
                )
            )
        except CircuitBreakerStoreError:
            self._record_store_degraded(
                operation="failure",
            )
            return None

        if (
            transition.changed
            and transition.new_state
            is CircuitState.OPEN
        ):
            self._emit_transition(
                "circuit.opened",
                "error",
                transition,
                failure_class=failure_class,
            )

        return transition

    def call(
        self,
        function: Callable[..., T],
        /,
        *args,
        **kwargs,
    ) -> T:
        permit = self.acquire()

        if not permit.allowed:
            raise CircuitBreakerOpenError(
                circuit_name=self.name,
                retry_after_seconds=(
                    permit.retry_after_seconds
                ),
            )

        try:
            result = function(
                *args,
                **kwargs,
            )
        except Exception as exc:
            try:
                failure_class = (
                    self.failure_classifier(exc)
                )
            except Exception:
                logger.exception(
                    "Circuit breaker failure classifier "
                    "failed for %s",
                    self.name,
                )
                failure_class = "unclassified"

            if failure_class is None:
                self.record_success(
                    permit
                )
            else:
                normalized_class = getattr(
                    failure_class,
                    "value",
                    failure_class,
                )

                if (
                    not isinstance(
                        normalized_class,
                        str,
                    )
                    or not normalized_class.strip()
                ):
                    logger.warning(
                        "Circuit breaker failure classifier "
                        "returned an invalid value for %s",
                        self.name,
                    )
                    normalized_class = "unclassified"

                self.record_failure(
                    permit,
                    failure_class=(
                        normalized_class.strip()
                    ),
                )

            raise

        self.record_success(
            permit
        )
        return result

    def _degraded_permit(
        self,
        *,
        operation: str,
    ) -> CircuitPermit:
        self._record_store_degraded(
            operation=operation,
        )

        return CircuitPermit(
            allowed=True,
            state=CircuitState.CLOSED,
            generation=0,
            store_available=False,
        )

    def _record_store_degraded(
        self,
        *,
        operation: str,
    ) -> None:
        logger.warning(
            "Circuit breaker state store degraded "
            "for circuit=%s operation=%s; "
            "provider call will proceed",
            self.name,
            operation,
        )

        self._emit_event(
            event_type=(
                "circuit.state_store_degraded"
            ),
            severity="warning",
            previous_state=None,
            new_state=None,
            generation=None,
            error_type=(
                "CircuitBreakerStoreError"
            ),
        )

    def _emit_transition(
        self,
        event_type: str,
        severity: str,
        transition: CircuitTransition,
        *,
        failure_class: str | None = None,
    ) -> None:
        self._emit_event(
            event_type=event_type,
            severity=severity,
            previous_state=(
                transition.previous_state
            ),
            new_state=transition.new_state,
            generation=transition.generation,
            failure_count=(
                transition.failure_count
            ),
            opened_until_ms=(
                transition.opened_until_ms
            ),
            failure_class=failure_class,
        )

    def _emit_event(
        self,
        *,
        event_type: str,
        severity: str,
        previous_state: CircuitState | None,
        new_state: CircuitState | None,
        generation: int | None,
        failure_count: int | None = None,
        opened_until_ms: int | None = None,
        failure_class: str | None = None,
        error_type: str | None = None,
    ) -> None:
        if (
            self.event_recorder is None
            or self.store is None
            or self.store.redis_client is None
        ):
            return

        try:
            self.event_recorder(
                redis_client=(
                    self.store.redis_client
                ),
                event_type=event_type,
                component=(
                    f"circuit:{self.name}"
                ),
                severity=severity,
                error_type=error_type,
                circuit_name=self.name,
                provider=self.provider,
                operation=self.operation,
                previous_state=(
                    previous_state.value
                    if previous_state is not None
                    else None
                ),
                new_state=(
                    new_state.value
                    if new_state is not None
                    else None
                ),
                generation=generation,
                failure_count=failure_count,
                failure_threshold=(
                    self.settings.failure_threshold
                ),
                opened_until_ms=(
                    opened_until_ms
                ),
                failure_class=failure_class,
            )
        except Exception:
            logger.warning(
                "Circuit breaker observability event "
                "could not be recorded for %s",
                self.name,
            )
