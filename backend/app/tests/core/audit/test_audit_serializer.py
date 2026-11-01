from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.audit.models.audit_model import AuditLog
from app.core.audit.serialization.audit_serializer import (
    serialize_audit_log,
    serialize_audit_logs,
    serialize_audit_page,
)
from app.core.enums.audit_enums import AuditAction


@pytest.fixture(autouse=True)
def configure_models(app):
    return app


def make_audit_log(**overrides):
    values = {
        "id": 1,
        "user_id": 10,
        "clinic_id": 2,
        "action": AuditAction.UPDATE,
        "entity_type": "User",
        "entity_id": 10,
        "description": "Updated user",
        "old_value": {"role": "NURSE"},
        "new_value": {"role": "DOCTOR"},
        "ip_address": "127.0.0.1",
        "created_at": datetime(
            2026,
            9,
            28,
            12,
            30,
            tzinfo=timezone.utc,
        ),
    }

    values.update(overrides)

    return AuditLog(**values)


def test_serialize_audit_log_returns_response_contract():
    log = make_audit_log()

    result = serialize_audit_log(log)

    assert result == {

        "patient_id": None,

        "operation_id": None,

        "reason": None,

        "evidence_sha256": None,
        "id": 1,
        "user_id": 10,
        "clinic_id": 2,
        "action": "update",
        "entity_type": "User",
        "entity_id": 10,
        "description": "Updated user",
        "old_value": {"role": "NURSE"},
        "new_value": {"role": "DOCTOR"},
        "ip_address": "127.0.0.1",
        "created_at": "2026-09-28T12:30:00Z",
    }


def test_serialize_audit_log_serializes_null_values():
    log = make_audit_log(
        user_id=None,
        clinic_id=None,
        description=None,
        old_value=None,
        new_value=None,
        ip_address=None,
    )

    result = serialize_audit_log(log)

    assert result["user_id"] is None
    assert result["clinic_id"] is None
    assert result["description"] is None
    assert result["old_value"] is None
    assert result["new_value"] is None
    assert result["ip_address"] is None


def test_serialize_audit_log_redacts_sensitive_old_value():
    log = make_audit_log(
        old_value={
            "email": "doctor@example.com",
            "access_token": "secret-token",
            "nested": {
                "api_key": "secret-key",
            },
        },
    )

    result = serialize_audit_log(log)

    assert result["old_value"] == {
        "email": "doctor@example.com",
        "access_token": "[REDACTED]",
        "nested": {
            "api_key": "[REDACTED]",
        },
    }


def test_serialize_audit_log_redacts_sensitive_new_value():
    log = make_audit_log(
        new_value={
            "password": "secret",
            "profile": {
                "refresh_token": "refresh-secret",
            },
        },
    )

    result = serialize_audit_log(log)

    assert result["new_value"] == {
        "password": "[REDACTED]",
        "profile": {
            "refresh_token": "[REDACTED]",
        },
    }


def test_serialize_audit_log_preserves_clinical_values():
    log = make_audit_log(
        old_value={
            "diagnosis": "Hypertension",
            "clinical_note": "Follow-up in two weeks",
        },
        new_value={
            "diagnosis": "Controlled hypertension",
            "clinical_note": "Continue treatment",
        },
    )

    result = serialize_audit_log(log)

    assert result["old_value"] == {
        "diagnosis": "Hypertension",
        "clinical_note": "Follow-up in two weeks",
    }

    assert result["new_value"] == {
        "diagnosis": "Controlled hypertension",
        "clinical_note": "Continue treatment",
    }


def test_serialize_audit_log_rejects_wrong_type():
    with pytest.raises(
        TypeError,
        match="audit_log must be an AuditLog",
    ):
        serialize_audit_log(SimpleNamespace())


def test_serialize_audit_logs_serializes_all_records():
    logs = [
        make_audit_log(id=1, entity_id=11),
        make_audit_log(id=2, entity_id=12),
        make_audit_log(id=3, entity_id=13),
    ]

    result = serialize_audit_logs(logs)

    assert [item["id"] for item in result] == [1, 2, 3]
    assert [item["entity_id"] for item in result] == [11, 12, 13]


def test_serialize_audit_logs_handles_empty_iterable():
    assert serialize_audit_logs([]) == []


def test_serialize_audit_page_serializes_items_and_metadata():
    pagination = SimpleNamespace(
        items=[
            make_audit_log(id=1),
            make_audit_log(id=2),
        ],
        total=5,
        page=1,
        per_page=2,
        pages=3,
        has_next=True,
        has_prev=False,
    )

    result = serialize_audit_page(pagination)

    assert result["items"][0]["id"] == 1
    assert result["items"][1]["id"] == 2
    assert result["total"] == 5
    assert result["page"] == 1
    assert result["per_page"] == 2
    assert result["pages"] == 3
    assert result["has_next"] is True
    assert result["has_prev"] is False


def test_serialize_audit_page_preserves_redaction():
    pagination = SimpleNamespace(
        items=[
            make_audit_log(
                new_value={
                    "authorization": "Bearer secret",
                    "status": "active",
                }
            )
        ],
        total=1,
        page=1,
        per_page=20,
        pages=1,
        has_next=False,
        has_prev=False,
    )

    result = serialize_audit_page(pagination)

    assert result["items"][0]["new_value"] == {
        "authorization": "[REDACTED]",
        "status": "active",
    }