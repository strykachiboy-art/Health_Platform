from __future__ import annotations

import json
import os
import subprocess
import time
from urllib.parse import urlparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from flask import g
from cryptography.fernet import Fernet

import app.core.utils.decorators as auth_decorators
from app.modules.pharmacy.routes import pharmacy_routes
from flask_jwt_extended import create_access_token
from sqlalchemy import delete, select

from app import create_app
from app import models_registry  # noqa: F401
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
from app.core.enums.patient_enums import BloodType
from app.core.enums.pharmacy_enums import (
    DispenseStatus,
    DrugCategory,
)
from app.core.enums.prescription_enums import (
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.extensions import db
from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.consultation.models.consultation_model import (
    Consultation,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import (
    DispenseItem,
    DispenseRecord,
    Drug,
    DrugBatch,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.core.auth.user.models.user_model import User
from app.modules.staff.models.staff_model import Staff


DB_CONTAINER = "clinic-gate11-postgres"
DB_READY_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 0.5


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


def _assert_disposable_database(
    database_url: str,
) -> None:
    try:
        parsed = urlparse(database_url)
        database_port = parsed.port
    except ValueError as exc:
        raise AssertionError(
            "REFUSING_NON_DISPOSABLE_DATABASE="
            f"{database_url}"
        ) from exc

    if (
        parsed.scheme
        not in {
            "postgresql",
            "postgresql+psycopg",
        }
        or parsed.hostname != "127.0.0.1"
        or parsed.username != "gate11"
        or parsed.password != "gate11_test"
        or parsed.path != "/clinic_gate11"
        or database_port is None
        or database_port <= 0
    ):
        raise AssertionError(
            "REFUSING_NON_DISPOSABLE_DATABASE="
            f"{database_url}"
        )

    inspect = _docker(
        "inspect",
        DB_CONTAINER,
        check=False,
    )

    if inspect.returncode != 0:
        raise AssertionError(
            "DISPOSABLE_DB_CONTAINER_MISSING="
            f"{DB_CONTAINER}"
        )

    try:
        container = json.loads(
            inspect.stdout
        )[0]
    except (
        IndexError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise AssertionError(
            "DISPOSABLE_DB_CONTAINER_INSPECT_INVALID="
            f"{DB_CONTAINER}"
        ) from exc

    config = container.get(
        "Config",
        {},
    )
    image = str(
        config.get(
            "Image",
            "",
        )
    )
    environment = set(
        config.get(
            "Env",
            [],
        )
        or []
    )

    if not image.startswith(
        "postgres:18"
    ):
        raise AssertionError(
            "DISPOSABLE_DB_IMAGE_MISMATCH="
            f"{image}"
        )

    required_environment = {
        "POSTGRES_USER=gate11",
        "POSTGRES_PASSWORD=gate11_test",
        "POSTGRES_DB=clinic_gate11",
    }

    if not required_environment.issubset(
        environment
    ):
        raise AssertionError(
            "DISPOSABLE_DB_ENVIRONMENT_MISMATCH"
        )

    port_bindings = (
        container.get(
            "NetworkSettings",
            {},
        )
        .get(
            "Ports",
            {},
        )
        .get(
            "5432/tcp",
        )
        or []
    )

    expected_host_port = str(
        database_port
    )

    matching_binding = any(
        str(
            binding.get(
                "HostPort",
                "",
            )
        )
        == expected_host_port
        and binding.get(
            "HostIp"
        )
        == "127.0.0.1"
        for binding in port_bindings
    )

    if not matching_binding:
        raise AssertionError(
            "DISPOSABLE_DB_PORT_MISMATCH="
            f"{database_url}"
        )


def _container_running() -> bool:
    result = _docker(
        "inspect",
        "-f",
        "{{.State.Running}}",
        DB_CONTAINER,
        check=False,
    )

    return (
        result.returncode == 0
        and result.stdout.strip().lower() == "true"
    )


def _ensure_postgres_running() -> None:
    if _container_running():
        return

    result = _docker(
        "start",
        DB_CONTAINER,
        check=False,
    )

    if result.returncode != 0:
        raise AssertionError(
            "POSTGRES_START_FAILED="
            f"{result.stderr.strip()}"
        )


def _wait_for_postgres() -> None:
    deadline = (
        time.monotonic()
        + DB_READY_TIMEOUT_SECONDS
    )

    last_output = ""

    while time.monotonic() < deadline:
        result = _docker(
            "exec",
            DB_CONTAINER,
            "pg_isready",
            "-U",
            "gate11",
            "-d",
            "clinic_gate11",
            check=False,
        )

        last_output = (
            result.stdout.strip()
            or result.stderr.strip()
        )

        if result.returncode == 0:
            return

        time.sleep(
            POLL_INTERVAL_SECONDS
        )

    raise AssertionError(
        "POSTGRES_READY_TIMEOUT="
        f"{last_output}"
    )


def _stop_postgres() -> None:
    result = _docker(
        "stop",
        DB_CONTAINER,
        check=False,
    )

    if result.returncode != 0:
        raise AssertionError(
            "POSTGRES_STOP_FAILED="
            f"{result.stderr.strip()}"
        )


def _dispose_db_engine() -> None:
    db.session.remove()
    db.engine.dispose()


def _auth_headers(
    app,
    user: User,
) -> dict[str, str]:
    with app.test_request_context():
        token = create_access_token(
            identity=str(user.id),
            additional_claims={
                "role": user.role.value,
                "token_version": user.token_version,
            },
        )

    return {
        "Authorization": (
            f"Bearer {token}"
        ),
    }


def _seed_domain(
    unique: str,
) -> dict[str, int]:
    clinic = Clinic(
        name=(
            "Gate 11 Slice 10 "
            f"Database Clinic {unique[:8]}"
        ),
        status=ClinicStatus.ACTIVE,
        ai_credits=5,
    )
    db.session.add(clinic)
    db.session.flush()

    doctor_user = User(
        clinic_id=clinic.id,
        email=(
            f"gate11-s10-doctor-{unique}"
            "@test.invalid"
        ),
        role=Role.DOCTOR,
        is_active=True,
        token_version=0,
    )
    pharmacist_user = User(
        clinic_id=clinic.id,
        email=(
            f"gate11-s10-pharmacist-{unique}"
            "@test.invalid"
        ),
        role=Role.PHARMACIST,
        is_active=True,
        token_version=0,
    )

    db.session.add_all(
        [
            doctor_user,
            pharmacist_user,
        ]
    )
    db.session.flush()

    doctor_staff = Staff(
        clinic_id=clinic.id,
        user_id=doctor_user.id,
        first_name="Gate11",
        last_name="DatabaseDoctor",
        email=doctor_user.email,
        status=StaffStatus.ACTIVE,
    )
    pharmacist_staff = Staff(
        clinic_id=clinic.id,
        user_id=pharmacist_user.id,
        first_name="Gate11",
        last_name="DatabasePharmacist",
        email=pharmacist_user.email,
        status=StaffStatus.ACTIVE,
    )

    db.session.add_all(
        [
            doctor_staff,
            pharmacist_staff,
        ]
    )
    db.session.flush()

    patient = Patient(
        clinic_id=clinic.id,
        first_name="Gate11",
        last_name="DatabasePatient",
        blood_type=BloodType.UNKNOWN,
        patient_number=(
            f"G11-S10-{unique[:12]}"
        ),
        is_active=True,
        email=(
            f"gate11-s10-patient-{unique}"
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
        staff_id=doctor_staff.id,
        scheduled_start=scheduled_start,
        scheduled_end=(
            scheduled_start
            + timedelta(minutes=30)
        ),
        status=AppointmentStatus.SCHEDULED,
        appointment_type=AppointmentType.IN_PERSON,
        reason=(
            "Gate 11 Slice 10 "
            "real PostgreSQL interruption"
        ),
    )
    db.session.add(appointment)
    db.session.flush()

    consultation = Consultation(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=doctor_staff.id,
        appointment_id=appointment.id,
        consultation_type=ConsultationType.GENERAL,
        status=ConsultationStatus.IN_PROGRESS,
        chief_complaint=(
            "Database failure recovery"
        ),
        symptoms=(
            "Real PostgreSQL infrastructure "
            "interruption"
        ),
        started_at=datetime.now(timezone.utc),
    )
    db.session.add(consultation)
    db.session.flush()

    drug = Drug(
        clinic_id=clinic.id,
        name=(
            f"Gate11 Slice10 Amoxicillin "
            f"{unique[:8]}"
        ),
        generic_name="Amoxicillin",
        category=DrugCategory.OTHER,
        dosage_form="capsule",
        strength="500 mg",
        unit_price=Decimal("5.00"),
        is_controlled=False,
        is_active=True,
    )
    db.session.add(drug)
    db.session.flush()

    batch = DrugBatch(
        clinic_id=clinic.id,
        drug_id=drug.id,
        batch_number=(
            f"G11-S10-{unique[:12]}"
        ),
        quantity_on_hand=100,
        reorder_level=10,
        expiry_date=(
            date.today()
            + timedelta(days=365)
        ),
    )
    db.session.add(batch)
    db.session.flush()

    prescription = Prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        consultation_id=consultation.id,
        prescribed_by_id=doctor_staff.id,
        status=PrescriptionStatus.ACTIVE,
        notes=(
            "Gate 11 Slice 10 "
            "database failure recovery"
        ),
    )
    db.session.add(prescription)
    db.session.flush()

    prescription_item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dosage="500 mg",
        frequency="twice daily",
        duration="5 days",
        quantity=10,
        instructions="Take after meals",
    )
    db.session.add(prescription_item)

    db.session.commit()

    return {
        "clinic_id": clinic.id,
        "doctor_user_id": doctor_user.id,
        "pharmacist_user_id": pharmacist_user.id,
        "doctor_staff_id": doctor_staff.id,
        "pharmacist_staff_id": pharmacist_staff.id,
        "patient_id": patient.id,
        "appointment_id": appointment.id,
        "consultation_id": consultation.id,
        "drug_id": drug.id,
        "batch_id": batch.id,
        "prescription_id": prescription.id,
        "prescription_item_id": (
            prescription_item.id
        ),
    }


def _cleanup_clinic(
    clinic_id: int,
) -> None:
    # Production audit rows are immutable. This Phase 9 E2E runs against a dedicated disposable database; database teardown owns row cleanup.
    return None


def _clinical_state(
    *,
    clinic_id: int,
    patient_id: int,
    appointment_id: int,
    consultation_id: int,
    prescription_id: int,
    batch_id: int,
) -> dict:
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
    prescription = db.session.get(
        Prescription,
        prescription_id,
    )
    batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    dispenses = list(
        db.session.scalars(
            select(
                DispenseRecord
            ).join(
                Prescription,
                DispenseRecord.prescription_id
                == Prescription.id,
            ).where(
                Prescription.clinic_id
                == clinic_id,
                Prescription.id
                == prescription_id,
            )
        )
    )

    return {
        "patient_exists": patient is not None,
        "appointment_exists": appointment is not None,
        "consultation_exists": consultation is not None,
        "prescription_exists": prescription is not None,
        "batch_exists": batch is not None,
        "batch_quantity": (
            batch.quantity_on_hand
            if batch is not None
            else None
        ),
        "dispense_count": len(dispenses),
        "dispense_statuses": [
            record.status
            for record in dispenses
        ],
    }


def test_gate11_cross_domain_real_database_failure_recovers_without_phantom_dispense(
    monkeypatch,
):
    database_url = os.environ.get(
        "PHASE9_TEST_DATABASE_URL"
    )

    if not database_url:
        pytest.fail(
            "PHASE9_TEST_DATABASE_URL is required"
        )

    _assert_disposable_database(
        database_url
    )
    _ensure_postgres_running()
    _wait_for_postgres()

    monkeypatch.setenv(
        "FLASK_ENV",
        "production",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        database_url,
    )

    monkeypatch.setenv(
        "PGCONNECT_TIMEOUT",
        "3",
    )
    monkeypatch.setenv(
        "REDIS_URL",
        os.environ.get(
            "TEST_REDIS_URL",
            "redis://localhost:56379/15",
        ),
    )
    monkeypatch.setenv(
        "CELERY_BROKER_URL",
        os.environ.get(
            "TEST_REDIS_URL",
            "redis://localhost:56379/15",
        ),
    )
    monkeypatch.setenv(
        "CELERY_RESULT_BACKEND",
        "cache+memory://",
    )
    monkeypatch.setenv(
        "SECRET_KEY",
        "gate11-slice10-disposable-secret",
    )
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "gate11-slice10-disposable-jwt-secret",
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

    clinic_id = None
    seeded = None

    try:
        with app.app_context():
            seeded = _seed_domain(
                uuid4().hex
            )

            clinic_id = seeded["clinic_id"]

            baseline = _clinical_state(
                clinic_id=clinic_id,
                patient_id=seeded["patient_id"],
                appointment_id=(
                    seeded["appointment_id"]
                ),
                consultation_id=(
                    seeded["consultation_id"]
                ),
                prescription_id=(
                    seeded["prescription_id"]
                ),
                batch_id=seeded["batch_id"],
            )

            assert baseline == {
                "patient_exists": True,
                "appointment_exists": True,
                "consultation_exists": True,
                "prescription_exists": True,
                "batch_exists": True,
                "batch_quantity": 100,
                "dispense_count": 0,
                "dispense_statuses": [],
            }

            pharmacist = db.session.get(
                User,
                seeded["pharmacist_user_id"],
            )

            assert pharmacist is not None

            pharmacist_staff = db.session.get(
                Staff,
                seeded["pharmacist_staff_id"],
            )

            assert pharmacist_staff is not None

            pharmacist_headers = _auth_headers(
                app,
                pharmacist,
            )

            # Existing auth/tenant outage behavior is covered
            # separately. Keep Slice 10 focused on the real
            # PostgreSQL failure at the pharmacy write boundary.
            def bypass_auth_context():
                g.current_user_id = pharmacist.id
                g.current_user_role = (
                    Role.PHARMACIST.value
                )
                g.current_clinic_id = clinic_id
                g._auth_context_loaded = True

            monkeypatch.setattr(
                auth_decorators,
                "_load_auth_context",
                bypass_auth_context,
            )

            monkeypatch.setattr(
                pharmacy_routes,
                "_get_current_user",
                lambda: pharmacist,
            )

            monkeypatch.setattr(
                pharmacy_routes,
                "_get_current_clinic_id",
                lambda: clinic_id,
            )

            monkeypatch.setattr(
                pharmacy_routes,
                "_get_current_staff",
                lambda: pharmacist_staff,
            )

            client = app.test_client()

            _dispose_db_engine()
            _stop_postgres()

            failed_response = client.post(
                "/api/v1/pharmacy/dispense",
                json={
                    "prescription_id": (
                        seeded["prescription_id"]
                    ),
                    "items": [
                        {
                            "prescription_item_id": (
                                seeded[
                                    "prescription_item_id"
                                ]
                            ),
                            "batch_id": (
                                seeded["batch_id"]
                            ),
                            "quantity": 10,
                        }
                    ],
                    "notes": (
                        "Gate 11 Slice 10 "
                        "real database interruption"
                    ),
                },
                headers=pharmacist_headers,
            )

            assert failed_response.status_code == 500, (
                failed_response.get_json()
            )

            failed_body = (
                failed_response.get_json()
            )

            assert failed_body == {
                "success": False,
                "error": "Internal server error",
            }

            _dispose_db_engine()

            _ensure_postgres_running()
            _wait_for_postgres()
            _dispose_db_engine()

            recovered_state = _clinical_state(
                clinic_id=clinic_id,
                patient_id=seeded["patient_id"],
                appointment_id=(
                    seeded["appointment_id"]
                ),
                consultation_id=(
                    seeded["consultation_id"]
                ),
                prescription_id=(
                    seeded["prescription_id"]
                ),
                batch_id=seeded["batch_id"],
            )

            assert recovered_state == baseline

            recovered_response = client.post(
                "/api/v1/pharmacy/dispense",
                json={
                    "prescription_id": (
                        seeded["prescription_id"]
                    ),
                    "items": [
                        {
                            "prescription_item_id": (
                                seeded[
                                    "prescription_item_id"
                                ]
                            ),
                            "batch_id": (
                                seeded["batch_id"]
                            ),
                            "quantity": 10,
                        }
                    ],
                    "notes": (
                        "Gate 11 Slice 10 "
                        "recovered dispense"
                    ),
                },
                headers=pharmacist_headers,
            )

            assert recovered_response.status_code == 201, (
                recovered_response.get_json()
            )

            body = recovered_response.get_json()

            assert body["success"] is True
            assert body["data"]["prescription_id"] == (
                seeded["prescription_id"]
            )
            assert body["data"]["dispensed_by_id"] == (
                seeded["pharmacist_staff_id"]
            )
            assert body["data"]["status"] == "dispensed"

            dispense_id = body["data"]["id"]

            db.session.expire_all()

            final_batch = db.session.get(
                DrugBatch,
                seeded["batch_id"],
            )

            final_dispense = db.session.get(
                DispenseRecord,
                dispense_id,
            )

            final_dispenses = list(
                db.session.scalars(
                    select(
                        DispenseRecord
                    ).where(
                        DispenseRecord.prescription_id
                        == seeded["prescription_id"]
                    )
                )
            )

            final_audits = list(
                db.session.scalars(
                    select(
                        AuditLog
                    ).where(
                        AuditLog.clinic_id == clinic_id,
                        AuditLog.entity_type
                        == "DispenseRecord",
                        AuditLog.entity_id
                        == dispense_id,
                        AuditLog.action
                        == AuditAction.CREATE,
                    )
                )
            )

            assert final_batch is not None
            assert (
                final_batch.quantity_on_hand
                == 90
            )

            assert final_dispense is not None
            assert (
                final_dispense.prescription_id
                == seeded["prescription_id"]
            )
            assert (
                final_dispense.dispensed_by_id
                == seeded[
                    "pharmacist_staff_id"
                ]
            )
            assert final_dispense.status == (
                DispenseStatus.DISPENSED
            )

            assert len(
                final_dispense.items
            ) == 1

            assert (
                final_dispense.items[0].batch_id
                == seeded["batch_id"]
            )

            assert (
                final_dispense.items[0]
                .prescription_item_id
                == seeded[
                    "prescription_item_id"
                ]
            )

            assert (
                final_dispense.items[0]
                .quantity_dispensed
                == 10
            )

            assert len(final_dispenses) == 1
            assert len(final_audits) == 1

    finally:
        if app is not None:
            try:
                with app.app_context():
                    _ensure_postgres_running()
                    _wait_for_postgres()
                    _dispose_db_engine()

                    if clinic_id is not None:
                        _cleanup_clinic(
                            clinic_id
                        )
            except Exception as cleanup_error:
                pytest.fail(
                    "GATE11_S10_CLEANUP_FAILED="
                    f"{cleanup_error!r}"
                )
