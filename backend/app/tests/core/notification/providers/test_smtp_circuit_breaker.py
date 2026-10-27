from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, Mock

import pytest

from app import extensions
from app.core.enums.notification_enums import (
    NotificationChannel,
    NotificationStatus,
)
from app.core.exceptions import ValidationError
from app.core.notifications.providers import email_provider
from app.core.notifications.providers.email_provider import (
    EmailNotificationProvider,
)
from app.core.notifications.providers.smtp_circuit_breaker import (
    SMTP_CIRCUIT_NAME,
    SMTP_PROVIDER_TIMEOUT_SECONDS,
    classify_smtp_circuit_failure,
    get_smtp_circuit,
)
from app.core.notifications.services import notification_service
from app.core.resilience.circuit_breaker import (
    CircuitBreakerOpenError,
)
from app.core.resilience.circuit_breaker_store import (
    CircuitState,
    RedisCircuitBreakerStore,
)
from app.extensions import db


def _configure(app):
    app.config.update(
        CIRCUIT_BREAKER_ENABLED=True,
        CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
        CIRCUIT_BREAKER_OPEN_SECONDS=30,
        CIRCUIT_BREAKER_PROBE_LEASE_SECONDS=60,
        CIRCUIT_BREAKER_STATE_TTL_SECONDS=3600,
    )


def _store():
    return RedisCircuitBreakerStore(
        redis_client=extensions.redis_client,
        circuit_name=SMTP_CIRCUIT_NAME,
    )


@pytest.fixture
def clean_smtp_circuit(app):
    with app.app_context():
        _store().delete()

    yield

    with app.app_context():
        _store().delete()


def _provider():
    return EmailNotificationProvider(
        credentials={
            "host": "smtp.example.com",
            "port": 587,
            "username": "mailer@example.com",
            "password": "secret",
            "from_email": "mailer@example.com",
            "use_tls": True,
            "use_ssl": False,
        }
    )


def test_smtp_failure_classifier_contract():
    assert (
        classify_smtp_circuit_failure(
            smtplib.SMTPServerDisconnected("down")
        )
        == "transport"
    )

    assert (
        classify_smtp_circuit_failure(
            smtplib.SMTPDataError(451, b"temporary")
        )
        == "smtp_transient"
    )

    assert (
        classify_smtp_circuit_failure(
            smtplib.SMTPDataError(550, b"permanent")
        )
        is None
    )

    assert (
        classify_smtp_circuit_failure(
            smtplib.SMTPAuthenticationError(
                535,
                b"bad credentials",
            )
        )
        is None
    )

    policy_error = ValidationError(
        "SMTP destination hostname is not approved"
    )
    assert classify_smtp_circuit_failure(policy_error) is None

    dns_error = ValidationError(
        "SMTP destination DNS lookup timed out"
    )
    dns_error.__cause__ = TimeoutError("dns timeout")
    assert (
        classify_smtp_circuit_failure(dns_error)
        == "dns_transport"
    )


def test_smtp_open_short_circuits_before_dns_and_connection(
    app,
    clean_smtp_circuit,
    make_notification,
    clinic,
    user,
    monkeypatch,
):
    notification = make_notification(
        clinic_id=clinic.id,
        user_id=user.id,
        channel=NotificationChannel.EMAIL,
    )

    resolver = Mock(
        return_value=("smtp.example.com", "8.8.8.8")
    )
    smtp_class = MagicMock()
    smtp = smtp_class.return_value.__enter__.return_value
    smtp.send_message.side_effect = (
        smtplib.SMTPServerDisconnected(
            "smtp disconnected"
        )
    )

    monkeypatch.setattr(
        email_provider,
        "resolve_approved_smtp_destination",
        resolver,
    )
    monkeypatch.setattr(
        email_provider,
        "PinnedSMTP",
        smtp_class,
    )

    with app.app_context():
        _configure(app)
        provider = _provider()

        with pytest.raises(
            smtplib.SMTPServerDisconnected,
        ):
            provider.send(
                notification=notification,
                email="patient@example.com",
                idempotency_key="smtp-cb-1",
            )

        assert _store().snapshot().state is CircuitState.OPEN

        with pytest.raises(CircuitBreakerOpenError):
            provider.send(
                notification=notification,
                email="patient@example.com",
                idempotency_key="smtp-cb-1",
            )

    assert resolver.call_count == 1
    assert smtp_class.call_count == 1
    assert smtp.send_message.call_count == 1


def test_smtp_permanent_550_does_not_open(
    app,
    clean_smtp_circuit,
    make_notification,
    clinic,
    user,
    monkeypatch,
):
    notification = make_notification(
        clinic_id=clinic.id,
        user_id=user.id,
        channel=NotificationChannel.EMAIL,
    )

    resolver = Mock(
        return_value=("smtp.example.com", "8.8.8.8")
    )
    smtp_class = MagicMock()
    smtp = smtp_class.return_value.__enter__.return_value
    smtp.send_message.side_effect = smtplib.SMTPDataError(
        550,
        b"permanent rejection",
    )

    monkeypatch.setattr(
        email_provider,
        "resolve_approved_smtp_destination",
        resolver,
    )
    monkeypatch.setattr(
        email_provider,
        "PinnedSMTP",
        smtp_class,
    )

    with app.app_context():
        _configure(app)
        provider = _provider()

        for _ in range(2):
            with pytest.raises(smtplib.SMTPDataError):
                provider.send(
                    notification=notification,
                    email="patient@example.com",
                    idempotency_key="smtp-cb-2",
                )

        snapshot = _store().snapshot()
        assert snapshot.state is CircuitState.CLOSED
        assert snapshot.failure_count == 0

    assert resolver.call_count == 2
    assert smtp_class.call_count == 2


def test_smtp_policy_validation_does_not_open(
    app,
    clean_smtp_circuit,
    make_notification,
    clinic,
    user,
    monkeypatch,
):
    notification = make_notification(
        clinic_id=clinic.id,
        user_id=user.id,
        channel=NotificationChannel.EMAIL,
    )

    resolver = Mock(
        side_effect=ValidationError(
            "SMTP destination hostname is not approved"
        )
    )

    monkeypatch.setattr(
        email_provider,
        "resolve_approved_smtp_destination",
        resolver,
    )

    with app.app_context():
        _configure(app)
        provider = _provider()

        for _ in range(2):
            with pytest.raises(
                ValidationError,
                match="hostname is not approved",
            ):
                provider.send(
                    notification=notification,
                    email="patient@example.com",
                )

        snapshot = _store().snapshot()
        assert snapshot.state is CircuitState.CLOSED
        assert snapshot.failure_count == 0

    assert resolver.call_count == 2


def test_smtp_redis_failure_fails_open(
    app,
    make_notification,
    clinic,
    user,
    monkeypatch,
):
    class BrokenRedis:
        def eval(self, *args, **kwargs):
            raise ConnectionError("redis unavailable")

        def lpush(self, *args, **kwargs):
            raise ConnectionError("redis unavailable")

    notification = make_notification(
        clinic_id=clinic.id,
        user_id=user.id,
        channel=NotificationChannel.EMAIL,
    )

    resolver = Mock(
        return_value=("smtp.example.com", "8.8.8.8")
    )
    smtp_class = MagicMock()

    monkeypatch.setattr(
        email_provider,
        "resolve_approved_smtp_destination",
        resolver,
    )
    monkeypatch.setattr(
        email_provider,
        "PinnedSMTP",
        smtp_class,
    )

    with app.app_context():
        _configure(app)
        monkeypatch.setattr(
            extensions,
            "redis_client",
            BrokenRedis(),
        )

        assert _provider().send(
            notification=notification,
            email="patient@example.com",
        ) is True

    resolver.assert_called_once()
    smtp_class.assert_called_once()


def test_smtp_probe_lease_must_exceed_provider_timeout(app):
    with app.app_context():
        _configure(app)
        app.config[
            "CIRCUIT_BREAKER_PROBE_LEASE_SECONDS"
        ] = SMTP_PROVIDER_TIMEOUT_SECONDS

        with pytest.raises(
            ValidationError,
            match="must exceed SMTP provider timeout",
        ):
            get_smtp_circuit()


def test_circuit_open_does_not_consume_notification_retry_budget(
    app,
    make_notification,
    clinic,
    user,
    monkeypatch,
):
    with app.app_context():
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.PENDING,
            retry_count=2,
        )
        notification_id = notification.id
        db.session.commit()

        monkeypatch.setattr(
            notification_service,
            "_deliver_with_provider",
            Mock(
                side_effect=CircuitBreakerOpenError(
                    circuit_name=SMTP_CIRCUIT_NAME,
                    retry_after_seconds=30,
                )
            ),
        )

        with pytest.raises(CircuitBreakerOpenError):
            notification_service.deliver_notification(
                clinic.id,
                notification_id,
            )

        persisted = db.session.get(
            type(notification),
            notification_id,
        )

        assert persisted is not None
        assert persisted.status is NotificationStatus.FAILED
        assert persisted.retry_count == 2
        assert persisted.failed_at is not None
        assert persisted.delivered_at is None


def test_real_provider_failure_still_consumes_retry_budget(
    app,
    make_notification,
    clinic,
    user,
    monkeypatch,
):
    with app.app_context():
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.PENDING,
            retry_count=2,
        )
        notification_id = notification.id
        db.session.commit()

        monkeypatch.setattr(
            notification_service,
            "_deliver_with_provider",
            Mock(
                side_effect=TimeoutError(
                    "provider outcome unknown"
                )
            ),
        )

        with pytest.raises(TimeoutError):
            notification_service.deliver_notification(
                clinic.id,
                notification_id,
            )

        persisted = db.session.get(
            type(notification),
            notification_id,
        )

        assert persisted is not None
        assert persisted.status is NotificationStatus.FAILED
        assert persisted.retry_count == 3
        assert persisted.failed_at is not None
        assert persisted.delivered_at is None
