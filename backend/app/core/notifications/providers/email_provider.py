from __future__ import annotations

from email.message import EmailMessage
import ssl
from typing import Any

from app.core.notifications.providers.smtp_transport import (
    PinnedSMTP,
    PinnedSMTP_SSL,
    resolve_approved_smtp_destination,
)
from app.core.notifications.providers.smtp_circuit_breaker import (
    acquire_smtp_circuit,
    call_smtp_with_permit,
)

from app.core.exceptions import ValidationError
from app.core.notifications.models.notification_models import Notification
from app.core.notifications.providers.base_provider import (
    NotificationProviderBase,
)


class EmailNotificationProvider(NotificationProviderBase):
    """
    SMTP-backed email notification provider.

    The provider receives an already-resolved email address and
    is responsible only for validating the email payload,
    constructing the message, and delegating delivery to SMTP.

    Recipient resolution and domain ownership remain outside
    the provider.
    """

    def __init__(
        self,
        *,
        credentials: dict[str, Any] | None = None,
    ):
        self.credentials = credentials or {}

    def send(
        self,
        *,
        notification: Notification,
        email: str,
        idempotency_key: str | None = None,
    ) -> bool:
        if notification is None:
            raise ValidationError(
                "Notification is required"
            )

        email = self._normalize_email(
            email
        )

        message = self._build_message(
            notification=notification,
            recipient=email,
            idempotency_key=idempotency_key,
        )

        return self._send_email(
            message=message
        )

    @staticmethod
    def _normalize_email(
        email: str,
    ) -> str:
        """
        Validate and normalize the already-resolved
        recipient email address.

        The provider does not resolve users or domain
        profiles.
        """

        if not isinstance(email, str):
            raise ValidationError(
                "Notification recipient email is invalid"
            )

        email = email.strip()

        if (
            not email
            or "@" not in email
            or len(email) > 120
        ):
            raise ValidationError(
                "Notification recipient email is invalid"
            )

        return email

    def _build_message(
        self,
        *,
        notification: Notification,
        recipient: str,
        idempotency_key: str | None = None,
    ) -> EmailMessage:
        from_email = self._get_required_credential(
            "from_email"
        )

        subject = getattr(
            notification,
            "title",
            None,
        )

        body = getattr(
            notification,
            "message",
            None,
        )

        if not isinstance(subject, str) or not subject.strip():
            raise ValidationError(
                "Notification title is required"
            )

        if not isinstance(body, str) or not body.strip():
            raise ValidationError(
                "Notification message is required"
            )

        message = EmailMessage()

        message["From"] = from_email
        message["To"] = recipient
        message["Subject"] = subject.strip()

        if idempotency_key:
            message["Message-ID"] = (
                f"<{idempotency_key}@clinic-system.local>"
            )

        message.set_content(
            body.strip()
        )

        return message

    def _get_required_credential(
        self,
        name: str,
    ) -> str:
        value = self.credentials.get(
            name
        )

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValidationError(
                f"Email provider credential "
                f"'{name}' is required"
            )

        return value.strip()

    def _get_port(self) -> int:
        port = self.credentials.get(
            "port",
            587,
        )

        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or port <= 0
            or port > 65535
        ):
            raise ValidationError(
                "Email provider port is invalid"
            )

        return port

    def _get_bool(
        self,
        name: str,
        default: bool,
    ) -> bool:
        value = self.credentials.get(
            name,
            default,
        )

        if not isinstance(value, bool):
            raise ValidationError(
                f"Email provider '{name}' "
                f"must be boolean"
            )

        return value

    def _send_email(
        self,
        *,
        message: EmailMessage,
    ) -> bool:
        host = self._get_required_credential(
            "host"
        )

        port = self._get_port()

        username = self.credentials.get(
            "username"
        )

        password = self.credentials.get(
            "password"
        )

        use_ssl = self._get_bool(
            "use_ssl",
            False,
        )

        use_tls = self._get_bool(
            "use_tls",
            True,
        )

        if use_ssl and use_tls:
            raise ValidationError(
                "Email provider cannot use both SSL and TLS"
            )

        if username is not None and not isinstance(
            username,
            str,
        ):
            raise ValidationError(
                "Email provider username is invalid"
            )

        if password is not None and not isinstance(
            password,
            str,
        ):
            raise ValidationError(
                "Email provider password is invalid"
            )

        if not use_ssl and not use_tls:
            raise ValidationError(
                "Email provider requires SSL or STARTTLS"
            )

        breaker, permit = acquire_smtp_circuit()

        def _deliver() -> bool:
            approved_host, destination_ip = (
                resolve_approved_smtp_destination(
                    host=host,
                    port=port,
                )
            )

            if use_ssl:
                smtp_connection = PinnedSMTP_SSL(
                    approved_host,
                    port,
                    destination_ip=destination_ip,
                    timeout=30,
                    context=ssl.create_default_context(),
                )
            else:
                smtp_connection = PinnedSMTP(
                    approved_host,
                    port,
                    destination_ip=destination_ip,
                    timeout=30,
                )

            with smtp_connection as smtp:
                if use_tls:
                    smtp.starttls(
                        context=ssl.create_default_context(),
                    )

                if username:
                    smtp.login(
                        username,
                        password or "",
                    )

                smtp.send_message(
                    message
                )

            return True

        return call_smtp_with_permit(
            breaker,
            permit,
            _deliver,
        )
