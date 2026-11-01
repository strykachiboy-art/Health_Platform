from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
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
from app.modules.reports.models.reports_model import GeneratedReport
from app.modules.reports.services import reports_service
from app.modules.staff.models.staff_model import Staff
from app.core.auth.user.models.user_model import User


REPORT_TYPE = "overview"
REPORT_FORMAT = "csv"


def _configure_environment(
    *,
    database_url: str,
    redis_url: str,
    encryption_key: str,
) -> None:
    os.environ.update(
        {
            "FLASK_ENV": "production",
            "DATABASE_URL": database_url,
            "REDIS_URL": redis_url,
            "CELERY_BROKER_URL": redis_url,
            "CELERY_RESULT_BACKEND": redis_url,
            "SECRET_KEY": (
                "gate11-slice7-disposable-secret"
            ),
            "JWT_SECRET_KEY": (
                "gate11-slice7-disposable-jwt-secret"
            ),
            "CORS_ALLOWED_ORIGINS": (
                "http://localhost"
            ),
            "INTEGRATION_ENCRYPTION_KEY": encryption_key,
        }
    )


def _seed_clinical_domain(
    unique: str,
) -> dict:
    clinic = Clinic(
        name=(
            "Gate 11 Slice 7 "
            f"Storage Clinic {unique[:8]}"
        ),
        status=ClinicStatus.ACTIVE,
        ai_credits=5,
    )

    db.session.add(clinic)
    db.session.flush()

    user = User(
        clinic_id=clinic.id,
        email=(
            f"gate11-s7-{unique}"
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
        last_name="StorageFailure",
        email=user.email,
        status=StaffStatus.ACTIVE,
    )

    db.session.add(staff)
    db.session.flush()

    patient = Patient(
        clinic_id=clinic.id,
        first_name="Storage",
        last_name="Failure Patient",
        blood_type=BloodType.UNKNOWN,
        patient_number=(
            f"G11-S7-{unique[:12]}"
        ),
        is_active=True,
        email=f"patient-{unique}@test.invalid",
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
            "Gate 11 Slice 7 "
            "cross-domain storage interruption"
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
        chief_complaint=(
            "Storage interruption recovery"
        ),
        symptoms=(
            "Clinical state must survive "
            "artifact persistence failure"
        ),
        started_at=datetime.now(timezone.utc),
    )

    db.session.add(consultation)
    db.session.commit()

    return {
        "clinic": clinic,
        "user": user,
        "staff": staff,
        "patient": patient,
        "appointment": appointment,
        "consultation": consultation,
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


def _storage_files(
    storage_root: Path,
) -> list[Path]:
    if not storage_root.exists():
        return []

    return [
        path
        for path in storage_root.rglob("*")
        if path.is_file()
    ]


def _authorization_headers(
    *,
    user_id: int,
) -> dict[str, str]:
    user = db.session.get(User, user_id)

    if user is None:
        raise AssertionError(
            f"AUTH_USER_NOT_FOUND={user_id}"
        )

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


def test_gate11_cross_domain_storage_interruption_rolls_back_and_recovers(
    monkeypatch,
    tmp_path,
):
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

    encryption_key = (
        os.environ.get(
            "INTEGRATION_ENCRYPTION_KEY"
        )
        or Fernet.generate_key().decode()
    )

    _configure_environment(
        database_url=database_url,
        redis_url=redis_url,
        encryption_key=encryption_key,
    )

    monkeypatch.chdir(
        tmp_path
    )

    app = create_app(
        "production"
    )

    target = None
    report_id = None
    primary_clinic_id = None
    second_clinic_id = None

    try:
        with app.app_context():
            unique = uuid4().hex

            primary = _seed_clinical_domain(
                unique
            )

            clinic = primary["clinic"]
            user = primary["user"]
            patient = primary["patient"]
            primary_clinic_id = clinic.id
            appointment = primary["appointment"]
            consultation = primary["consultation"]

            baseline_clinical_state = _clinical_state(
                clinic_id=clinic.id,
                patient_id=patient.id,
                appointment_id=appointment.id,
                consultation_id=consultation.id,
            )

            headers = _authorization_headers(
                user_id=user.id
            )

            client = app.test_client()

            storage_root = (
                tmp_path
                / "generated_reports"
            )

            real_replace = (
                reports_service.os.replace
            )

            replace_calls = {
                "count": 0,
            }

            def fail_first_replace(
                src,
                dst,
            ):
                replace_calls["count"] += 1

                if replace_calls["count"] == 1:
                    raise OSError(
                        "simulated storage interruption"
                    )

                return real_replace(
                    src,
                    dst,
                )

            monkeypatch.setattr(
                reports_service.os,
                "replace",
                fail_first_replace,
            )

            failed_response = client.post(
                "/api/v1/reports",
                json={
                    "report_type": REPORT_TYPE,
                    "report_format": REPORT_FORMAT,
                },
                headers=headers,
            )

            assert failed_response.status_code == 500

            failed_body = (
                failed_response.get_json()
            )

            assert failed_body["success"] is False
            assert failed_body["error"] == (
                "An unexpected error occurred"
            )

            assert replace_calls["count"] == 1

            failed_reports = list(
                db.session.scalars(
                    select(
                        GeneratedReport
                    ).where(
                        GeneratedReport.clinic_id
                        == clinic.id,
                    )
                )
            )

            assert failed_reports == []

            failed_audits = list(
                db.session.scalars(
                    select(
                        AuditLog
                    ).where(
                        AuditLog.clinic_id
                        == clinic.id,
                        AuditLog.entity_type
                        == "GeneratedReport",
                    )
                )
            )

            assert failed_audits == []

            assert not _storage_files(
                storage_root
            )

            temp_files = (
                list(
                    storage_root.glob(
                        "*.tmp"
                    )
                )
                if storage_root.exists()
                else []
            )

            assert temp_files == []

            assert (
                _clinical_state(
                    clinic_id=clinic.id,
                    patient_id=patient.id,
                    appointment_id=appointment.id,
                    consultation_id=consultation.id,
                )
                == baseline_clinical_state
            )

            monkeypatch.setattr(
                reports_service.os,
                "replace",
                real_replace,
            )

            recovered_response = client.post(
                "/api/v1/reports",
                json={
                    "report_type": REPORT_TYPE,
                    "report_format": REPORT_FORMAT,
                },
                headers=headers,
            )

            assert recovered_response.status_code == 201

            recovered_body = (
                recovered_response.get_json()
            )

            assert recovered_body["success"] is True
            assert recovered_body["message"] == (
                "Report generated successfully"
            )

            report_data = recovered_body["data"]

            report_id = report_data["id"]

            assert (
                report_data["clinic_id"]
                == clinic.id
            )
            assert (
                report_data["report_type"]
                == REPORT_TYPE
            )
            assert (
                report_data["report_format"]
                == REPORT_FORMAT
            )
            assert report_data["file_url"]

            report = db.session.get(
                GeneratedReport,
                report_id,
            )

            assert report is not None
            assert report.clinic_id == clinic.id
            assert report.generated_by_id == primary["staff"].id
            assert report.file_url == (
                report_data["file_url"]
            )

            target = Path(
                report.file_url
            ).resolve()

            assert target.is_file()
            assert target.parent == (
                storage_root.resolve()
            )

            report_bytes = (
                target.read_bytes()
            )

            assert report_bytes
            assert b"clinic_id" in report_bytes
            assert str(clinic.id).encode() in (
                report_bytes
            )

            reports = list(
                db.session.scalars(
                    select(
                        GeneratedReport
                    ).where(
                        GeneratedReport.clinic_id
                        == clinic.id,
                    )
                )
            )

            assert len(reports) == 1
            assert reports[0].id == report_id

            audits = list(
                db.session.scalars(
                    select(
                        AuditLog
                    ).where(
                        AuditLog.clinic_id
                        == clinic.id,
                        AuditLog.entity_type
                        == "GeneratedReport",
                        AuditLog.entity_id
                        == report_id,
                    )
                )
            )

            assert len(audits) == 1
            assert audits[0].action is (
                AuditAction.CREATE
            )
            assert audits[0].clinic_id == (
                clinic.id
            )

            files = _storage_files(
                storage_root
            )

            assert files == [
                target,
            ]

            assert (
                _clinical_state(
                    clinic_id=clinic.id,
                    patient_id=patient.id,
                    appointment_id=appointment.id,
                    consultation_id=consultation.id,
                )
                == baseline_clinical_state
            )

            second = _seed_clinical_domain(
                uuid4().hex
            )

            second_clinic = second["clinic"]
            second_user = second["user"]

            second_clinic_id = (
                second_clinic.id
            )
            second_user_id = (
                second_user.id
            )

            second_headers = (
                _authorization_headers(
                    user_id=second_user.id
                )
            )

            foreign_response = client.get(
                "/api/v1/reports?page=1&per_page=20",
                headers=second_headers,
            )

            assert foreign_response.status_code == 200

            foreign_body = (
                foreign_response.get_json()
            )

            assert foreign_body["success"] is True

            foreign_ids = {
                item["id"]
                for item in (
                    foreign_body["data"]["items"]
                )
            }

            assert report_id not in foreign_ids

            foreign_get = client.get(
                f"/api/v1/reports/{report_id}",
                headers=second_headers,
            )

            assert foreign_get.status_code == 422

            foreign_get_body = (
                foreign_get.get_json()
            )

            assert foreign_get_body["success"] is False
            assert foreign_get_body["error"] == (
                "Unauthorized clinic access"
            )

    finally:
        with app.app_context():
            if target is not None:
                target.unlink(
                    missing_ok=True
                )

            generated_reports_dir = (
                tmp_path
                / "generated_reports"
            )

            if generated_reports_dir.exists():
                shutil.rmtree(
                    generated_reports_dir,
                    ignore_errors=True,
                )

            # Production audit rows are immutable. This Phase 9 E2E runs against a dedicated disposable database; database teardown owns row cleanup.
            db.session.rollback()
            db.session.remove()
            db.engine.dispose()
