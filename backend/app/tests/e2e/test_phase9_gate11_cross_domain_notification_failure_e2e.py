from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.billing_enums import InvoiceStatus
from app.core.enums.role_enums import Role
from app.core.enums.notification_enums import (
    NotificationStatus,
)
from app.core.notifications.models.notification_models import (
    Notification,
)
from app.core.notifications.services import notification_service
from app.extensions import db
from app.modules.billing.models.billing_model import Invoice
from app.modules.pharmacy.models.pharmacy_model import (
    DispenseRecord,
    DrugBatch,
)


def _auth(login: dict) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {login['access_token']}"
        ),
    }


class Gate11NotificationProvider:
    def __init__(self):
        self.calls = []
        self.accepted_keys = set()
        self.logical_deliveries = []

    def send(self, **kwargs):
        key = kwargs["idempotency_key"]

        self.calls.append(key)

        if key not in self.accepted_keys:
            self.accepted_keys.add(key)
            self.logical_deliveries.append(
                kwargs["notification"].id
            )

            raise TimeoutError(
                "Gate 11 notification provider "
                "timeout after acceptance"
            )

        return True


def test_gate11_cross_domain_notification_provider_failure_is_retryable(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    e2e_login,
    monkeypatch,
):
    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="gate11-slice2-receptionist@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "gate11-slice2-doctor@test.com",
        },
    )

    pharmacist_staff = make_staff(
        clinic=clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": "gate11-slice2-pharmacist@test.com",
        },
    )

    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="gate11-slice2-admin@test.com",
    )

    receptionist_login = e2e_login(
        "gate11-slice2-receptionist@test.com",
    )
    doctor_login = e2e_login(
        "gate11-slice2-doctor@test.com",
    )
    pharmacist_login = e2e_login(
        "gate11-slice2-pharmacist@test.com",
    )
    admin_login = e2e_login(
        "gate11-slice2-admin@test.com",
    )

    assert receptionist_login["role"] == (
        Role.RECEPTIONIST.value
    )
    assert doctor_login["role"] == Role.DOCTOR.value
    assert pharmacist_login["role"] == (
        Role.PHARMACIST.value
    )
    assert admin_login["role"] == Role.ADMIN.value

    # ============================================================
    # PATIENT
    # ============================================================

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Gate11",
            "last_name": "Slice2 Patient",
        },
        headers=_auth(receptionist_login),
    )

    assert patient_response.status_code == 201, (
        patient_response.get_json()
    )

    patient_id = patient_response.get_json()["data"]["id"]

    # ============================================================
    # APPOINTMENT
    # ============================================================

    scheduled_start = (
        datetime.now(timezone.utc)
        + timedelta(days=1)
    )

    scheduled_end = (
        scheduled_start
        + timedelta(minutes=30)
    )

    appointment_response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "scheduled_start": scheduled_start.isoformat(),
            "scheduled_end": scheduled_end.isoformat(),
            "appointment_type": "in_person",
            "reason": (
                "Gate 11 Slice 2 notification failure"
            ),
        },
        headers=_auth(doctor_login),
    )

    assert appointment_response.status_code == 201, (
        appointment_response.get_json()
    )

    appointment_id = (
        appointment_response.get_json()["data"]["id"]
    )

    # ============================================================
    # CONSULTATION
    # ============================================================

    consultation_response = client.post(
        "/api/v1/consultations/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "appointment_id": appointment_id,
            "consultation_type": "general",
            "chief_complaint": (
                "Gate 11 Slice 2 workflow"
            ),
            "symptoms": (
                "Cross-domain notification failure"
            ),
        },
        headers=_auth(doctor_login),
    )

    assert consultation_response.status_code == 201, (
        consultation_response.get_json()
    )

    consultation_id = (
        consultation_response.get_json()["data"]["id"]
    )

    # ============================================================
    # PHARMACY
    # ============================================================

    drug_response = client.post(
        "/api/v1/pharmacy/drugs",
        json={
            "name": "Gate11 Slice2 Amoxicillin",
            "generic_name": "Amoxicillin",
            "dosage_form": "capsule",
            "strength": "500 mg",
            "unit_price": "5.00",
            "is_controlled": False,
        },
        headers=_auth(admin_login),
    )

    assert drug_response.status_code == 201, (
        drug_response.get_json()
    )

    drug_id = drug_response.get_json()["data"]["id"]

    expiry_date = (
        datetime.now(timezone.utc).date()
        + timedelta(days=365)
    )

    batch_response = client.post(
        "/api/v1/pharmacy/batches",
        json={
            "drug_id": drug_id,
            "batch_number": "GATE11-S2-001",
            "quantity_on_hand": 100,
            "expiry_date": expiry_date.isoformat(),
            "reorder_level": 10,
        },
        headers=_auth(admin_login),
    )

    assert batch_response.status_code == 201, (
        batch_response.get_json()
    )

    batch_id = batch_response.get_json()["data"]["id"]

    prescription_response = client.post(
        "/api/v1/prescriptions",
        json={
            "patient_id": patient_id,
            "consultation_id": consultation_id,
            "items": [
                {
                    "drug_id": drug_id,
                    "dosage": "500 mg",
                    "frequency": "twice daily",
                    "duration": "5 days",
                    "quantity": 10,
                    "instructions": "Take after meals",
                }
            ],
            "notes": (
                "Gate 11 Slice 2 cross-domain workflow"
            ),
        },
        headers=_auth(doctor_login),
    )

    assert prescription_response.status_code == 201, (
        prescription_response.get_json()
    )

    prescription_body = (
        prescription_response.get_json()["data"]
    )

    prescription_id = prescription_body["id"]
    prescription_item_id = prescription_body["items"][0]["id"]

    dispense_response = client.post(
        "/api/v1/pharmacy/dispense",
        json={
            "prescription_id": prescription_id,
            "items": [
                {
                    "prescription_item_id": prescription_item_id,
                    "batch_id": batch_id,
                    "quantity": 10,
                }
            ],
            "notes": (
                "Gate 11 Slice 2 completed pharmacy step"
            ),
        },
        headers=_auth(pharmacist_login),
    )

    assert dispense_response.status_code == 201, (
        dispense_response.get_json()
    )

    dispense_body = dispense_response.get_json()

    assert dispense_body["success"] is True

    dispense_id = dispense_body["data"]["id"]

    # ============================================================
    # BILLING
    # ============================================================

    invoice_response = client.post(
        "/api/v1/billing/invoices",
        json={
            "patient_id": patient_id,
            "currency": "NGN",
            "appointment_id": appointment_id,
            "due_date": (
                date.today()
                + timedelta(days=7)
            ).isoformat(),
            "items": [
                {
                    "description": (
                        "Gate 11 Slice 2 consultation"
                    ),
                    "quantity": 1,
                    "unit_price": "100.00",
                },
                {
                    "description": (
                        "Gate 11 Slice 2 pharmacy"
                    ),
                    "quantity": 1,
                    "unit_price": "50.00",
                },
            ],
        },
        headers=_auth(admin_login),
    )

    assert invoice_response.status_code == 201, (
        invoice_response.get_json()
    )

    invoice_body = invoice_response.get_json()

    assert invoice_body["success"] is True
    assert invoice_body["data"]["clinic_id"] == clinic.id
    assert invoice_body["data"]["patient_id"] == patient_id
    assert (
        invoice_body["data"]["appointment_id"]
        == appointment_id
    )
    assert invoice_body["data"]["total_amount"] == "150.00"
    assert invoice_body["data"]["status"] == "issued"

    invoice_id = invoice_body["data"]["id"]

    persisted_invoice = db.session.get(
        Invoice,
        invoice_id,
    )

    persisted_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    persisted_dispense = db.session.get(
        DispenseRecord,
        dispense_id,
    )

    assert persisted_invoice is not None
    assert persisted_invoice.clinic_id == clinic.id
    assert persisted_invoice.patient_id == patient_id
    assert (
        persisted_invoice.status
        is InvoiceStatus.ISSUED
    )

    assert persisted_batch is not None
    assert persisted_batch.clinic_id == clinic.id
    assert persisted_batch.quantity_on_hand == 90

    assert persisted_dispense is not None
    assert persisted_dispense.prescription_id == (
        prescription_id
    )
    assert persisted_dispense.dispensed_by_id == (
        pharmacist_staff.id
    )

    # ============================================================
    # NOTIFICATION CREATION
    # ============================================================

    notification_response = client.post(
        "/api/v1/notifications/",
        json={
            "user_id": pharmacist_login["user_id"],
            "title": "Prescription dispensed",
            "message": (
                "Gate 11 Slice 2 notification "
                "for completed pharmacy and billing workflow."
            ),
            "notification_type": "pharmacy",
            "priority": "high",
            "channel": "push",
            "reference_type": "DispenseRecord",
            "reference_id": dispense_id,
        },
        headers=_auth(pharmacist_login),
    )

    assert notification_response.status_code == 201, (
        notification_response.get_json()
    )

    notification_body = (
        notification_response.get_json()
    )

    assert notification_body["success"] is True
    assert notification_body["data"]["clinic_id"] == clinic.id
    assert notification_body["data"]["user_id"] == (
        pharmacist_login["user_id"]
    )
    assert notification_body["data"]["channel"] == "push"
    assert notification_body["data"]["status"] == "pending"
    assert notification_body["data"]["reference_type"] == (
        "DispenseRecord"
    )
    assert notification_body["data"]["reference_id"] == (
        dispense_id
    )

    notification_id = notification_body["data"]["id"]

    persisted_notification = db.session.get(
        Notification,
        notification_id,
    )

    assert persisted_notification is not None
    assert persisted_notification.clinic_id == clinic.id
    assert persisted_notification.user_id == (
        pharmacist_login["user_id"]
    )
    assert persisted_notification.status is (
        NotificationStatus.PENDING
    )
    assert persisted_notification.reference_id == (
        dispense_id
    )

    audit_rows = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.entity_type == "Notification",
                AuditLog.entity_id == notification_id,
            )
            .order_by(AuditLog.id.asc())
        )
        .scalars()
        .all()
    )

    assert any(
        row.clinic_id == clinic.id
        and row.action is AuditAction.CREATE
        for row in audit_rows
    )

    # ============================================================
    # FAILURE INJECTION
    # PROVIDER ACCEPTS ONCE, THEN NETWORK OUTCOME IS UNKNOWN
    # ============================================================

    provider = Gate11NotificationProvider()

    monkeypatch.setattr(
        notification_service,
        "get_notification_provider",
        lambda channel, *, clinic_id: provider,
    )

    with pytest.raises(
        TimeoutError,
        match=(
            "Gate 11 notification provider "
            "timeout after acceptance"
        ),
    ):
        notification_service.deliver_notification(
            clinic.id,
            notification_id,
        )

    failed_notification = db.session.get(
        Notification,
        notification_id,
    )

    assert failed_notification is not None
    assert failed_notification.clinic_id == clinic.id
    assert failed_notification.user_id == (
        pharmacist_login["user_id"]
    )
    assert failed_notification.status is (
        NotificationStatus.FAILED
    )
    assert failed_notification.retry_count == 1
    assert failed_notification.error_message == (
        "Gate 11 notification provider "
        "timeout after acceptance"
    )
    assert failed_notification.delivered_at is None

    stable_key = (
        f"clinic-notification-"
        f"{clinic.id}-"
        f"{notification_id}"
    )

    assert provider.calls == [stable_key]
    assert provider.logical_deliveries == [
        notification_id
    ]

    # Earlier clinical, pharmacy, and billing state remains committed.
    db.session.expire_all()

    recovered_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    recovered_dispense = db.session.get(
        DispenseRecord,
        dispense_id,
    )

    recovered_invoice = db.session.get(
        Invoice,
        invoice_id,
    )

    assert recovered_batch is not None
    assert recovered_batch.quantity_on_hand == 90

    assert recovered_dispense is not None
    assert recovered_dispense.prescription_id == (
        prescription_id
    )

    assert recovered_invoice is not None
    assert recovered_invoice.clinic_id == clinic.id
    assert recovered_invoice.status is (
        InvoiceStatus.ISSUED
    )

    # ============================================================
    # RECOVERY
    # ============================================================

    result = notification_service.deliver_notification(
        clinic.id,
        notification_id,
    )

    assert result is True

    final_notification = db.session.get(
        Notification,
        notification_id,
    )

    assert final_notification is not None
    assert final_notification.clinic_id == clinic.id
    assert final_notification.user_id == (
        pharmacist_login["user_id"]
    )
    assert final_notification.status is (
        NotificationStatus.DELIVERED
    )
    assert final_notification.retry_count == 1
    assert final_notification.delivered_at is not None
    assert final_notification.error_message is None

    assert provider.calls == [
        stable_key,
        stable_key,
    ]

    assert provider.logical_deliveries == [
        notification_id
    ]

    # Exactly one pharmacy dispense remains after recovery.
    dispense_rows = (
        db.session.execute(
            db.select(DispenseRecord)
            .where(
                DispenseRecord.prescription_id
                == prescription_id,
            )
            .order_by(DispenseRecord.id.asc())
        )
        .scalars()
        .all()
    )

    assert len(dispense_rows) == 1
    assert dispense_rows[0].id == dispense_id

    final_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    assert final_batch is not None
    assert final_batch.quantity_on_hand == 90