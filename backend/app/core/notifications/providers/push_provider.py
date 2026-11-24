from __future__ import annotations

"""FCM HTTP v1 provider. A 2xx response is *provider acceptance*, not device receipt.

The per-clinic push integration config supplies only a public ``project_id``.
Google Application Default Credentials are configured on the worker host and
never enter clinic integration settings or the repository.
"""

import re
from typing import Any

from google.auth import default as google_auth_default
from google.auth.transport.requests import AuthorizedSession
from sqlalchemy import select

from app.extensions import db
from app.core.auth.user.models.user_device_model import UserDevice
from app.core.exceptions import ValidationError
from app.core.notifications.providers.base_provider import NotificationProviderBase

_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_PROJECT_ID = "hospitalplatform-23541"
_ENDPOINT = f"https://fcm.googleapis.com/v1/projects/{_PROJECT_ID}/messages:send"


def _send_http_v1(message: dict[str, Any]) -> bool:
    """Submit one request to Google's fixed endpoint, no caller-controlled URL.

    No automatic retry: a timeout after dispatch may mean FCM accepted it.
    Never log an access token, FID, request body or FCM error response.
    """
    credentials, _ = google_auth_default(scopes=[_FCM_SCOPE])
    session = AuthorizedSession(credentials)
    try:
        response = session.post(
            _ENDPOINT,
            json={"message": message},
            timeout=(5, 10),
        )
        if response.status_code != 200:
            return False
        try:
            body = response.json()
        except (ValueError, TypeError):
            return False
        name = body.get("name") if isinstance(body, dict) else None
        return isinstance(name, str) and name.startswith(
            f"projects/{_PROJECT_ID}/messages/"
        ) and len(name) > len(f"projects/{_PROJECT_ID}/messages/")
    finally:
        session.close()


class PushNotificationProvider(NotificationProviderBase):
    def __init__(self, *, credentials: dict[str, Any] | None = None):
        self.credentials = credentials or {}

    def _verify_project(self) -> None:
        project = self.credentials.get("project_id")
        if project != _PROJECT_ID:
            raise ValidationError("FCM project configuration is not approved")
        if any(k in self.credentials for k in ("private_key", "client_email", "access_token")):
            raise ValidationError("FCM worker credentials must not be stored in clinic settings")

    def send(self, *, notification, idempotency_key: str | None = None) -> bool:
        if notification is None:
            raise ValidationError("Notification is required")
        user_id = getattr(notification, "user_id", None)
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
            raise ValidationError("Invalid notification user ID")
        # Validate configuration BEFORE querying targets or attempting network.
        self._verify_project()
        payload = self._build_payload(notification=notification)
        devices = self._get_active_devices(user_id=user_id)
        accepted = False
        for device in devices:
            try:
                if self._send_to_device(
                    device=device, payload=payload, idempotency_key=idempotency_key,
                ):
                    accepted = True
            except Exception:
                # Fail closed per device. The notification service owns status;
                # provider response alone never proves on-device delivery.
                continue
        return accepted

    def _get_active_devices(self, *, user_id: int) -> list[UserDevice]:
        statement = (
            select(UserDevice)
            .where(UserDevice.user_id == user_id, UserDevice.is_active.is_(True))
            .order_by(UserDevice.id.asc())
        )
        return list(db.session.scalars(statement).all())

    def _build_payload(self, *, notification) -> dict[str, str]:
        notification_id = getattr(notification, "id", None)
        if isinstance(notification_id, bool) or not isinstance(notification_id, int) or notification_id < 1 or notification_id > 2147483647:
            raise ValidationError("Invalid notification ID")
        # Never transmit title, message, patient, clinic, user, reference or PHI.
        return {"kind": "clinic_notification", "notification_id": str(notification_id)}

    def _send_to_device(self, *, device: UserDevice, payload: dict[str, str], idempotency_key: str | None = None) -> bool:
        fid = getattr(device, "device_token", None)
        platform = getattr(device, "platform", None)
        if not isinstance(fid, str) or not (10 <= len(fid) <= 500) or fid != fid.strip() or not re.fullmatch(r"[A-Za-z0-9_:-]+", fid):
            return False
        # Only Android has been configured in this P24D bootstrap.
        if platform != "android":
            return False
        return self._deliver_to_device(device_token=fid, platform=platform, payload=payload, idempotency_key=idempotency_key)

    def _deliver_to_device(self, *, device_token: str, platform: str, payload: dict[str, str], idempotency_key: str | None = None) -> bool:
        self._verify_project()
        if platform != "android" or set(payload) != {"kind", "notification_id"}:
            return False
        if payload.get("kind") != "clinic_notification" or not re.fullmatch(r"[1-9][0-9]{0,9}", payload.get("notification_id", "")):
            return False
        message: dict[str, Any] = {
            "fid": device_token,
            "notification": {
                "title": "Clinic System Pro",
                "body": "You have a new notification.",
            },
            "data": dict(payload),
            "android": {
                "priority": "NORMAL",
                "notification": {"visibility": "PRIVATE"},
            },
        }
        return _send_http_v1(message)
