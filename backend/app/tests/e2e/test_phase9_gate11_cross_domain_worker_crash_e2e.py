from __future__ import annotations

import os
import json
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest
from celery import Celery
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app.core.audit.models.audit_model import AuditLog
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
from app import models_registry  # noqa: F401
from app.core.auth.user.models.user_model import User
from app.core.notifications.models.notification_models import (
    Notification,
)
from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.consultation.models.consultation_model import (
    Consultation,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.settings.models.integration_config import (
    IntegrationConfig,
)
from app.modules.staff.models.staff_model import Staff


VISIBILITY_TIMEOUT_SECONDS = 3
POLL_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 0.25


class _SMTPState:
    def __init__(self):
        self.lock = threading.Lock()
        self.attempts: list[str] = []
        self.logical_deliveries: list[str] = []
        self.first_accept_event = threading.Event()
        self.release_first = threading.Event()


class _BlockingSMTPHandler(socketserver.StreamRequestHandler):
    def _send(self, payload: bytes):
        self.wfile.write(payload)
        self.wfile.flush()

    def handle(self):
        state: _SMTPState = self.server.state  # type: ignore[attr-defined]

        self.request.settimeout(60)

        self._send(
            b"220 gate11-slice6.test ESMTP ClinicSystem\r\n"
        )

        while True:
            line = self.rfile.readline()

            if not line:
                return

            command = line.decode(
                "ascii",
                errors="ignore",
            ).strip()

            if not command:
                continue

            verb = command.split(
                " ",
                1,
            )[0].upper()

            if verb in {"EHLO", "HELO"}:
                self._send(
                    b"250-gate11-slice6.test\r\n"
                    b"250-8BITMIME\r\n"
                    b"250 OK\r\n"
                )
                continue

            if verb == "MAIL":
                self._send(b"250 OK\r\n")
                continue

            if verb == "RCPT":
                self._send(b"250 OK\r\n")
                continue

            if verb == "DATA":
                self._send(
                    b"354 End data with <CR><LF>.<CR><LF>\r\n"
                )

                chunks = []

                while True:
                    data_line = self.rfile.readline()

                    if not data_line:
                        return

                    if data_line.rstrip(
                        b"\r\n"
                    ) == b".":
                        break

                    chunks.append(data_line)

                raw_message = b"".join(chunks)

                parsed = BytesParser(
                    policy=policy.default,
                ).parsebytes(raw_message)

                message_id = parsed["Message-ID"]

                if not isinstance(message_id, str):
                    raise AssertionError(
                        "SMTP message did not contain Message-ID"
                    )

                stable_key = message_id.strip()

                with state.lock:
                    state.attempts.append(
                        stable_key,
                    )

                    first_attempt = (
                        len(state.attempts) == 1
                    )

                    if stable_key not in (
                        {
                            item
                            for item in state.logical_deliveries
                        }
                    ):
                        state.logical_deliveries.append(
                            stable_key,
                        )

                if first_attempt:
                    state.first_accept_event.set()

                    # The SMTP server has accepted the message body,
                    # but intentionally withholds the final 250 response.
                    #
                    # The worker therefore has an UNKNOWN provider outcome:
                    # provider accepted the message, while the local process
                    # has not yet acknowledged delivery.
                    state.release_first.wait(
                        timeout=60,
                    )

                self._send(
                    b"250 2.0.0 accepted\r\n"
                )
                continue

            if verb == "RSET":
                self._send(b"250 OK\r\n")
                continue

            if verb == "NOOP":
                self._send(b"250 OK\r\n")
                continue

            if verb == "QUIT":
                self._send(b"221 Bye\r\n")
                return

            self._send(b"250 OK\r\n")


class _BlockingSMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, state):
        self.state = state
        super().__init__(
            server_address,
            _BlockingSMTPHandler,
        )


def _wait_until(
    predicate,
    *,
    timeout=POLL_TIMEOUT_SECONDS,
    description="condition",
):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return

        time.sleep(POLL_INTERVAL_SECONDS)

    raise AssertionError(
        f"Timed out waiting for {description}"
    )


def _worker_env(
    *,
    database_url: str,
    redis_url: str,
    encryption_key: str,
):
    repo_root = Path(__file__).resolve().parents[4]
    backend_root = repo_root / "backend"

    env = os.environ.copy()

    env.update(
        {
            "FLASK_ENV": "production",
            "DATABASE_URL": database_url,
            "REDIS_URL": redis_url,
            "CELERY_BROKER_URL": redis_url,
            "CELERY_RESULT_BACKEND": redis_url,
            "SECRET_KEY": (
                "gate11-slice6-disposable-secret"
            ),
            "JWT_SECRET_KEY": (
                "gate11-slice6-disposable-jwt-secret"
            ),
            "CORS_ALLOWED_ORIGINS": (
                "http://localhost"
            ),
            "INTEGRATION_ENCRYPTION_KEY": encryption_key,
            "PYTHONPATH": str(backend_root),
            "PYTHONUNBUFFERED": "1",
            "GATE11_WORKER_LOG_PATH": str(
                Path(
                    env.get(
                        "TEMP",
                        str(repo_root),
                    )
                )
                / f"gate11-s6-worker-{uuid4().hex}.log"
            ),
        }
    )

    return env, backend_root


class _DockerWorkerHandle:
    def __init__(
        self,
        container_name,
        wait_process,
    ):
        self.container_name = container_name
        self.wait_process = wait_process

    def poll(self):
        return self.wait_process.poll()

    def wait(self, timeout=None):
        return self.wait_process.wait(
            timeout=timeout
        )

    @property
    def returncode(self):
        return self.wait_process.returncode


def _read_worker_log(worker):
    container_name = getattr(
        worker,
        "container_name",
        None,
    )

    if not container_name:
        return "<worker container name unavailable>"

    result = subprocess.run(
        [
            "docker",
            "logs",
            container_name,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    output = (
        (result.stdout or "")
        + (result.stderr or "")
    )

    return output[-16000:]


def _start_worker(
    *,
    database_url: str,
    redis_url: str,
    encryption_key: str,
):
    worker_image = "clinic-system-phase9:49ae8cc"

    container_name = (
        f"gate11-s6-worker-{uuid4().hex[:12]}"
    )

    worker_database_url = (
        database_url
        .replace(
            "127.0.0.1",
            "host.docker.internal",
        )
        .replace(
            "localhost",
            "host.docker.internal",
        )
    )

    worker_redis_url = (
        redis_url
        .replace(
            "127.0.0.1",
            "host.docker.internal",
        )
        .replace(
            "localhost",
            "host.docker.internal",
        )
    )

    launcher = r"""
import celery_worker

celery_worker.celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={
        "visibility_timeout": 3,
    },
    result_expires=30,
)

print(
    "GATE11_WORKER_CONFIG="
    + repr(
        celery_worker.celery.conf.broker_transport_options
    ),
    flush=True,
)

celery_worker.celery.worker_main(
    [
        "worker",
        "--concurrency=1",
        "--loglevel=INFO",
        "--without-gossip",
        "--without-mingle",
        "--hostname=gate11-s6-worker@%h",
    ]
)
""".strip()

    start = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "--add-host",
            "host.docker.internal:host-gateway",
            "-e",
            "FLASK_ENV=production",
            "-e",
            f"DATABASE_URL={worker_database_url}",
            "-e",
            f"REDIS_URL={worker_redis_url}",
            "-e",
            f"CELERY_BROKER_URL={worker_redis_url}",
            "-e",
            f"CELERY_RESULT_BACKEND={worker_redis_url}",
            "-e",
            "SECRET_KEY=gate11-slice6-disposable-secret",
            "-e",
            "JWT_SECRET_KEY=gate11-slice6-disposable-jwt-secret",
            "-e",
            "CORS_ALLOWED_ORIGINS=http://localhost",
            "-e",
            f"INTEGRATION_ENCRYPTION_KEY={encryption_key}",
            "-e",
            "PYTHONPATH=/app",
            worker_image,
            "python",
            "-c",
            launcher,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    if start.returncode != 0:
        raise AssertionError(
            "GATE11_WORKER_CONTAINER_START_FAILED\n"
            f"STDOUT={start.stdout}\n"
            f"STDERR={start.stderr}"
        )

    wait_process = subprocess.Popen(
        [
            "docker",
            "wait",
            container_name,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    worker = _DockerWorkerHandle(
        container_name,
        wait_process,
    )

    deadline = time.monotonic() + 45

    while time.monotonic() < deadline:
        if worker.poll() is not None:
            raise AssertionError(
                "GATE11_WORKER_EXITED_BEFORE_READY\n"
                f"RETURN_CODE={worker.returncode}\n"
                f"LOG=\n{_read_worker_log(worker)}"
            )

        log = _read_worker_log(worker)

        if " ready." in log:
            return worker

        time.sleep(0.25)

    raise AssertionError(
        "GATE11_WORKER_READY_TIMEOUT\n"
        f"LOG=\n{_read_worker_log(worker)}"
    )


def _kill_worker(worker):
    container_name = getattr(
        worker,
        "container_name",
        None,
    )

    if container_name:
        subprocess.run(
            [
                "docker",
                "rm",
                "-f",
                container_name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )

    try:
        worker.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _get_notification(
    engine,
    notification_id,
):
    with Session(engine) as session:
        notification = session.get(
            Notification,
            notification_id,
        )

        if notification is None:
            return None

        return {
            "status": notification.status,
            "retry_count": notification.retry_count,
            "sent_at": notification.sent_at,
            "delivered_at": notification.delivered_at,
            "error_message": notification.error_message,
        }


def _get_clinical_state(
    engine,
    *,
    clinic_id,
    patient_id,
    appointment_id,
    consultation_id,
):
    with Session(engine) as session:
        clinic = session.get(
            Clinic,
            clinic_id,
        )

        patient = session.get(
            Patient,
            patient_id,
        )

        appointment = session.get(
            Appointment,
            appointment_id,
        )

        consultation = session.get(
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


def _cleanup(
    engine,
    *,
    clinic_id,
    notification_id,
    patient_id,
    appointment_id,
    consultation_id,
    user_id,
    staff_id,
    integration_id,
):
    # Production audit rows are immutable. This Phase 9 E2E runs against a dedicated disposable database; database teardown owns row cleanup.
    return None


def test_gate11_cross_domain_real_worker_crash_unknown_outcome_recovers():
    database_url = os.environ.get(
        "PHASE9_TEST_DATABASE_URL"
    )
    redis_url = os.environ.get(
        "TEST_REDIS_URL"
    )

    if not database_url:
        pytest.fail(
            "PHASE9_TEST_DATABASE_URL is required"
        )

    if not redis_url:
        pytest.fail(
            "TEST_REDIS_URL is required"
        )

    encryption_key = Fernet.generate_key().decode(
        "utf-8"
    )

    engine = create_engine(
        database_url,
        pool_pre_ping=True,
    )

    smtp_state = _SMTPState()

    smtp_server = _BlockingSMTPServer(
        (
            "0.0.0.0",
            0,
        ),
        smtp_state,
    )

    smtp_thread = threading.Thread(
        target=smtp_server.serve_forever,
        daemon=True,
    )
    smtp_thread.start()

    smtp_port = smtp_server.server_address[1]

    worker_processes = []

    clinic_id = None
    user_id = None
    staff_id = None
    patient_id = None
    appointment_id = None
    consultation_id = None
    notification_id = None
    integration_id = None

    try:
        unique = uuid4().hex

        with Session(engine) as session:
            clinic = Clinic(
                name=(
                    "Gate 11 Slice 6 "
                    f"Worker Crash Clinic {unique[:8]}"
                ),
                status=ClinicStatus.ACTIVE,
                ai_credits=5,
            )

            session.add(clinic)
            session.flush()

            user = User(
                clinic_id=clinic.id,
                email=(
                    f"gate11-s6-{unique}"
                    "@test.invalid"
                ),
                role=Role.ADMIN,
                is_active=True,
                token_version=0,
            )

            session.add(user)
            session.flush()

            staff = Staff(
                clinic_id=clinic.id,
                user_id=user.id,
                first_name="Gate11",
                last_name="WorkerCrash",
                email=user.email,
                status=StaffStatus.ACTIVE,
            )

            session.add(staff)
            session.flush()

            patient = Patient(
                clinic_id=clinic.id,
                first_name="Gate11",
                last_name="Worker Crash Patient",
                blood_type=BloodType.UNKNOWN,
                patient_number=f"G11-S6-{unique[:12]}",
                is_active=True,
                email=f"patient-{unique}@test.invalid",
            )

            session.add(patient)
            session.flush()

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
                    "Gate 11 Slice 6 "
                    "real worker interruption"
                ),
            )

            session.add(appointment)
            session.flush()

            consultation = Consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                appointment_id=appointment.id,
                consultation_type=ConsultationType.GENERAL,
                status=ConsultationStatus.IN_PROGRESS,
                chief_complaint=(
                    "Worker crash unknown outcome"
                ),
                symptoms=(
                    "SMTP provider accepted before "
                    "worker acknowledgement"
                ),
                started_at=datetime.now(timezone.utc),
            )

            session.add(consultation)
            session.flush()

            notification = Notification(
                clinic_id=clinic.id,
                user_id=user.id,
                title="Gate 11 worker-crash notification",
                message=(
                    "Provider acceptance occurred before "
                    "local acknowledgement."
                ),
                notification_type=NotificationType.SYSTEM,
                priority=NotificationPriority.HIGH,
                channel=NotificationChannel.EMAIL,
                status=NotificationStatus.PENDING,
                reference_type="Consultation",
                reference_id=consultation.id,
                is_read=False,
                retry_count=0,
            )

            session.add(notification)
            session.flush()

            credentials = {
                "host": "host.docker.internal",
                "port": smtp_port,
                "from_email": "no-reply@clinic-system.local",
                "use_ssl": False,
                "use_tls": False,
            }

            integration = IntegrationConfig(
            clinic_id=clinic.id,
            provider="email",
            is_enabled=True,
            configuration={},
            encrypted_credentials=(
                Fernet(
                    encryption_key.encode("utf-8")
                ).encrypt(
                    json.dumps(
                        credentials,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).decode("utf-8")
            ),
            credentials_version=1,
        )

            session.add(integration)
            session.flush()

            audit = AuditLog(
                clinic_id=clinic.id,
                user_id=user.id,
                action=AuditAction.CREATE,
                entity_type="Notification",
                entity_id=notification.id,
                description=(
                    "Gate 11 Slice 6 notification "
                    "created for worker crash test"
                ),
                new_value={
                    "clinic_id": clinic.id,
                    "user_id": user.id,
                    "reference_type": "Consultation",
                    "reference_id": consultation.id,
                },
            )

            session.add(audit)
            session.commit()

            clinic_id = clinic.id
            user_id = user.id
            staff_id = staff.id
            patient_id = patient.id
            appointment_id = appointment.id
            consultation_id = consultation.id
            notification_id = notification.id
            integration_id = integration.id

        producer = Celery(
            "gate11-slice6-producer",
            broker=redis_url,
            backend=redis_url,
        )

        first_worker = _start_worker(
            database_url=database_url,
            redis_url=redis_url,
            encryption_key=encryption_key,
        )
        worker_processes.append(first_worker)

        task_result = producer.send_task(
            "deliver_notification",
            args=[
                clinic_id,
                notification_id,
            ],
        )

        _wait_until(
            lambda: smtp_state.first_accept_event.is_set(),
            description=(
                "real worker to reach SMTP provider "
                "and submit the notification"
            ),
        )

        assert smtp_state.attempts == [
            (
                f"<clinic-notification-"
                f"{clinic_id}-"
                f"{notification_id}"
                "@clinic-system.local>"
            )
        ]

        stable_message_id = smtp_state.attempts[0]

        # The external provider has accepted the message body,
        # but the worker is still blocked waiting for the SMTP
        # acknowledgement.
        #
        # This is the critical UNKNOWN outcome boundary.
        _kill_worker(
            first_worker,
        )

        smtp_state.release_first.set()

        _wait_until(
            lambda: first_worker.poll() is not None,
            timeout=10,
            description="first worker process to terminate",
        )

        _wait_until(
            lambda: (
                _get_notification(
                    engine,
                    notification_id,
                )["status"]
                is NotificationStatus.PENDING
            ),
            description=(
                "local notification state to remain "
                "uncommitted after worker loss"
            ),
        )

        pending_state = _get_notification(
            engine,
            notification_id,
        )

        assert pending_state["status"] is (
            NotificationStatus.PENDING
        )
        assert pending_state["retry_count"] == 0
        assert pending_state["delivered_at"] is None

        clinical_state = _get_clinical_state(
            engine,
            clinic_id=clinic_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
            consultation_id=consultation_id,
        )

        assert clinical_state == {
            "clinic_exists": True,
            "patient_exists": True,
            "appointment_exists": True,
            "consultation_exists": True,
            "appointment_status": (
                AppointmentStatus.SCHEDULED
            ),
            "consultation_status": (
                ConsultationStatus.IN_PROGRESS
            ),
        }

        second_worker = _start_worker(
            database_url=database_url,
            redis_url=redis_url,
            encryption_key=encryption_key,
        )
        worker_processes.append(second_worker)

        try:
            _wait_until(
                lambda: (
                    (
                        state := _get_notification(
                            engine,
                            notification_id,
                        )
                    )["status"]
                    is NotificationStatus.DELIVERED
                ),
                timeout=125,
                description=(
                    "replacement worker redelivery to "
                    "complete notification delivery"
                ),
            )
        except AssertionError:
            print(
                "SECOND_WORKER_RETURN_CODE=",
                second_worker.poll(),
            )
            print("SECOND_WORKER_LOG_START")
            print(_read_worker_log(second_worker))
            print("SECOND_WORKER_LOG_END")
            raise

        final_state = _get_notification(
            engine,
            notification_id,
        )

        assert final_state["status"] is (
            NotificationStatus.DELIVERED
        )
        assert final_state["retry_count"] == 0
        assert final_state["delivered_at"] is not None
        assert final_state["error_message"] is None

        assert smtp_state.attempts == [
            stable_message_id,
            stable_message_id,
        ]

        assert smtp_state.logical_deliveries == [
            stable_message_id,
        ]

        assert smtp_state.attempts.count(
            stable_message_id,
        ) == 2

        assert len(
            smtp_state.logical_deliveries
        ) == 1

        with Session(engine) as session:
            notifications = list(
                session.scalars(
                    select(Notification).where(
                        Notification.clinic_id == clinic_id,
                        Notification.reference_type
                        == "Consultation",
                        Notification.reference_id
                        == consultation_id,
                    )
                )
            )

            assert len(notifications) == 1
            assert notifications[0].id == notification_id

            audit_rows = list(
                session.scalars(
                    select(AuditLog).where(
                        AuditLog.clinic_id == clinic_id,
                        AuditLog.entity_type
                        == "Notification",
                        AuditLog.entity_id
                        == notification_id,
                    )
                )
            )

            assert any(
                row.action is AuditAction.CREATE
                for row in audit_rows
            )

        recovered_clinical_state = _get_clinical_state(
            engine,
            clinic_id=clinic_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
            consultation_id=consultation_id,
        )

        assert recovered_clinical_state == (
            clinical_state
        )

        assert task_result.id

    finally:
        for worker in worker_processes:
            if worker.poll() is None:
                _kill_worker(worker)

        smtp_state.release_first.set()

        try:
            smtp_server.shutdown()
        finally:
            smtp_server.server_close()

        if clinic_id is not None:
            _cleanup(
                engine,
                clinic_id=clinic_id,
                notification_id=notification_id,
                patient_id=patient_id,
                appointment_id=appointment_id,
                consultation_id=consultation_id,
                user_id=user_id,
                staff_id=staff_id,
                integration_id=integration_id,
            )

        engine.dispose()