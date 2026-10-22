from unittest.mock import patch

import pytest

from app.core.exceptions import ValidationError
from app.core.notifications.providers.email_provider import (
    EmailNotificationProvider,
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
            "app.core.notifications.providers.email_provider.smtplib.SMTP"
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
            "app.core.notifications.providers.email_provider.smtplib.SMTP"
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
            "app.core.notifications.providers.email_provider.smtplib.SMTP_SSL"
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
            "app.core.notifications.providers.email_provider.smtplib.SMTP"
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
            "app.core.notifications.providers.email_provider.smtplib.SMTP"
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
