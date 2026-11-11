from __future__ import annotations

import pytest

from app.core.enums.notification_enums import NotificationType
from app.core.enums.role_enums import Role
from app.core.exceptions import NotFoundError
from app.core.notifications.services import notification_service


def _create(clinic_id, user_id, title):
    return notification_service.create_notification(
        clinic_id=clinic_id,
        user_id=user_id,
        title=title,
        message="Scoped system message",
        notification_type=NotificationType.SYSTEM,
    )


def test_super_admin_requires_selected_clinic_for_inbox(
    client, make_user, auth_headers_for
):
    super_admin = make_user(role=Role.SUPER_ADMIN)
    headers = auth_headers_for(super_admin)
    for path in ("/", "/unread"):
        response = client.get(f"/api/v1/notifications{path}", headers=headers)
        assert response.status_code == 422
    response = client.post(
        "/api/v1/notifications/read-all", json={}, headers=headers
    )
    assert response.status_code == 422


def test_super_admin_selected_clinic_inbox_is_owner_and_tenant_scoped(
    client, clinic, make_clinic, make_user, auth_headers_for
):
    other_clinic = make_clinic()
    super_admin = make_user(role=Role.SUPER_ADMIN)
    regular_user = make_user(clinic=clinic, role=Role.DOCTOR)
    first = _create(clinic.id, super_admin.id, "Clinic A")
    second = _create(other_clinic.id, super_admin.id, "Clinic B")
    other_owner = _create(clinic.id, regular_user.id, "Doctor only")

    a_headers = auth_headers_for(super_admin, clinic_context_id=clinic.id)
    b_headers = auth_headers_for(super_admin, clinic_context_id=other_clinic.id)
    for path in ("/", "/unread"):
        result = client.get(f"/api/v1/notifications{path}", headers=a_headers)
        assert result.status_code == 200
        assert [x["id"] for x in result.get_json()["data"]["items"]] == [first.id]

    for foreign_id in (second.id, other_owner.id):
        assert client.get(
            f"/api/v1/notifications/{foreign_id}", headers=a_headers
        ).status_code == 404
        assert client.post(
            f"/api/v1/notifications/{foreign_id}/read",
            json={}, headers=a_headers
        ).status_code == 404

    mark_all = client.post(
        "/api/v1/notifications/read-all", json={}, headers=a_headers
    )
    assert mark_all.status_code == 200
    assert mark_all.get_json()["data"]["updated_count"] == 1
    a_unread = client.get("/api/v1/notifications/unread", headers=a_headers)
    assert a_unread.get_json()["data"]["items"] == []
    b_unread = client.get("/api/v1/notifications/unread", headers=b_headers)
    assert b_unread.status_code == 200
    assert [x["id"] for x in b_unread.get_json()["data"]["items"]] == [second.id]


def test_super_admin_single_read_and_create_denied(
    client, clinic, make_user, auth_headers_for
):
    super_admin = make_user(role=Role.SUPER_ADMIN)
    notification = _create(clinic.id, super_admin.id, "Direct message")
    headers = auth_headers_for(super_admin, clinic_context_id=clinic.id)
    read = client.post(
        f"/api/v1/notifications/{notification.id}/read",
        json={}, headers=headers
    )
    assert read.status_code == 200
    assert read.get_json()["data"]["is_read"] is True
    assert client.post(
        "/api/v1/notifications/",
        json={
            "user_id": super_admin.id,
            "title": "Not authorized",
            "message": "No role expansion",
            "notification_type": "system",
        },
        headers=headers,
    ).status_code == 403


def test_public_create_cannot_target_super_admin(
    client, clinic, make_user, auth_headers_for
):
    actor = make_user(clinic=clinic, role=Role.DOCTOR)
    super_admin = make_user(role=Role.SUPER_ADMIN)
    response = client.post(
        "/api/v1/notifications/",
        json={
            "user_id": super_admin.id,
            "title": "Wrong target",
            "message": "No direct message permission",
            "notification_type": "system",
        },
        headers=auth_headers_for(actor),
    )
    assert response.status_code == 404


def test_service_still_rejects_cross_clinic_regular_recipient(
    clinic, make_clinic, make_user
):
    other_clinic = make_clinic()
    user = make_user(clinic=clinic, role=Role.DOCTOR)
    with pytest.raises(NotFoundError):
        _create(other_clinic.id, user.id, "Wrong tenant")
