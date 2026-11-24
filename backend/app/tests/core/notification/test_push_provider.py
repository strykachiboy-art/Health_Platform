"""P24D focused FCM provider tests. No credentials, DB fixture or network."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.exceptions import ValidationError
from app.core.notifications.providers import push_provider as mod


def provider():
    return mod.PushNotificationProvider(credentials={"project_id": "hospitalplatform-23541"})


def notification(**overrides):
    values = dict(id=127, user_id=8, title="Private patient name", message="Confidential diagnosis", reference_type="patient", reference_id=99)
    values.update(overrides)
    return SimpleNamespace(**values)


def device(token="fid_ABC0123456789", platform="android"):
    return SimpleNamespace(device_token=token, platform=platform)


def test_payload_has_only_opaque_id_and_kind():
    payload = provider()._build_payload(notification=notification())
    assert payload == {"kind": "clinic_notification", "notification_id": "127"}
    assert "Private" not in str(payload) and "Confidential" not in str(payload)


@pytest.mark.parametrize("id", [None, 0, -1, True, "1", 2147483648])
def test_invalid_id_is_rejected(id):
    with pytest.raises(ValidationError):
        provider()._build_payload(notification=notification(id=id))


def test_mismatched_project_or_secrets_fail_closed():
    for credentials in ({}, {"project_id": "other-project"}, {"project_id": "hospitalplatform-23541", "private_key": "bad"}):
        with pytest.raises(ValidationError):
            mod.PushNotificationProvider(credentials=credentials)._verify_project()


def test_fid_transport_privacy_and_accepted_confirmation(monkeypatch):
    transport = Mock(return_value=True)
    monkeypatch.setattr(mod, "_send_http_v1", transport)
    result = provider()._send_to_device(device=device(), payload={"kind": "clinic_notification", "notification_id": "127"})
    assert result is True
    sent = transport.call_args.args[0]
    assert sent["fid"] == "fid_ABC0123456789"
    assert sent["notification"] == {"title": "Clinic System Pro", "body": "You have a new notification."}
    assert sent["data"] == {"kind": "clinic_notification", "notification_id": "127"}
    assert "token" not in sent
    assert "patient" not in str(sent).lower()


def test_skips_unconfigured_platform_and_invalid_identifiers(monkeypatch):
    transport = Mock(return_value=True)
    monkeypatch.setattr(mod, "_send_http_v1", transport)
    for item in [device(platform="ios"), device(token="invalid id"), device(token="x"), device(token="secret\nvalue")]:
        assert provider()._send_to_device(device=item, payload={"kind":"clinic_notification", "notification_id":"127"}) is False
    transport.assert_not_called()


def test_unknown_network_outcome_does_not_retry(monkeypatch):
    transport = Mock(side_effect=TimeoutError("no confirmation"))
    monkeypatch.setattr(mod, "_send_http_v1", transport)
    p = provider()
    monkeypatch.setattr(p, "_get_active_devices", lambda *, user_id: [device()])
    assert p.send(notification=notification()) is False
    transport.assert_called_once()


def test_no_device_is_not_accepted(monkeypatch):
    p = provider()
    monkeypatch.setattr(p, "_get_active_devices", lambda *, user_id: [])
    assert p.send(notification=notification()) is False


def test_request_targets_fixed_project_without_exposing_credentials(monkeypatch):
    import google.auth
    dummy_session = Mock()
    dummy_session.post.return_value.status_code = 200
    dummy_session.post.return_value.json.return_value = {"name": "projects/hospitalplatform-23541/messages/example"}
    import google.auth.transport.requests
    monkeypatch.setattr(mod, "google_auth_default", Mock(return_value=(object(), "other")))
    monkeypatch.setattr(mod, "AuthorizedSession", Mock(return_value=dummy_session))
    assert mod._send_http_v1({"fid": "a1234567890"}) is True
    url = dummy_session.post.call_args.args[0]
    assert url == "https://fcm.googleapis.com/v1/projects/hospitalplatform-23541/messages:send"
    dummy_session.close.assert_called_once()
