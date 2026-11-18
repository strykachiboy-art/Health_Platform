"""GAP-C P0: notification worker authority checked at egress.

Two organizations, two clinics, real SQLAlchemy and Celery task entry.
No fake provider is allowed to observe a denied notification.
"""
from uuid import uuid4
from unittest.mock import Mock
import pytest

from app.core.enums.role_enums import Role
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.notification_enums import NotificationChannel, NotificationStatus
from app.core.notifications.services import notification_service
from app.core.notifications.models.notification_models import Notification
from app.core.exceptions import NotFoundError
from app.modules.clinic.models.organization_model import (
    HospitalOrganization, TenantMembership, ClinicMembership,
)


@pytest.fixture()
def world(db, make_clinic, make_staff, make_user, make_notification):
    alpha = HospitalOrganization(id=uuid4(), name="GAPC synthetic Alpha")
    beta = HospitalOrganization(id=uuid4(), name="GAPC synthetic Beta")
    db.session.add_all([alpha, beta])
    db.session.flush()
    a = make_clinic(name="GAPC Alpha Clinic", organization_id=alpha.id)
    b = make_clinic(name="GAPC Beta Clinic", organization_id=beta.id)
    staff = make_staff(a, role=Role.DOCTOR)
    db.session.add(TenantMembership(
        organization_id=alpha.id, user_id=staff.user.id,
        is_active=True, role="member", status="active",
    ))
    db.session.flush()
    db.session.add(ClinicMembership(
        organization_id=alpha.id, clinic_id=a.id, user_id=staff.user.id,
        role="doctor", is_active=True,
    ))
    db.session.flush()
    notice = make_notification(
        clinic_id=a.id, user_id=staff.user.id,
        channel=NotificationChannel.PUSH,
        status=NotificationStatus.PENDING,
    )
    db.session.commit()
    return {
        "alpha": alpha, "beta": beta, "a": a, "b": b,
        "staff": staff, "notice": notice,
    }


def _provider(monkeypatch):
    provider=Mock()
    provider.send.return_value=True
    factory=Mock(return_value=provider)
    monkeypatch.setattr(notification_service, "get_notification_provider", factory)
    return provider, factory


def _attempt(world, monkeypatch, db):
    provider, factory = _provider(monkeypatch)
    notice=world["notice"]
    result = notification_service.deliver_notification(
        world["a"].id, notice.id,
    )
    db.session.expire_all()
    persisted=db.session.get(Notification, notice.id)
    return result, persisted, provider, factory


def test_active_explicit_staff_grant_can_deliver(world, monkeypatch, db):
    result, persisted, provider, factory=_attempt(world,monkeypatch,db)
    assert result is True
    assert persisted.status is NotificationStatus.DELIVERED
    assert provider.send.call_count == 1
    assert factory.call_count == 1


def test_revoked_organization_membership_prevents_external_delivery(world, monkeypatch, db):
    member=db.session.query(TenantMembership).filter_by(
        user_id=world["staff"].user_id, organization_id=world["alpha"].id,
    ).one()
    member.is_active=False
    db.session.commit()
    result, persisted, provider, factory=_attempt(world,monkeypatch,db)
    assert result is False
    assert persisted.status is NotificationStatus.PENDING
    assert persisted.sent_at is None
    assert persisted.retry_count == 0
    provider.send.assert_not_called()
    factory.assert_not_called()


def test_revoked_clinic_grant_prevents_external_delivery(world, monkeypatch, db):
    member=db.session.query(ClinicMembership).filter_by(
        user_id=world["staff"].user_id, clinic_id=world["a"].id,
    ).one()
    member.is_active=False
    db.session.commit()
    result, persisted, provider, factory=_attempt(world,monkeypatch,db)
    assert result is False
    assert persisted.status is NotificationStatus.PENDING
    assert persisted.sent_at is None
    provider.send.assert_not_called()
    factory.assert_not_called()


def test_suspended_clinic_prevents_external_delivery(world, monkeypatch, db):
    world["a"].status=ClinicStatus.SUSPENDED
    db.session.commit()
    result, persisted, provider, factory=_attempt(world,monkeypatch,db)
    assert result is False
    assert persisted.status is NotificationStatus.PENDING
    provider.send.assert_not_called()
    factory.assert_not_called()


def test_cross_organization_id_guess_never_dispatches(world, monkeypatch, db):
    provider,factory=_provider(monkeypatch)
    n=world["notice"]
    result=notification_service.deliver_notification(world["b"].id,n.id)
    db.session.expire_all()
    assert result is False
    assert db.session.get(Notification,n.id).status is NotificationStatus.PENDING
    provider.send.assert_not_called()
    factory.assert_not_called()


def test_direct_provider_helper_is_not_a_revocation_bypass(world, monkeypatch, db):
    member=db.session.query(TenantMembership).filter_by(
        user_id=world["staff"].user_id, organization_id=world["alpha"].id,
    ).one()
    member.status="revoked"
    db.session.commit()
    provider,factory=_provider(monkeypatch)
    with pytest.raises(NotFoundError, match="unavailable"):
        notification_service._deliver_with_provider(world["notice"])
    provider.send.assert_not_called()
    factory.assert_not_called()


def test_ungranted_superadmin_not_implicitly_allowed(world, make_user, monkeypatch, db):
    admin=make_user(clinic=world["a"],role=Role.SUPER_ADMIN)
    db.session.add(admin)
    notice=Notification(
        clinic_id=world["a"].id, user_id=admin.id,
        title="Synthetic", message="Synthetic", channel=NotificationChannel.PUSH,
        notification_type=world["notice"].notification_type,
        priority=world["notice"].priority,
        status=NotificationStatus.PENDING,
    )
    db.session.add(notice)
    db.session.commit()
    provider,factory=_provider(monkeypatch)
    assert notification_service.deliver_notification(world["a"].id,notice.id) is False
    assert db.session.get(Notification,notice.id).status is NotificationStatus.PENDING
    provider.send.assert_not_called()
    factory.assert_not_called()


def test_patient_first_notification_does_not_require_staff_membership(
    world, make_user, make_patient, make_notification, monkeypatch, db,
):
    patient_user=make_user(clinic=world["a"],role=Role.PATIENT)
    patient=make_patient(world["a"], user_id=patient_user.id)
    db.session.add(patient)
    notice=make_notification(
        clinic_id=world["a"].id, user_id=patient_user.id,
        channel=NotificationChannel.PUSH,
        status=NotificationStatus.PENDING,
    )
    db.session.commit()
    provider,factory=_provider(monkeypatch)
    assert notification_service.deliver_notification(world["a"].id,notice.id) is True
    assert db.session.get(Notification,notice.id).status is NotificationStatus.DELIVERED
    provider.send.assert_called_once()
    factory.assert_called_once()
