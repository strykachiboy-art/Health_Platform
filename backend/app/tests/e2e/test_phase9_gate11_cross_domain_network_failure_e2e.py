from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from flask_jwt_extended import create_access_token
from sqlalchemy import delete, select

from app import create_app
from app import models_registry  # noqa: F401
from app.core.audit.models.audit_model import AuditLog
from app.core.auth.user.models.user_model import User
from app.core.enums.appointment_enums import (
    AppointmentStatus,
    AppointmentType,
)
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.consultation_enums import (
    ConsultationStatus,
    ConsultationType,
)
from app.core.enums.hie_enums import (
    HIEFailureClass,
    HIEIntegrationStatus,
    HIEOperation,
    HIEPurposeOfUse,
    HIESubmissionStatus,
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
from app.modules.hie.models.hie_model import (
    HIEIntegration,
    HIESubmission,
)
from app.modules.hie.providers import registry
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff


PROVIDER_NAME = "gate11-slice9-network-hie"
REMOTE_IDENTIFIER = "REMOTE-MRN-GATE11-S9"


class _NetworkHIEServerState:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = []

    def record(self, identifier: str) -> None:
        with self.lock:
            self.requests.append(identifier)


class _NetworkHIEHandler(BaseHTTPRequestHandler):
    state: _NetworkHIEServerState

    def do_GET(self):
        parsed = urllib.parse.urlparse(
            self.path
        )

        if parsed.path != "/patient":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(
            parsed.query
        )

        identifiers = params.get(
            "identifier",
            [],
        )

        if len(identifiers) != 1:
            self.send_response(400)
            self.end_headers()
            return

        identifier = identifiers[0]

        self.server.state.record(
            identifier
        )

        body = json.dumps(
            {
                "status_code": 200,
                "external_reference": (
                    "G11-S9-NETWORK-001"
                ),
                "patient": {
                    "identifier": identifier,
                    "name": (
                        "Gate 11 Network Recovery"
                    ),
                },
            }
        ).encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


class _NetworkHIEServer:
    def __init__(self):
        self.state = _NetworkHIEServerState()
        self.server = ThreadingHTTPServer(
            (
                "127.0.0.1",
                0,
            ),
            _NetworkHIEHandler,
        )
        self.server.state = self.state
        self.thread = None

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address
        return (
            f"http://{host}:{port}"
        )

    def start(self) -> None:
        if self.thread is not None:
            return

        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        if self.thread is None:
            return

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(
            timeout=5
        )
        self.thread = None


class _RealNetworkHIEProvider:
    def __init__(self, endpoint: str | None):
        if not endpoint:
            raise ValueError(
                "HIE endpoint is required"
            )

        self.endpoint = endpoint.rstrip("/")
        self.calls = []

    def query_patient(
        self,
        patient_identifier: str,
    ):
        self.calls.append(
            patient_identifier
        )

        url = (
            f"{self.endpoint}/patient?"
            + urllib.parse.urlencode(
                {
                    "identifier": (
                        patient_identifier
                    )
                }
            )
        )

        try:
            with urllib.request.urlopen(
                url,
                timeout=2,
            ) as response:
                payload = json.loads(
                    response.read().decode(
                        "utf-8"
                    )
                )

        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, BaseException):
                raise reason from exc
            raise ConnectionError(str(exc)) from exc

        return payload


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


def _seed_primary_domain(
    unique: str,
) -> dict:
    clinic = Clinic(
        name=(
            "Gate 11 Slice 9 "
            f"Network Clinic {unique[:8]}"
        ),
        status=ClinicStatus.ACTIVE,
        ai_credits=5,
    )

    db.session.add(clinic)
    db.session.flush()

    admin = User(
        clinic_id=clinic.id,
        email=(
            f"gate11-s9-admin-{unique}"
            "@test.invalid"
        ),
        role=Role.ADMIN,
        is_active=True,
        token_version=0,
    )

    doctor = User(
        clinic_id=clinic.id,
        email=(
            f"gate11-s9-doctor-{unique}"
            "@test.invalid"
        ),
        role=Role.DOCTOR,
        is_active=True,
        token_version=0,
    )

    db.session.add_all(
        [
            admin,
            doctor,
        ]
    )
    db.session.flush()

    admin_staff = Staff(
        clinic_id=clinic.id,
        user_id=admin.id,
        first_name="Gate11",
        last_name="NetworkAdmin",
        email=admin.email,
        status=StaffStatus.ACTIVE,
    )

    doctor_staff = Staff(
        clinic_id=clinic.id,
        user_id=doctor.id,
        first_name="Gate11",
        last_name="NetworkDoctor",
        email=doctor.email,
        status=StaffStatus.ACTIVE,
    )

    db.session.add_all(
        [
            admin_staff,
            doctor_staff,
        ]
    )
    db.session.flush()

    patient = Patient(
        clinic_id=clinic.id,
        first_name="Gate11",
        last_name="Network Patient",
        blood_type=BloodType.UNKNOWN,
        patient_number=(
            f"G11-S9-{unique[:12]}"
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
        staff_id=doctor_staff.id,
        scheduled_start=scheduled_start,
        scheduled_end=(
            scheduled_start
            + timedelta(minutes=30)
        ),
        status=AppointmentStatus.SCHEDULED,
        appointment_type=AppointmentType.IN_PERSON,
        reason=(
            "Gate 11 Slice 9 "
            "network interruption"
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
            "Network recovery"
        ),
        symptoms=(
            "HIE network interruption must "
            "not corrupt clinical state"
        ),
        started_at=datetime.now(
            timezone.utc
        ),
    )

    db.session.add(consultation)
    db.session.commit()

    return {
        "clinic_id": clinic.id,
        "admin_id": admin.id,
        "doctor_id": doctor.id,
        "patient_id": patient.id,
        "appointment_id": appointment.id,
        "consultation_id": consultation.id,
    }


def _seed_secondary_clinic(
    unique: str,
) -> dict:
    clinic = Clinic(
        name=(
            "Gate 11 Slice 9 Secondary "
            f"{unique[:8]}"
        ),
        status=ClinicStatus.ACTIVE,
        ai_credits=5,
    )

    db.session.add(clinic)
    db.session.flush()

    doctor = User(
        clinic_id=clinic.id,
        email=(
            f"gate11-s9-secondary-{unique}"
            "@test.invalid"
        ),
        role=Role.DOCTOR,
        is_active=True,
        token_version=0,
    )

    db.session.add(doctor)
    db.session.flush()

    staff = Staff(
        clinic_id=clinic.id,
        user_id=doctor.id,
        first_name="Gate11",
        last_name="Secondary",
        email=doctor.email,
        status=StaffStatus.ACTIVE,
    )

    db.session.add(staff)
    db.session.commit()

    return {
        "clinic_id": clinic.id,
        "doctor_id": doctor.id,
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


def _submissions_for(
    integration_id: int,
) -> list[HIESubmission]:
    return list(
        db.session.scalars(
            select(
                HIESubmission
            ).where(
                HIESubmission.integration_id
                == integration_id
            ).order_by(
                HIESubmission.id.asc()
            )
        )
    )


def _cleanup_clinic(
    clinic_id: int,
    integration_ids: list[int],
) -> None:
    for integration_id in integration_ids:
        db.session.execute(
            delete(
                HIESubmission
            ).where(
                HIESubmission.integration_id
                == integration_id
            )
        )

        db.session.execute(
            delete(
                HIEIntegration
            ).where(
                HIEIntegration.id
                == integration_id
            )
        )

    db.session.execute(
        delete(
            Consultation
        ).where(
            Consultation.clinic_id
            == clinic_id
        )
    )

    db.session.execute(
        delete(
            Appointment
        ).where(
            Appointment.clinic_id
            == clinic_id
        )
    )

    db.session.execute(
        delete(
            Patient
        ).where(
            Patient.clinic_id
            == clinic_id
        )
    )

    db.session.execute(
        delete(
            Staff
        ).where(
            Staff.clinic_id
            == clinic_id
        )
    )

    db.session.execute(
        delete(
            User
        ).where(
            User.clinic_id
            == clinic_id
        )
    )

    db.session.execute(
        delete(
            Clinic
        ).where(
            Clinic.id
            == clinic_id
        )
    )


def test_gate11_cross_domain_network_failure_recovers_with_retryable_hie_submission(
    monkeypatch,
):
    database_url = os.environ.get(
        "PHASE9_TEST_DATABASE_URL"
    )
    redis_url = os.environ.get(
        "TEST_REDIS_URL",
        "redis://localhost:56379/15",
    )

    if not database_url:
        pytest.fail(
            "PHASE9_TEST_DATABASE_URL is required"
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
        "gate11-slice9-disposable-secret",
    )
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "gate11-slice9-disposable-jwt-secret",
    )
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost",
    )
    monkeypatch.setenv(
        "HIE_ALLOWED_ENDPOINT_HOSTS",
        "fhir.partnerhealth.org",
    )
    monkeypatch.setenv(
        "INTEGRATION_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )

    app = create_app(
        "production"
    )

    provider_name = PROVIDER_NAME
    network_provider = None
    server = _NetworkHIEServer()

    primary_clinic_id = None
    secondary_clinic_id = None
    integration_id = None
    secondary_integration_id = None

    try:
        server.start()

        network_provider = _RealNetworkHIEProvider(
            server.endpoint
        )

        registry.unregister_provider(
            provider_name
        )

        registry.register_provider(
            provider_name,
            lambda endpoint: network_provider,
        )

        with app.app_context():
            primary = _seed_primary_domain(
                uuid4().hex
            )

            primary_clinic_id = (
                primary["clinic_id"]
            )

            baseline = _clinical_state(
                clinic_id=primary_clinic_id,
                patient_id=primary["patient_id"],
                appointment_id=(
                    primary["appointment_id"]
                ),
                consultation_id=(
                    primary["consultation_id"]
                ),
            )

            admin_headers = _auth_headers(
                app,
                db.session.get(
                    User,
                    primary["admin_id"],
                ),
            )

            doctor_headers = _auth_headers(
                app,
                db.session.get(
                    User,
                    primary["doctor_id"],
                ),
            )

            create_response = app.test_client().post(
                "/api/v1/hie/integrations",
                json={
                    "provider": provider_name,
                    "endpoint_url": "https://fhir.partnerhealth.org/fhir/r4",
                    "organization_id": (
                        "G11-S9-ORG"
                    ),
                    "facility_id": (
                        "G11-S9-FACILITY"
                    ),
                },
                headers=admin_headers,
            )

            assert create_response.status_code == 201, (
                create_response.get_json()
            )

            integration_id = (
                create_response
                .get_json()["data"]["id"]
            )

            activate_response = (
                app.test_client().patch(
                    f"/api/v1/hie/integrations/"
                    f"{integration_id}",
                    json={
                        "status": (
                            HIEIntegrationStatus.ACTIVE.value
                        ),
                    },
                    headers=admin_headers,
                )
            )

            assert (
                activate_response.status_code
                == 200
            ), (
                activate_response.get_json()
            )

            client = app.test_client()

            first_response = client.post(
                "/api/v1/hie/queries/patient",
                json={
                    "patient_identifier": (
                        REMOTE_IDENTIFIER
                    ),
                    "purpose_of_use": (
                        HIEPurposeOfUse.TREATMENT.value
                    ),
                    "integration_id": integration_id,
                },
                headers=doctor_headers,
            )

            assert first_response.status_code == 200, (
                first_response.get_json()
            )

            first_body = (
                first_response.get_json()
            )

            assert first_body["success"] is True
            assert (
                first_body["data"]["external_reference"]
                == "G11-S9-NETWORK-001"
            )

            submissions = _submissions_for(
                integration_id
            )

            assert len(submissions) == 1
            assert submissions[0].status is (
                HIESubmissionStatus.SUCCESS
            )

            server.stop()

            failed_response = client.post(
                "/api/v1/hie/queries/patient",
                json={
                    "patient_identifier": (
                        REMOTE_IDENTIFIER
                    ),
                    "purpose_of_use": (
                        HIEPurposeOfUse.TREATMENT.value
                    ),
                    "integration_id": integration_id,
                },
                headers=doctor_headers,
            )

            assert failed_response.status_code == 500, (
                failed_response.get_json()
            )

            submissions = _submissions_for(
                integration_id
            )

            assert len(submissions) == 2

            failed_submission = submissions[1]

            assert failed_submission.clinic_id == (
                primary_clinic_id
            )
            assert failed_submission.integration_id == (
                integration_id
            )
            assert failed_submission.operation is (
                HIEOperation.PATIENT_QUERY
            )
            assert failed_submission.status is (
                HIESubmissionStatus.FAILED
            )
            assert failed_submission.failure_class is (
                HIEFailureClass.RETRYABLE
            )
            assert failed_submission.retry_count == 1
            assert failed_submission.external_reference is None
            assert failed_submission.response_data is None
            assert failed_submission.error_message == (
                "HIE provider operation failed"
            )

            assert (
                _clinical_state(
                    clinic_id=primary_clinic_id,
                    patient_id=(
                        primary["patient_id"]
                    ),
                    appointment_id=(
                        primary["appointment_id"]
                    ),
                    consultation_id=(
                        primary["consultation_id"]
                    ),
                )
                == baseline
            )

            server = _NetworkHIEServer()
            server.start()

            network_provider.endpoint = (
                server.endpoint
            )

            recovered_response = client.post(
                "/api/v1/hie/queries/patient",
                json={
                    "patient_identifier": (
                        REMOTE_IDENTIFIER
                    ),
                    "purpose_of_use": (
                        HIEPurposeOfUse.TREATMENT.value
                    ),
                    "integration_id": integration_id,
                },
                headers=doctor_headers,
            )

            assert recovered_response.status_code == 200, (
                recovered_response.get_json()
            )

            recovered_body = (
                recovered_response.get_json()
            )

            assert recovered_body["success"] is True
            assert (
                recovered_body["data"]["external_reference"]
                == "G11-S9-NETWORK-001"
            )

            final_submissions = (
                _submissions_for(
                    integration_id
                )
            )

            assert len(final_submissions) == 3

            assert final_submissions[0].status is (
                HIESubmissionStatus.SUCCESS
            )
            assert final_submissions[1].status is (
                HIESubmissionStatus.FAILED
            )
            assert final_submissions[1].failure_class is (
                HIEFailureClass.RETRYABLE
            )
            assert final_submissions[2].status is (
                HIESubmissionStatus.SUCCESS
            )

            assert (
                _clinical_state(
                    clinic_id=primary_clinic_id,
                    patient_id=(
                        primary["patient_id"]
                    ),
                    appointment_id=(
                        primary["appointment_id"]
                    ),
                    consultation_id=(
                        primary["consultation_id"]
                    ),
                )
                == baseline
            )

            secondary = _seed_secondary_clinic(
                uuid4().hex
            )

            secondary_clinic_id = (
                secondary["clinic_id"]
            )

            secondary_doctor = db.session.get(
                User,
                secondary["doctor_id"],
            )

            secondary_headers = _auth_headers(
                app,
                secondary_doctor,
            )

            foreign_response = client.post(
                "/api/v1/hie/queries/patient",
                json={
                    "patient_identifier": (
                        REMOTE_IDENTIFIER
                    ),
                    "purpose_of_use": (
                        HIEPurposeOfUse.TREATMENT.value
                    ),
                    "integration_id": integration_id,
                },
                headers=secondary_headers,
            )

            assert (
                foreign_response.status_code
                == 422
            )

            assert (
                len(
                    _submissions_for(
                        integration_id
                    )
                )
                == 3
            )

            assert (
                _clinical_state(
                    clinic_id=primary_clinic_id,
                    patient_id=(
                        primary["patient_id"]
                    ),
                    appointment_id=(
                        primary["appointment_id"]
                    ),
                    consultation_id=(
                        primary["consultation_id"]
                    ),
                )
                == baseline
            )

    finally:
        server.stop()

        if app is not None:
            with app.app_context():
                registry.unregister_provider(
                    provider_name
                )

                clinic_ids = [
                    clinic_id
                    for clinic_id in (
                        primary_clinic_id,
                        secondary_clinic_id,
                    )
                    if clinic_id is not None
                ]

                integration_ids = [
                    integration_id_value
                    for integration_id_value in (
                        integration_id,
                        secondary_integration_id,
                    )
                    if integration_id_value is not None
                ]

                for clinic_id in clinic_ids:
                    if integration_ids:
                        clinic_specific_integration_ids = [
                            item_id
                            for item_id in integration_ids
                            if (
                                db.session.get(
                                    HIEIntegration,
                                    item_id,
                                )
                                is not None
                            )
                            and (
                                db.session.get(
                                    HIEIntegration,
                                    item_id,
                                ).clinic_id
                                == clinic_id
                            )
                        ]
                    else:
                        clinic_specific_integration_ids = []

                    _cleanup_clinic(
                        clinic_id,
                        clinic_specific_integration_ids,
                    )

                db.session.commit()
                db.session.remove()
                db.engine.dispose()
