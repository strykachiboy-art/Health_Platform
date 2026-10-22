import ipaddress
from unittest.mock import Mock, patch

import pytest

from app.core.exceptions import ValidationError
from app.core.notifications.providers import email_provider, smtp_transport
from app.core.notifications.providers.email_provider import (
    EmailNotificationProvider,
)


@pytest.fixture(autouse=True)
def _mock_approved_smtp_destination_resolution(monkeypatch):
    # Existing provider tests mock SMTP itself. Keep them offline while the
    # dedicated transport tests exercise the real destination-policy helpers.
    monkeypatch.setattr(
        email_provider,
        "resolve_approved_smtp_destination",
        lambda *, host, port: (host, "8.8.8.8"),
    )


class TestEmailNotificationProvider:

    def test_send_success(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
            title="Appointment Reminder",
            message="Your appointment is tomorrow.",
        )

        provider = EmailNotificationProvider(
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

        with patch(
            "app.core.notifications.providers.email_provider.PinnedSMTP"
        ) as smtp_class:
            smtp = (
                smtp_class
                .return_value
                .__enter__
                .return_value
            )

            result = provider.send(
                notification=notification,
                email="patient@example.com",
            )

        assert result is True

        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with(
            "mailer@example.com",
            "secret",
        )
        smtp.send_message.assert_called_once()

        sent_message = smtp.send_message.call_args.args[0]

        assert sent_message["To"] == "patient@example.com"
        assert sent_message["From"] == "mailer@example.com"
        assert sent_message["Subject"] == "Appointment Reminder"

    def test_send_rejects_missing_notification(self):
        provider = EmailNotificationProvider()

        with pytest.raises(
            ValidationError,
            match="Notification is required",
        ):
            provider.send(
                notification=None,
                email="patient@example.com",
            )

    def test_send_rejects_invalid_email(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider()

        with pytest.raises(
            ValidationError,
            match="Notification recipient email is invalid",
        ):
            provider.send(
                notification=notification,
                email="invalid-email",
            )

    def test_send_rejects_empty_email(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider()

        with pytest.raises(
            ValidationError,
            match="Notification recipient email is invalid",
        ):
            provider.send(
                notification=notification,
                email="   ",
            )

    def test_send_rejects_non_string_email(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider()

        with pytest.raises(
            ValidationError,
            match="Notification recipient email is invalid",
        ):
            provider.send(
                notification=notification,
                email=None,
            )

    def test_send_rejects_oversized_email(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider()

        with pytest.raises(
            ValidationError,
            match="Notification recipient email is invalid",
        ):
            provider.send(
                notification=notification,
                email=("a" * 110) + "@example.com",
            )

    def test_send_rejects_empty_title(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
            title="   ",
        )

        provider = EmailNotificationProvider(
            credentials={
                "from_email": "mailer@example.com",
            }
        )

        with pytest.raises(
            ValidationError,
            match="Notification title is required",
        ):
            provider.send(
                notification=notification,
                email="patient@example.com",
            )

    def test_send_rejects_empty_message(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
            message="   ",
        )

        provider = EmailNotificationProvider(
            credentials={
                "from_email": "mailer@example.com",
            }
        )

        with pytest.raises(
            ValidationError,
            match="Notification message is required",
        ):
            provider.send(
                notification=notification,
                email="patient@example.com",
            )

    def test_missing_host_is_rejected(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider(
            credentials={
                "from_email": "mailer@example.com",
            }
        )

        with pytest.raises(
            ValidationError,
            match="host",
        ):
            provider.send(
                notification=notification,
                email="patient@example.com",
            )

    def test_missing_from_email_is_rejected(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider(
            credentials={
                "host": "smtp.example.com",
            }
        )

        with pytest.raises(
            ValidationError,
            match="from_email",
        ):
            provider.send(
                notification=notification,
                email="patient@example.com",
            )

    def test_invalid_port_is_rejected(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider(
            credentials={
                "host": "smtp.example.com",
                "port": 0,
                "from_email": "mailer@example.com",
            }
        )

        with pytest.raises(
            ValidationError,
            match="port is invalid",
        ):
            provider.send(
                notification=notification,
                email="patient@example.com",
            )

    def test_ssl_and_tls_cannot_both_be_enabled(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider(
            credentials={
                "host": "smtp.example.com",
                "from_email": "mailer@example.com",
                "use_ssl": True,
                "use_tls": True,
            }
        )

        with pytest.raises(
            ValidationError,
            match="both SSL and TLS",
        ):
            provider.send(
                notification=notification,
                email="patient@example.com",
            )

    def test_plaintext_smtp_is_rejected(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider(
            credentials={
                "host": "smtp.example.com",
                "port": 25,
                "from_email": "mailer@example.com",
                "use_ssl": False,
                "use_tls": False,
            }
        )

        with patch(
            "app.core.notifications.providers.email_provider.PinnedSMTP"
        ) as smtp_class:
            with pytest.raises(
                ValidationError,
                match="requires SSL or STARTTLS",
            ):
                provider.send(
                    notification=notification,
                    email="patient@example.com",
                )

            smtp_class.assert_not_called()

    def test_ssl_transport_is_supported(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider(
            credentials={
                "host": "smtp.example.com",
                "port": 465,
                "from_email": "mailer@example.com",
                "use_ssl": True,
                "use_tls": False,
            }
        )

        with patch(
            "app.core.notifications.providers.email_provider.PinnedSMTP_SSL"
        ) as smtp_class:
            smtp = (
                smtp_class
                .return_value
                .__enter__
                .return_value
            )

            result = provider.send(
                notification=notification,
                email="patient@example.com",
            )

        assert result is True
        smtp.send_message.assert_called_once()

    def test_send_normalizes_email(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
            title="  Appointment Reminder  ",
            message="  Your appointment is tomorrow.  ",
        )

        provider = EmailNotificationProvider(
            credentials={
                "host": "smtp.example.com",
                "from_email": "mailer@example.com",
            }
        )

        with patch(
            "app.core.notifications.providers.email_provider.PinnedSMTP"
        ) as smtp_class:
            smtp = (
                smtp_class
                .return_value
                .__enter__
                .return_value
            )

            result = provider.send(
                notification=notification,
                email="  patient@example.com  ",
            )

        assert result is True

        sent_message = smtp.send_message.call_args.args[0]

        assert sent_message["To"] == "patient@example.com"
        assert sent_message["Subject"] == "Appointment Reminder"
        assert (
            sent_message.get_content().strip()
            == "Your appointment is tomorrow."
        )

    def test_transport_failure_propagates(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
        )

        provider = EmailNotificationProvider(
            credentials={
                "host": "smtp.example.com",
                "from_email": "mailer@example.com",
            }
        )

        with patch(
            "app.core.notifications.providers.email_provider.PinnedSMTP"
        ) as smtp_class:
            smtp = (
                smtp_class
                .return_value
                .__enter__
                .return_value
            )

            smtp.send_message.side_effect = RuntimeError(
                "SMTP failure"
            )

            with pytest.raises(
                RuntimeError,
                match="SMTP failure",
            ):
                provider.send(
                    notification=notification,
                    email="patient@example.com",
                )

    def test_build_message_sets_idempotency_message_id(
        self,
        make_notification,
        clinic,
        user,
    ):
        notification = make_notification(
            clinic_id=clinic.id,
            user_id=user.id,
            title="Appointment Reminder",
            message="Your appointment is tomorrow.",
        )

        provider = EmailNotificationProvider(
            credentials={
                "from_email": "mailer@example.com",
            }
        )

        message = provider._build_message(
            notification=notification,
            recipient="patient@example.com",
            idempotency_key="clinic-notification-1-2",
        )

        assert message["Message-ID"] == (
            "<clinic-notification-1-2@clinic-system.local>"
        )


def test_smtp_destination_policy_fails_closed_without_operator_allowlists(app, monkeypatch):
    app.config["SMTP_ALLOWED_HOSTS"] = ""
    app.config["SMTP_ALLOWED_PORTS"] = ""
    dns_lookup = Mock(return_value=("8.8.8.8",))
    monkeypatch.setattr(smtp_transport, "_resolve_smtp_host_addresses", dns_lookup)

    with pytest.raises(ValidationError, match="policy is not configured"):
        smtp_transport.resolve_approved_smtp_destination(
            host="smtp.example.com",
            port=587,
        )

    dns_lookup.assert_not_called()


def test_smtp_destination_policy_rejects_unapproved_host_before_dns(app, monkeypatch):
    dns_lookup = Mock(return_value=("8.8.8.8",))
    monkeypatch.setattr(smtp_transport, "_resolve_smtp_host_addresses", dns_lookup)

    with pytest.raises(ValidationError, match="hostname is not approved"):
        smtp_transport.resolve_approved_smtp_destination(
            host="smtp.attacker.example",
            port=587,
        )

    dns_lookup.assert_not_called()


def test_smtp_destination_policy_rejects_unapproved_port_before_dns(app, monkeypatch):
    dns_lookup = Mock(return_value=("8.8.8.8",))
    monkeypatch.setattr(smtp_transport, "_resolve_smtp_host_addresses", dns_lookup)

    with pytest.raises(ValidationError, match="port is not approved"):
        smtp_transport.resolve_approved_smtp_destination(
            host="smtp.example.com",
            port=25,
        )

    dns_lookup.assert_not_called()


def test_smtp_destination_policy_rejects_mixed_public_and_private_dns_answers(app, monkeypatch):
    monkeypatch.setattr(
        smtp_transport,
        "_resolve_smtp_host_addresses",
        Mock(return_value=("8.8.8.8", "127.0.0.1")),
    )

    with pytest.raises(ValidationError, match="not globally routable"):
        smtp_transport.resolve_approved_smtp_destination(
            host="smtp.example.com",
            port=587,
        )


def test_pinned_smtp_socket_uses_numeric_ip_and_verifies_peer(monkeypatch):
    raw_socket = Mock()
    raw_socket.getpeername.return_value = ("8.8.8.8", 587)
    connect = Mock(return_value=raw_socket)
    monkeypatch.setattr(smtp_transport.socket, "create_connection", connect)

    result = smtp_transport._open_pinned_socket(
        "8.8.8.8",
        587,
        4.0,
        None,
    )

    assert result is raw_socket
    connect.assert_called_once_with(
        ("8.8.8.8", 587),
        timeout=4.0,
        source_address=None,
    )


def test_pinned_smtp_socket_rejects_a_peer_ip_mismatch(monkeypatch):
    raw_socket = Mock()
    raw_socket.getpeername.return_value = ("1.1.1.1", 587)
    monkeypatch.setattr(
        smtp_transport.socket,
        "create_connection",
        Mock(return_value=raw_socket),
    )

    with pytest.raises(OSError, match="did not match"):
        smtp_transport._open_pinned_socket(
            "8.8.8.8",
            587,
            4.0,
            None,
        )

    raw_socket.close.assert_called_once()


def test_pinned_implicit_tls_keeps_original_hostname_for_certificate_verification(monkeypatch):
    connection = object.__new__(smtp_transport.PinnedSMTP_SSL)
    connection._destination_ip = ipaddress.ip_address("8.8.8.8")
    connection.source_address = None
    connection._host = "smtp.example.com"
    connection.context = Mock()
    raw_socket = Mock()
    tls_socket = Mock()
    connection.context.wrap_socket.return_value = tls_socket
    monkeypatch.setattr(
        smtp_transport,
        "_open_pinned_socket",
        Mock(return_value=raw_socket),
    )

    assert connection._get_socket("smtp.example.com", 465, 30.0) is tls_socket
    connection.context.wrap_socket.assert_called_once_with(
        raw_socket,
        server_hostname="smtp.example.com",
    )


def test_pinned_starttls_uses_original_hostname_for_certificate_verification():
    connection = object.__new__(smtp_transport.PinnedSMTP)
    connection._host = "smtp.example.com"
    connection.sock = Mock()
    raw_socket = connection.sock
    connection.debuglevel = 0
    connection.file = None
    connection.helo_resp = None
    connection.ehlo_resp = None
    connection.esmtp_features = {}
    connection.does_esmtp = False
    connection.ehlo_or_helo_if_needed = Mock(return_value=None)
    connection.has_extn = Mock(return_value=True)
    connection.docmd = Mock(return_value=(220, b"ready"))
    tls_context = Mock()
    tls_socket = Mock()
    tls_context.wrap_socket.return_value = tls_socket

    connection.starttls(context=tls_context)

    tls_context.wrap_socket.assert_called_once_with(
        raw_socket,
        server_hostname="smtp.example.com",
    )
    assert connection.sock is tls_socket
