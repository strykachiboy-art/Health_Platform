from __future__ import annotations

import os
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import redis
from cryptography.fernet import Fernet
from kombu.exceptions import OperationalError
from sqlalchemy import delete, select

from app import create_app
from app import models_registry  # noqa: F401
from app.core.audit.models.audit_model import AuditLog
from app.core.auth.user.models.user_model import User
from app.core.enums.appointment_enums import (
    AppointmentStatus,
    AppointmentType,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.consultation_enums import (
    ConsultationStatus,
    ConsultationType,
)
from app.core.enums.notification_enums import (
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)
from app.core.enums.patient_enums import BloodType
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.notifications.models.notification_models import (
    Notification,
)
from app.core.notifications.services import (
    notification_service,
)
from app.extensions import celery, db
from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.consultation.models.consultation_model import (
    Consultation,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff


REDIS_CONTAINER = "clinic-gate11-s8-redis"
REDIS_IMAGE = "redis:8"
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 56389
REDIS_DB = 0

POLL_INTERVAL_SECONDS = 0.25
POLL_TIMEOUT_SECONDS = 20


def _docker(
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        text=True,
        capture_output=True,
    )


def _stop_redis() -> None:
    _docker(
        "rm",
        "-f",
        REDIS_CONTAINER,
        check=False,
    )


def _assert_port_free() -> None:
    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    ) as sock:
        sock.settimeout(0.5)

        if sock.connect_ex(
            (
                REDIS_HOST,
                REDIS_PORT,
            )
        ) == 0:
            raise AssertionError(
                "REDIS_PORT_ALREADY_IN_USE="
                f"{REDIS_HOST}:{REDIS_PORT}"
            )


def _redis_url() -> str:
    return (
        f"redis://{REDIS_HOST}:"
        f"{REDIS_PORT}/{REDIS_DB}"
    )


def _start_redis() -> None:
    _stop_redis()
    _assert_port_free()

    result = _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        REDIS_CONTAINER,
        "-p",
        f"{REDIS_HOST}:{REDIS_PORT}:6379",
        REDIS_IMAGE,
    )

    container_id = result.stdout.strip()

    if not container_id:
        raise AssertionError(
            "REDIS_CONTAINER_ID_MISSING"
        )


def _wait_for_redis(
    redis_url: str,
) -> redis.Redis:
    client = redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )

    deadline = (
        time.monotonic()
        + POLL_TIMEOUT_SECONDS
    )

    last_error = None

    while time.monotonic() < deadline:
        try:
            client.ping()
            client.flushdb()
            return client
        except Exception as exc:
            last_error = exc

        time.sleep(
            POLL_INTERVAL_SECONDS
        )

    client.close()

    raise AssertionError(
        "REDIS_READY_TIMEOUT "
        f"LAST_ERROR={last_error!r}"
    )


def _seed_clinical_domain(
    unique: str,
) -> dict:
    clinic = Clinic(
        name=(
            "Gate 11 Slice 8 "
            f"Redis Clinic {unique[:8]}"
        ),
        status=ClinicStatus.ACTIVE,
        ai_credits=5,
    )

    db.session.add(clinic)
    db.session.flush()

    user = User(
        clinic_id=clinic.id,
        email=(
            f"gate11-s8-{unique}"
            "@test.invalid"
        ),
        role=Role.ADMIN,
        is_active=True,
        token_version=0,
    )

    db.session.add(user)
    db.session.flush()

    staff = Staff(
        clinic_id=clinic.id,
        user_id=user.id,
        first_name="Gate11",
        last_name="RedisFailure",
        email=user.email,
        status=StaffStatus.ACTIVE,
    )

    db.session.add(staff)
    db.session.flush()

    patient = Patient(
        clinic_id=clinic.id,
        first_name="Redis",
        last_name="Failure Patient",
        blood_type=BloodType.UNKNOWN,
        patient_number=(
            f"G11-S8-{unique[:12]}"
        ),
        is_active=True,
        email=(
            f"patient-{unique}"
            "@test.invalid"
        ),
    )

    db.session.add(patient)
    db.session.flush()

    scheduled_start = (
        datetime.now(timezone.utc)
        + timedelta(minutes=30)
    )

    appointment = Appointment(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
        scheduled_start=scheduled_start,
        scheduled_end=(
            scheduled_start
            + timedelta(minutes=30)
        ),
        status=AppointmentStatus.SCHEDULED,
        appointment_type=AppointmentType.IN_PERSON,
        reason=(
            "Gate 11 Slice 8 "
            "real Redis broker interruption"
        ),
    )

    db.session.add(appointment)
    db.session.flush()

    consultation = Consultation(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
        appointment_id=appointment.id,
        consultation_type=ConsultationType.GENERAL,
        status=ConsultationStatus.IN_PROGRESS,
        chief_complaint="Redis broker recovery",
        symptoms=(
            "Clinical state must survive "
            "queue infrastructure failure"
        ),
        started_at=datetime.now(timezone.utc),
    )

    db.session.add(consultation)
    db.session.commit()

    return {
        "clinic_id": clinic.id,
        "user_id": user.id,
        "staff_id": staff.id,
        "patient_id": patient.id,
        "appointment_id": appointment.id,
        "consultation_id": consultation.id,
    }


def _clinical_state(
    *,
    clinic_id: int,
    patient_id: int,
    appointment_id: int,
    consultation_id: int,
) -> dict:
    clinic = db.session.get(
        Clinic,
        clinic_id,
    )
    patient = db.session.get(
        Patient,
        patient_id,
    )
    appointment = db.session.get(
        Appointment,
        appointment_id,
    )
    consultation = db.session.get(
        Consultation,
        consultation_id,
    )

    return {
        "clinic_exists": clinic is not None,
        "patient_exists": patient is not None,
        "appointment_exists": appointment is not None,
        "consultation_exists": consultation is not None,
        "appointment_status": (
            appointment.status
            if appointment is not None
            else None
        ),
        "consultation_status": (
            consultation.status
            if consultation is not None
            else None
        ),
    }


def _cleanup_clinic(
    clinic_id: int,
    notification_id: int,
) -> None:
    # Production audit rows are immutable. This Phase 9 E2E runs against a dedicated disposable database; database teardown owns row cleanup.
    return None


def test_gate11_cross_domain_real_redis_failure_recovers_without_duplicate_state(
    monkeypatch,
):
    database_url = os.environ.get(
        "PHASE9_TEST_DATABASE_URL"
    )

    if not database_url:
        pytest.fail(
            "PHASE9_TEST_DATABASE_URL is required"
        )

    redis_url = _redis_url()

    _start_redis()

    app = None
    redis_client = None
    clinic_id = None
    notification_id = None

    try:
        redis_client = _wait_for_redis(
            redis_url
        )

        monkeypatch.setenv(
            "FLASK_ENV",
            "production",
        )
        monkeypatch.setenv(
            "DATABASE_URL",
            database_url,
        )
        monkeypatch.setenv(
            "REDIS_URL",
            redis_url,
        )
        monkeypatch.setenv(
            "CELERY_BROKER_URL",
            redis_url,
        )
        monkeypatch.setenv(
            "CELERY_RESULT_BACKEND",
            "cache+memory://",
        )
        monkeypatch.setenv(
            "SECRET_KEY",
            "gate11-slice8-disposable-secret",
        )
        monkeypatch.setenv(
            "JWT_SECRET_KEY",
            "gate11-slice8-disposable-jwt-secret",
        )
        monkeypatch.setenv(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost",
        )
        monkeypatch.setenv(
            "INTEGRATION_ENCRYPTION_KEY",
            Fernet.generate_key().decode(),
        )

        app = create_app(
            "production"
        )

        original_publish_retry = (
            celery.conf.get(
                "task_publish_retry",
                True,
            )
        )

        with app.app_context():
            celery.conf.update(
                task_publish_retry=False,
                broker_connection_retry=False,
            )

            seeded = _seed_clinical_domain(
                uuid4().hex
            )

            clinic_id = seeded["clinic_id"]

            baseline_clinical_state = (
                _clinical_state(
                    clinic_id=clinic_id,
                    patient_id=(
                        seeded["patient_id"]
                    ),
                    appointment_id=(
                        seeded["appointment_id"]
                    ),
                    consultation_id=(
                        seeded["consultation_id"]
                    ),
                )
            )

            notification = (
                notification_service.create_notification(
                    clinic_id=clinic_id,
                    user_id=seeded["user_id"],
                    title=(
                        "Gate 11 Slice 8 "
                        "Redis recovery"
                    ),
                    message=(
                        "Real Redis broker "
                        "failure recovery"
                    ),
                    notification_type=(
                        NotificationType.CONSULTATION
                    ),
                    priority=(
                        NotificationPriority.NORMAL
                    ),
                    channel=(
                        NotificationChannel.EMAIL
                    ),
                    reference_type="Consultation",
                    reference_id=(
                        seeded["consultation_id"]
                    ),
                    actor_user_id=seeded["user_id"],
                )
            )

            notification_id = notification.id

            create_audits = list(
                db.session.scalars(
                    select(
                        AuditLog
                    ).where(
                        AuditLog.clinic_id
                        == clinic_id,
                        AuditLog.entity_type
                        == "Notification",
                        AuditLog.entity_id
                        == notification_id,
                        AuditLog.action
                        == AuditAction.CREATE,
                    )
                )
            )

            assert len(create_audits) == 1

            redis_client.close()
            redis_client = None

            _stop_redis()

            with pytest.raises(
                OperationalError,
            ):
                notification_service.queue_notification_delivery(
                    notification_id,
                    clinic_id=clinic_id,
                    user_id=seeded["user_id"],
                )

            db.session.refresh(
                notification
            )

            assert notification.status == (
                NotificationStatus.PENDING
            )
            assert notification.retry_count == 0
            assert notification.sent_at is None
            assert notification.delivered_at is None
            assert notification.failed_at is None

            pending_rows = list(
                db.session.scalars(
                    select(
                        Notification
                    ).where(
                        Notification.clinic_id
                        == clinic_id,
                        Notification.id
                        == notification_id,
                    )
                )
            )

            assert len(pending_rows) == 1

            assert (
                _clinical_state(
                    clinic_id=clinic_id,
                    patient_id=(
                        seeded["patient_id"]
                    ),
                    appointment_id=(
                        seeded["appointment_id"]
                    ),
                    consultation_id=(
                        seeded["consultation_id"]
                    ),
                )
                == baseline_clinical_state
            )

            _start_redis()

            redis_client = _wait_for_redis(
                redis_url
            )

            queued = (
                notification_service
                .queue_notification_delivery(
                    notification_id,
                    clinic_id=clinic_id,
                    user_id=seeded["user_id"],
                )
            )

            assert queued.id == notification_id

            queue_name = (
                celery.conf.get(
                    "task_default_queue"
                )
                or "celery"
            )

            assert redis_client.llen(
                queue_name
            ) == 1

            db.session.refresh(
                notification
            )

            assert notification.status == (
                NotificationStatus.PENDING
            )
            assert notification.retry_count == 0
            assert notification.sent_at is None
            assert notification.delivered_at is None
            assert notification.failed_at is None

            notification_rows = list(
                db.session.scalars(
                    select(
                        Notification
                    ).where(
                        Notification.clinic_id
                        == clinic_id
                    )
                )
            )

            assert len(notification_rows) == 1
            assert notification_rows[0].id == (
                notification_id
            )

            assert (
                _clinical_state(
                    clinic_id=clinic_id,
                    patient_id=(
                        seeded["patient_id"]
                    ),
                    appointment_id=(
                        seeded["appointment_id"]
                    ),
                    consultation_id=(
                        seeded["consultation_id"]
                    ),
                )
                == baseline_clinical_state
            )

            final_audits = list(
                db.session.scalars(
                    select(
                        AuditLog
                    ).where(
                        AuditLog.clinic_id
                        == clinic_id,
                        AuditLog.entity_type
                        == "Notification",
                        AuditLog.entity_id
                        == notification_id,
                    )
                )
            )

            assert len(final_audits) == 1
            assert final_audits[0].action is (
                AuditAction.CREATE
            )

            celery.conf.update(
                task_publish_retry=(
                    original_publish_retry
                )
            )

    finally:
        _stop_redis()

        if app is not None:
            with app.app_context():
                if clinic_id is not None and (
                    notification_id is not None
                ):
                    _cleanup_clinic(
                        clinic_id,
                        notification_id,
                    )

                db.session.commit()

                db.session.remove()

                db.engine.dispose()
