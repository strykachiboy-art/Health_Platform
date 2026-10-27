from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.core.resilience.circuit_breaker_config import (
    CircuitBreakerSettings,
)


_CIRCUIT_NAME = re.compile(
    r"[a-z0-9][a-z0-9:._-]{0,127}\Z"
)

_KEY_PREFIX = "resilience:circuit:v1"


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class CircuitPermit:
    allowed: bool
    state: CircuitState
    generation: int
    probe_token: str | None = None
    retry_after_seconds: float | None = None
    store_available: bool = True
    emit_rejected_event: bool = False
    transitioned: bool = False


@dataclass(frozen=True)
class CircuitTransition:
    action: str
    previous_state: CircuitState
    new_state: CircuitState
    generation: int
    failure_count: int
    opened_until_ms: int | None
    changed: bool
    stale: bool = False


@dataclass(frozen=True)
class CircuitSnapshot:
    state: CircuitState
    generation: int
    failure_count: int
    opened_until_ms: int | None
    rejected_count: int
    ttl_seconds: int | None


def validate_circuit_name(name: object) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name != name.strip()
        or name != name.lower()
        or _CIRCUIT_NAME.fullmatch(name) is None
    ):
        raise ValueError(
            "Circuit name must be lowercase and contain only "
            "letters, numbers, ':', '.', '_' or '-'"
        )

    return name


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")

    return str(value)


def _integer(value: Any, *, field: str) -> int:
    try:
        return int(_text(value))
    except (TypeError, ValueError) as exc:
        raise CircuitBreakerStoreError(
            f"Invalid circuit breaker store field: {field}"
        ) from exc


def _state(value: Any) -> CircuitState:
    try:
        return CircuitState(_text(value))
    except ValueError as exc:
        raise CircuitBreakerStoreError(
            "Invalid circuit breaker state"
        ) from exc


_ACQUIRE_SCRIPT = r"""
local key = KEYS[1]
local probe_token = ARGV[1]
local probe_lease_ms = tonumber(ARGV[2])
local ttl_seconds = tonumber(ARGV[3])

local redis_time = redis.call('TIME')
local now_ms = (
    tonumber(redis_time[1]) * 1000
    + math.floor(tonumber(redis_time[2]) / 1000)
)

local state = redis.call('HGET', key, 'state')
local generation = tonumber(
    redis.call('HGET', key, 'generation') or '0'
)

if not state or state == 'closed' then
    return {
        1,
        'closed',
        generation,
        '',
        0,
        0,
        0
    }
end

if state == 'open' then
    local opened_until_ms = tonumber(
        redis.call('HGET', key, 'opened_until_ms') or '0'
    )

    if now_ms < opened_until_ms then
        redis.call('HINCRBY', key, 'rejected_count', 1)
        local emit_rejected = redis.call(
            'HSETNX',
            key,
            'rejection_event_emitted',
            '1'
        )
        redis.call('EXPIRE', key, ttl_seconds)

        return {
            0,
            'open',
            generation,
            '',
            opened_until_ms - now_ms,
            emit_rejected,
            0
        }
    end

    redis.call(
        'HSET',
        key,
        'state',
        'half_open',
        'probe_token',
        probe_token,
        'probe_until_ms',
        now_ms + probe_lease_ms
    )
    redis.call(
        'HDEL',
        key,
        'rejection_event_emitted'
    )
    redis.call('EXPIRE', key, ttl_seconds)

    return {
        1,
        'half_open',
        generation,
        probe_token,
        0,
        0,
        1
    }
end

if state == 'half_open' then
    local probe_until_ms = tonumber(
        redis.call('HGET', key, 'probe_until_ms') or '0'
    )

    if now_ms >= probe_until_ms then
        redis.call(
            'HSET',
            key,
            'probe_token',
            probe_token,
            'probe_until_ms',
            now_ms + probe_lease_ms
        )
        redis.call(
            'HDEL',
            key,
            'rejection_event_emitted'
        )
        redis.call('EXPIRE', key, ttl_seconds)

        return {
            1,
            'half_open',
            generation,
            probe_token,
            0,
            0,
            0
        }
    end

    redis.call('HINCRBY', key, 'rejected_count', 1)
    local emit_rejected = redis.call(
        'HSETNX',
        key,
        'rejection_event_emitted',
        '1'
    )
    redis.call('EXPIRE', key, ttl_seconds)

    return {
        0,
        'half_open',
        generation,
        '',
        probe_until_ms - now_ms,
        emit_rejected,
        0
    }
end

return redis.error_reply(
    'invalid circuit breaker state'
)
"""


_FAILURE_SCRIPT = r"""
local key = KEYS[1]
local permit_generation = tonumber(ARGV[1])
local permit_state = ARGV[2]
local probe_token = ARGV[3]
local failure_threshold = tonumber(ARGV[4])
local open_ms = tonumber(ARGV[5])
local ttl_seconds = tonumber(ARGV[6])

local redis_time = redis.call('TIME')
local now_ms = (
    tonumber(redis_time[1]) * 1000
    + math.floor(tonumber(redis_time[2]) / 1000)
)

local state = redis.call('HGET', key, 'state') or 'closed'
local generation = tonumber(
    redis.call('HGET', key, 'generation') or '0'
)
local failures = tonumber(
    redis.call('HGET', key, 'failures') or '0'
)

if generation ~= permit_generation then
    local opened_until_ms = tonumber(
        redis.call('HGET', key, 'opened_until_ms') or '0'
    )
    return {
        'stale',
        state,
        state,
        generation,
        failures,
        opened_until_ms,
        0
    }
end

if permit_state == 'half_open' then
    local current_probe_token = (
        redis.call('HGET', key, 'probe_token') or ''
    )

    if (
        state ~= 'half_open'
        or current_probe_token ~= probe_token
    ) then
        local opened_until_ms = tonumber(
            redis.call('HGET', key, 'opened_until_ms') or '0'
        )
        return {
            'stale',
            state,
            state,
            generation,
            failures,
            opened_until_ms,
            0
        }
    end

    local next_generation = generation + 1
    local opened_until_ms = now_ms + open_ms

    redis.call(
        'HSET',
        key,
        'state',
        'open',
        'generation',
        next_generation,
        'failures',
        failure_threshold,
        'opened_until_ms',
        opened_until_ms,
        'rejected_count',
        0
    )
    redis.call(
        'HDEL',
        key,
        'probe_token',
        'probe_until_ms',
        'rejection_event_emitted'
    )
    redis.call('EXPIRE', key, ttl_seconds)

    return {
        'opened',
        'half_open',
        'open',
        next_generation,
        failure_threshold,
        opened_until_ms,
        1
    }
end

if permit_state ~= 'closed' then
    return redis.error_reply(
        'invalid circuit permit state'
    )
end

if state ~= 'closed' then
    local opened_until_ms = tonumber(
        redis.call('HGET', key, 'opened_until_ms') or '0'
    )
    return {
        'stale',
        state,
        state,
        generation,
        failures,
        opened_until_ms,
        0
    }
end

failures = redis.call(
    'HINCRBY',
    key,
    'failures',
    1
)
redis.call(
    'HSET',
    key,
    'state',
    'closed',
    'generation',
    generation
)

if failures >= failure_threshold then
    local next_generation = generation + 1
    local opened_until_ms = now_ms + open_ms

    redis.call(
        'HSET',
        key,
        'state',
        'open',
        'generation',
        next_generation,
        'failures',
        failures,
        'opened_until_ms',
        opened_until_ms,
        'rejected_count',
        0
    )
    redis.call(
        'HDEL',
        key,
        'probe_token',
        'probe_until_ms',
        'rejection_event_emitted'
    )
    redis.call('EXPIRE', key, ttl_seconds)

    return {
        'opened',
        'closed',
        'open',
        next_generation,
        failures,
        opened_until_ms,
        1
    }
end

redis.call('EXPIRE', key, ttl_seconds)

return {
    'failure',
    'closed',
    'closed',
    generation,
    failures,
    0,
    0
}
"""


_SUCCESS_SCRIPT = r"""
local key = KEYS[1]
local permit_generation = tonumber(ARGV[1])
local permit_state = ARGV[2]
local probe_token = ARGV[3]
local ttl_seconds = tonumber(ARGV[4])

if redis.call('EXISTS', key) == 0 then
    if (
        permit_generation == 0
        and permit_state == 'closed'
    ) then
        return {
            'noop',
            'closed',
            'closed',
            0,
            0,
            0,
            0
        }
    end

    return {
        'stale',
        'closed',
        'closed',
        0,
        0,
        0,
        0
    }
end

local state = redis.call('HGET', key, 'state') or 'closed'
local generation = tonumber(
    redis.call('HGET', key, 'generation') or '0'
)
local failures = tonumber(
    redis.call('HGET', key, 'failures') or '0'
)
local opened_until_ms = tonumber(
    redis.call('HGET', key, 'opened_until_ms') or '0'
)

if generation ~= permit_generation then
    return {
        'stale',
        state,
        state,
        generation,
        failures,
        opened_until_ms,
        0
    }
end

if permit_state == 'half_open' then
    local current_probe_token = (
        redis.call('HGET', key, 'probe_token') or ''
    )

    if (
        state ~= 'half_open'
        or current_probe_token ~= probe_token
    ) then
        return {
            'stale',
            state,
            state,
            generation,
            failures,
            opened_until_ms,
            0
        }
    end

    redis.call(
        'HSET',
        key,
        'state',
        'closed',
        'generation',
        generation,
        'failures',
        0,
        'rejected_count',
        0
    )
    redis.call(
        'HDEL',
        key,
        'opened_until_ms',
        'probe_token',
        'probe_until_ms',
        'rejection_event_emitted'
    )
    redis.call('EXPIRE', key, ttl_seconds)

    return {
        'closed',
        'half_open',
        'closed',
        generation,
        0,
        0,
        1
    }
end

if permit_state ~= 'closed' then
    return redis.error_reply(
        'invalid circuit permit state'
    )
end

if state ~= 'closed' then
    return {
        'stale',
        state,
        state,
        generation,
        failures,
        opened_until_ms,
        0
    }
end

local changed = 0

if failures > 0 then
    changed = 1
end

redis.call(
    'HSET',
    key,
    'state',
    'closed',
    'generation',
    generation,
    'failures',
    0,
    'rejected_count',
    0
)
redis.call(
    'HDEL',
    key,
    'opened_until_ms',
    'probe_token',
    'probe_until_ms',
    'rejection_event_emitted'
)
redis.call('EXPIRE', key, ttl_seconds)

return {
    'closed',
    'closed',
    'closed',
    generation,
    0,
    0,
    changed
}
"""


class RedisCircuitBreakerStore:
    def __init__(
        self,
        *,
        redis_client,
        circuit_name: str,
    ) -> None:
        self.redis_client = redis_client
        self.circuit_name = validate_circuit_name(
            circuit_name
        )
        self.key = (
            f"{_KEY_PREFIX}:{self.circuit_name}"
        )

    def _eval(
        self,
        script: str,
        *args: object,
    ):
        if self.redis_client is None:
            raise CircuitBreakerStoreError(
                "Circuit breaker Redis client is unavailable"
            )

        try:
            return self.redis_client.eval(
                script,
                1,
                self.key,
                *args,
            )
        except Exception as exc:
            raise CircuitBreakerStoreError(
                "Circuit breaker Redis operation failed"
            ) from exc

    def acquire(
        self,
        settings: CircuitBreakerSettings,
    ) -> CircuitPermit:
        probe_token = secrets.token_urlsafe(24)

        raw = self._eval(
            _ACQUIRE_SCRIPT,
            probe_token,
            max(
                1,
                round(
                    settings.probe_lease_seconds
                    * 1000
                ),
            ),
            settings.state_ttl_seconds,
        )

        if not isinstance(raw, (list, tuple)) or len(raw) != 7:
            raise CircuitBreakerStoreError(
                "Circuit breaker admission response is invalid"
            )

        returned_probe = _text(raw[3]) or None
        retry_ms = _integer(
            raw[4],
            field="retry_after_ms",
        )

        return CircuitPermit(
            allowed=bool(
                _integer(raw[0], field="allowed")
            ),
            state=_state(raw[1]),
            generation=_integer(
                raw[2],
                field="generation",
            ),
            probe_token=returned_probe,
            retry_after_seconds=(
                retry_ms / 1000.0
                if retry_ms > 0
                else None
            ),
            store_available=True,
            emit_rejected_event=bool(
                _integer(
                    raw[5],
                    field="emit_rejected_event",
                )
            ),
            transitioned=bool(
                _integer(
                    raw[6],
                    field="transitioned",
                )
            ),
        )

    def record_failure(
        self,
        *,
        permit: CircuitPermit,
        settings: CircuitBreakerSettings,
    ) -> CircuitTransition:
        self._validate_permit(permit)

        raw = self._eval(
            _FAILURE_SCRIPT,
            permit.generation,
            permit.state.value,
            permit.probe_token or "",
            settings.failure_threshold,
            max(
                1,
                round(settings.open_seconds * 1000),
            ),
            settings.state_ttl_seconds,
        )

        return self._parse_transition(
            raw,
            operation="failure",
        )

    def record_success(
        self,
        *,
        permit: CircuitPermit,
        settings: CircuitBreakerSettings,
    ) -> CircuitTransition:
        self._validate_permit(permit)

        raw = self._eval(
            _SUCCESS_SCRIPT,
            permit.generation,
            permit.state.value,
            permit.probe_token or "",
            settings.state_ttl_seconds,
        )

        return self._parse_transition(
            raw,
            operation="success",
        )

    @staticmethod
    def _validate_permit(
        permit: CircuitPermit,
    ) -> None:
        if not isinstance(
            permit,
            CircuitPermit,
        ):
            raise TypeError(
                "permit must be a CircuitPermit"
            )

        if not permit.allowed:
            raise ValueError(
                "Denied circuit permits cannot record outcomes"
            )

        if (
            permit.state is CircuitState.HALF_OPEN
            and not permit.probe_token
        ):
            raise ValueError(
                "Half-open permits require a probe token"
            )

    def _parse_transition(
        self,
        raw,
        *,
        operation: str,
    ) -> CircuitTransition:
        if not isinstance(raw, (list, tuple)) or len(raw) != 7:
            raise CircuitBreakerStoreError(
                f"Circuit breaker {operation} response is invalid"
            )

        action = _text(raw[0])
        opened_until = _integer(
            raw[5],
            field="opened_until_ms",
        )

        return CircuitTransition(
            action=action,
            previous_state=_state(raw[1]),
            new_state=_state(raw[2]),
            generation=_integer(
                raw[3],
                field="generation",
            ),
            failure_count=_integer(
                raw[4],
                field="failure_count",
            ),
            opened_until_ms=(
                opened_until
                if opened_until > 0
                else None
            ),
            changed=bool(
                _integer(raw[6], field="changed")
            ),
            stale=(action == "stale"),
        )

    def snapshot(self) -> CircuitSnapshot:
        if self.redis_client is None:
            raise CircuitBreakerStoreError(
                "Circuit breaker Redis client is unavailable"
            )

        try:
            data = self.redis_client.hgetall(
                self.key
            )
            ttl = self.redis_client.ttl(
                self.key
            )
        except Exception as exc:
            raise CircuitBreakerStoreError(
                "Circuit breaker Redis operation failed"
            ) from exc

        if not data:
            return CircuitSnapshot(
                state=CircuitState.CLOSED,
                generation=0,
                failure_count=0,
                opened_until_ms=None,
                rejected_count=0,
                ttl_seconds=None,
            )

        normalized = {
            _text(key): _text(value)
            for key, value in data.items()
        }

        opened_until = int(
            normalized.get(
                "opened_until_ms",
                "0",
            )
        )

        return CircuitSnapshot(
            state=CircuitState(
                normalized.get(
                    "state",
                    CircuitState.CLOSED.value,
                )
            ),
            generation=int(
                normalized.get(
                    "generation",
                    "0",
                )
            ),
            failure_count=int(
                normalized.get(
                    "failures",
                    "0",
                )
            ),
            opened_until_ms=(
                opened_until
                if opened_until > 0
                else None
            ),
            rejected_count=int(
                normalized.get(
                    "rejected_count",
                    "0",
                )
            ),
            ttl_seconds=(
                int(ttl)
                if isinstance(ttl, int)
                and ttl >= 0
                else None
            ),
        )

    def delete(self) -> None:
        if self.redis_client is None:
            return

        try:
            self.redis_client.delete(
                self.key
            )
        except Exception as exc:
            raise CircuitBreakerStoreError(
                "Circuit breaker Redis operation failed"
            ) from exc
