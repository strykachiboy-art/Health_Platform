from __future__ import annotations

import pytest

from app.core.audit.models.audit_model import AuditLog
from app.core.audit.services.audit_writer import write_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.extensions import db
from app.modules.access_control.services import (
    access_control_service,
)


def test_audit_actor_derives_current_clinic(
    db_session,
    user,
    clinic,
):
    log = write_audit_log(
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        user_id=user.id,
    )

    db_session.flush()

    assert log.user_id == user.id
    assert log.clinic_id == clinic.id


def test_audit_rejects_actor_from_wrong_clinic(
    user,
    make_clinic,
):
    other_clinic = make_clinic()

    with pytest.raises(
        ValidationError,
        match="Audit clinic does not match the audit actor",
    ):
        write_audit_log(
            action=AuditAction.UPDATE,
            entity_type="User",
            entity_id=user.id,
            user_id=user.id,
            clinic_id=other_clinic.id,
        )


def test_audit_actor_integrity_follows_persisted_user_clinic(
    db_session,
    make_user,
    make_clinic,
):
    first_clinic = make_clinic()
    second_clinic = make_clinic()

    actor = make_user(
        first_clinic,
        role=Role.DOCTOR,
    )

    first_log = write_audit_log(
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=actor.id,
        user_id=actor.id,
    )

    db_session.flush()

    assert first_log.clinic_id == first_clinic.id

    actor.clinic_id = second_clinic.id
    db_session.flush()
    db_session.expire_all()

    second_log = write_audit_log(
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=actor.id,
        user_id=actor.id,
    )

    db_session.flush()

    assert second_log.clinic_id == second_clinic.id


def test_audit_log_redacts_sensitive_actor_data(
    db_session,
    user,
):
    log = write_audit_log(
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        user_id=user.id,
        old_value={
            "role": "doctor",
            "password": "old-secret",
            "refresh_token": "old-token",
        },
        new_value={
            "role": "nurse",
            "password": "new-secret",
            "access_token": "new-token",
        },
    )

    db_session.flush()

    assert log.old_value == {
        "role": "doctor",
        "password": "[REDACTED]",
        "refresh_token": "[REDACTED]",
    }

    assert log.new_value == {
        "role": "nurse",
        "password": "[REDACTED]",
        "access_token": "[REDACTED]",
    }


def test_privileged_change_records_authenticated_actor(
    db_session,
    clinic,
    make_user,
):
    super_admin = make_user(
        None,
        role=Role.SUPER_ADMIN,
        email="audit-integrity-super@test.com",
    )

    target = make_user(
        clinic,
        role=Role.DOCTOR,
        email="audit-integrity-target@test.com",
    )

    access_control_service.change_user_role(
        actor_id=super_admin.id,
        user_id=target.id,
        new_role=Role.NURSE,
        reason="Audit actor integrity test",
    )

    audit = (
        db_session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.entity_type == "User",
                AuditLog.entity_id == target.id,
                AuditLog.action == AuditAction.UPDATE,
            )
            .order_by(AuditLog.id.desc())
        )
        .scalars()
        .first()
    )

    assert audit is not None
    assert audit.user_id == super_admin.id
    assert audit.clinic_id == clinic.id

    assert audit.old_value["role"] == Role.DOCTOR.value
    assert audit.new_value["role"] == Role.NURSE.value


def test_audit_actor_relationship_mutation_is_rejected(
    db_session,
    user,
    clinic,
):
    log = write_audit_log(
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    user.audit_logs.remove(
        log
    )

    with pytest.raises(
        ConflictError,
        match="immutable",
    ):
        db_session.flush()

    db_session.rollback()


def test_audit_write_does_not_commit_before_caller_transaction(
    db_session,
    user,
):
    log = write_audit_log(
        action=AuditAction.CREATE,
        entity_type="Patient",
        entity_id=100,
        user_id=user.id,
    )

    assert log in db_session.new
    assert log.id is None


@pytest.mark.parametrize(
    "method",
    [
        "post",
        "patch",
        "delete",
    ],
)
def test_audit_api_has_no_mutation_endpoint(
    client,
    auth_headers_for,
    user,
    method,
):
    request_method = getattr(client, method)

    response = request_method(
        "/api/v1/audit-logs",
        headers=auth_headers_for(user),
        json={
            "action": "update",
            "entity_type": "User",
            "entity_id": user.id,
        },
    )

    assert response.status_code == 405


def test_audit_api_rejects_client_actor_scope_fields(
    client,
    auth_headers_for,
    user,
):
    response = client.get(
        "/api/v1/audit-logs",
        query_string={
            "clinic_id": user.clinic_id,
            "actor_id": user.id,
        },
        headers=auth_headers_for(user),
    )

    assert response.status_code == 422
