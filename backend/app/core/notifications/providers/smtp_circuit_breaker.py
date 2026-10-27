from __future__ import annotations

import smtplib
import socket
import ssl
from collections.abc import Callable
from typing import Any, TypeVar

import dns.exception
import dns.resolver
from flask import current_app

from app import extensions
from app.core.exceptions import ValidationError
from app.core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
)
from app.core.resilience.circuit_breaker_config import (
    CircuitBreakerSettings,
)
from app.core.resilience.circuit_breaker_store import (
    CircuitPermit,
    RedisCircuitBreakerStore,
)


T = TypeVar("T")

SMTP_CIRCUIT_PROVIDER = "smtp"
SMTP_CIRCUIT_OPERATION = "send"
SMTP_PROVIDER_TIMEOUT_SECONDS = 30.0
SMTP_CIRCUIT_NAME = (
    f"{SMTP_CIRCUIT_PROVIDER}:{SMTP_CIRCUIT_OPERATION}"
)


def _validation_error_has_transient_dns_cause(
    error: ValidationError,
) -> bool:
    cause = error.__cause__

    if cause is None:
        return False

    if isinstance(cause, dns.resolver.NXDOMAIN):
        return False

    return isinstance(
        cause,
        (
            TimeoutError,
            ConnectionError,
            OSError,
            dns.exception.Timeout,
            dns.resolver.NoNameservers,
        ),
    )


def _recipients_refused_is_transient(
    error: smtplib.SMTPRecipientsRefused,
) -> bool:
    recipients = getattr(error, "recipients", None)

    if not isinstance(recipients, dict) or not recipients:
        return False

    codes: list[int] = []

    for details in recipients.values():
        if (
            not isinstance(details, tuple)
            or not details
            or isinstance(details[0], bool)
            or not isinstance(details[0], int)
        ):
            return False
        codes.append(details[0])

    return bool(codes) and all(
        400 <= code <= 499 for code in codes
    )


def classify_smtp_circuit_failure(
    error: Exception,
) -> str | None:
    if isinstance(error, ValidationError):
        if _validation_error_has_transient_dns_cause(error):
            return "dns_transport"
        return None

    if isinstance(error, smtplib.SMTPAuthenticationError):
        return None

    if isinstance(error, smtplib.SMTPRecipientsRefused):
        if _recipients_refused_is_transient(error):
            return "smtp_transient"
        return None

    if isinstance(error, smtplib.SMTPResponseException):
        smtp_code = getattr(error, "smtp_code", None)

        if (
            isinstance(smtp_code, int)
            and not isinstance(smtp_code, bool)
            and 400 <= smtp_code <= 499
        ):
            return "smtp_transient"

        return None

    if isinstance(
        error,
        (
            smtplib.SMTPServerDisconnected,
            socket.timeout,
            TimeoutError,
            ConnectionError,
            ssl.SSLError,
            OSError,
        ),
    ):
        return "transport"

    return None


def get_smtp_circuit() -> CircuitBreaker:
    settings = CircuitBreakerSettings.from_mapping(
        current_app.config
    )

    if (
        settings.enabled
        and settings.probe_lease_seconds
        <= SMTP_PROVIDER_TIMEOUT_SECONDS
    ):
        raise ValidationError(
            "CIRCUIT_BREAKER_PROBE_LEASE_SECONDS "
            "must exceed SMTP provider timeout"
        )

    store = RedisCircuitBreakerStore(
        redis_client=extensions.redis_client,
        circuit_name=SMTP_CIRCUIT_NAME,
    )

    return CircuitBreaker(
        name=SMTP_CIRCUIT_NAME,
        store=store,
        settings=settings,
        failure_classifier=classify_smtp_circuit_failure,
        provider=SMTP_CIRCUIT_PROVIDER,
        operation=SMTP_CIRCUIT_OPERATION,
    )


def acquire_smtp_circuit() -> tuple[
    CircuitBreaker,
    CircuitPermit,
]:
    breaker = get_smtp_circuit()
    permit = breaker.acquire()

    if not permit.allowed:
        raise CircuitBreakerOpenError(
            circuit_name=breaker.name,
            retry_after_seconds=permit.retry_after_seconds,
        )

    return breaker, permit


def call_smtp_with_permit(
    breaker: CircuitBreaker,
    permit: CircuitPermit,
    function: Callable[..., T],
    /,
    *args: Any,
    **kwargs: Any,
) -> T:
    try:
        result = function(*args, **kwargs)
    except Exception as exc:
        failure_class = classify_smtp_circuit_failure(exc)

        if failure_class is None:
            breaker.record_success(permit)
        else:
            breaker.record_failure(
                permit,
                failure_class=failure_class,
            )
        raise

    breaker.record_success(permit)
    return result
