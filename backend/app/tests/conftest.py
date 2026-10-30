from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from flask_jwt_extended import create_access_token
from sqlalchemy import event

from app import create_app
from app.extensions import db as _db
from app.extensions import socketio

from app.core.enums.notification_enums import (
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationType,
)

from app.core.notifications.models.notification_models import (
    Notification,
)


# ============================================================================
# TEST CONSTANTS
# ============================================================================


_TEST_INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")


# ============================================================================
# APP / DATABASE / CLIENT
# ============================================================================


@pytest.fixture(scope="function")
def app():
    flask_app = create_app("testing")

    flask_app.config["INTEGRATION_ENCRYPTION_KEY"] = (
        _TEST_INTEGRATION_ENCRYPTION_KEY
    )

    with flask_app.app_context():
        if _db.engine.dialect.name == "sqlite":
            @event.listens_for(
                _db.engine,
                "connect",
            )
            def _enable_sqlite_foreign_keys(
                connection,
                connection_record,
            ):
                connection.execute(
                    "PRAGMA foreign_keys=ON"
                )

        _db.create_all()

        try:
            yield flask_app
        finally:
            _db.session.rollback()
            _db.session.remove()

            if _db.engine.dialect.name == "sqlite":
                # SQLite cannot ALTER cyclic foreign-key constraints
                # away before DROP TABLE. Keep FK enforcement enabled
                # for the entire test and disable it only on the exact
                # connection performing teardown DDL.
                with _db.engine.connect() as connection:
                    connection.exec_driver_sql(
                        "PRAGMA foreign_keys=OFF"
                    )
                    try:
                        _db.metadata.drop_all(
                            bind=connection
                        )
                    finally:
                        connection.exec_driver_sql(
                            "PRAGMA foreign_keys=ON"
                        )
            else:
                _db.drop_all()


@pytest.fixture(scope="function")
def db(app):
    return _db


@pytest.fixture(scope="function")
def db_session(app, db):
    return db.session


@pytest.fixture(scope="function")
def client(app):
    return app.test_client()


# ============================================================================
# AUTHENTICATION
# ============================================================================


@pytest.fixture()
def auth_headers_for(app):
    def _make(user, role=None, clinic_context_id=None):
        claim_role = (
            role
            if role is not None
            else user.role
        )

        if hasattr(claim_role, "value"):
            claim_role = claim_role.value

        claims = {
            "role": claim_role,
            "token_version": user.token_version,
        }

        if clinic_context_id is not None:
            claims["clinic_context_id"] = clinic_context_id

        with app.test_request_context():
            token = create_access_token(
                identity=str(user.id),
                additional_claims=claims,
            )

        return {
            "Authorization": f"Bearer {token}",
        }

    return _make


@pytest.fixture()
def make_auth_headers(auth_headers_for):
    def _make(user, role=None):
        return auth_headers_for(
            user,
            role=role,
        )

    return _make


# ============================================================================
# CLINIC
# ============================================================================


@pytest.fixture()
def make_clinic(db):
    from app.core.enums.clinic_enums import ClinicStatus
    from app.modules.clinic.models.clinic_model import Clinic

    counter = {"n": 0}

    def _make(**overrides):
        counter["n"] += 1

        overrides.setdefault(
            "name",
            f"Test Clinic {counter['n']}",
        )

        overrides.setdefault(
            "ai_credits",
            5,
        )

        overrides.setdefault(
            "status",
            ClinicStatus.ACTIVE,
        )

        clinic = Clinic(**overrides)

        db.session.add(clinic)
        db.session.flush()

        return clinic

    return _make


@pytest.fixture()
def clinic(make_clinic):
    return make_clinic()


@pytest.fixture()
def suspended_clinic(make_clinic):
    from app.core.enums.clinic_enums import ClinicStatus

    return make_clinic(
        name="Suspended Clinic",
        status=ClinicStatus.SUSPENDED,
    )


# ============================================================================
# USER
# ============================================================================


@pytest.fixture()
def make_user(db):
    from app.core.auth.user.models.user_model import User
    from app.core.enums.role_enums import Role

    counter = {"n": 0}

    def _make(
        clinic=None,
        role=Role.ADMIN,
        is_active=True,
        password="supersecret",
        **overrides,
    ):
        counter["n"] += 1

        overrides.setdefault(
            "email",
            f"user{counter['n']}@test.com",
        )

        user = User(
            clinic_id=(
                clinic.id
                if clinic is not None
                else None
            ),
            role=role,
            is_active=is_active,
            **overrides,
        )

        user.set_password(password)

        db.session.add(user)
        db.session.flush()

        return user

    return _make


@pytest.fixture()
def user(make_user, clinic):
    from app.core.enums.role_enums import Role

    return make_user(
        clinic,
        role=Role.ADMIN,
        email="admin@test.com",
    )


# ============================================================================
# SETTINGS
# ============================================================================


@pytest.fixture()
def make_clinic_settings(db_session):
    from app.modules.settings.models.clinic_settings import (
        ClinicSettings,
    )

    def _make(
        clinic,
        language="en",
        date_format="YYYY-MM-DD",
        time_format="24h",
        notification_preferences=None,
        feature_flags=None,
        operational_preferences=None,
        security_preferences=None,
        system_preferences=None,
        is_enabled=True,
        version=1,
        **overrides,
    ):
        settings = ClinicSettings(
            clinic_id=clinic.id,
            language=language,
            date_format=date_format,
            time_format=time_format,
            notification_preferences=(
                {}
                if notification_preferences is None
                else notification_preferences
            ),
            feature_flags=(
                {}
                if feature_flags is None
                else feature_flags
            ),
            operational_preferences=(
                {}
                if operational_preferences is None
                else operational_preferences
            ),
            security_preferences=(
                {}
                if security_preferences is None
                else security_preferences
            ),
            system_preferences=(
                {}
                if system_preferences is None
                else system_preferences
            ),
            is_enabled=is_enabled,
            version=version,
            **overrides,
        )

        db_session.add(settings)
        db_session.flush()

        return settings

    return _make


@pytest.fixture()
def clinic_settings(
    make_clinic_settings,
    clinic,
):
    return make_clinic_settings(clinic)


@pytest.fixture()
def disabled_clinic_settings(
    make_clinic_settings,
    clinic,
):
    return make_clinic_settings(
        clinic,
        is_enabled=False,
    )


# ============================================================================
# NOTIFICATION
# ============================================================================


@pytest.fixture()
def make_notification(db_session):
    def _make_notification(
        *,
        clinic_id,
        user_id,
        title="Test Notification",
        message="This is a test notification.",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.NORMAL,
        channel=NotificationChannel.IN_APP,
        status=NotificationStatus.PENDING,
        reference_type=None,
        reference_id=None,
        is_read=False,
        read_at=None,
        sent_at=None,
        delivered_at=None,
        failed_at=None,
        error_message=None,
        retry_count=0,
        created_at=None,
        updated_at=None,
    ):
        notification = Notification(
            clinic_id=clinic_id,
            user_id=user_id,
            title=title,
            message=message,
            notification_type=notification_type,
            priority=priority,
            channel=channel,
            status=status,
            reference_type=reference_type,
            reference_id=reference_id,
            is_read=is_read,
            read_at=read_at,
            sent_at=sent_at,
            delivered_at=delivered_at,
            failed_at=failed_at,
            error_message=error_message,
            retry_count=retry_count,
            created_at=created_at,
            updated_at=updated_at,
        )

        db_session.add(notification)
        db_session.flush()

        return notification

    return _make_notification


@pytest.fixture()
def notification(
    make_notification,
    clinic,
    user,
):
    return make_notification(
        clinic_id=clinic.id,
        user_id=user.id,
    )


# ============================================================================
# STAFF
# ============================================================================


@pytest.fixture()
def make_staff(db, make_user):
    from app.core.enums.role_enums import Role
    from app.core.enums.staff_enums import StaffStatus
    from app.modules.staff.models.staff_model import Staff

    counter = {"n": 0}

    def _make(
        clinic,
        role=Role.ADMIN,
        status=StaffStatus.ACTIVE,
        first_name="Test",
        last_name="Staff",
        user_is_active=True,
        user_overrides=None,
        **overrides,
    ):
        counter["n"] += 1

        user_overrides = dict(
            user_overrides or {}
        )

        linked_user = make_user(
            clinic,
            role=role,
            is_active=user_is_active,
            **user_overrides,
        )

        staff = Staff(
            clinic_id=clinic.id,
            user_id=linked_user.id,
            first_name=first_name,
            last_name=last_name,
            status=status,
            **overrides,
        )

        db.session.add(staff)
        db.session.flush()

        return staff

    return _make


@pytest.fixture()
def staff(make_staff, clinic):
    return make_staff(clinic)


@pytest.fixture()
def make_authenticated_staff(
    make_staff,
    auth_headers_for,
):
    def _make(clinic, role, **overrides):
        staff_obj = make_staff(
            clinic,
            role=role,
            **overrides,
        )

        headers = auth_headers_for(
            staff_obj.user,
            role=role,
        )

        return staff_obj, headers

    return _make


# ============================================================================
# PATIENT
# ============================================================================


@pytest.fixture()
def make_patient(db):
    from app.modules.patient.models.patient_model import Patient

    counter = {"n": 0}

    def _make(clinic, **overrides):
        counter["n"] += 1

        overrides.setdefault(
            "first_name",
            "Jane",
        )

        overrides.setdefault(
            "last_name",
            "Doe",
        )

        overrides.setdefault(
            "patient_number",
            f"MRN-{counter['n']}",
        )

        patient = Patient(
            clinic_id=clinic.id,
            **overrides,
        )

        db.session.add(patient)
        db.session.flush()

        return patient

    return _make


@pytest.fixture()
def patient(make_patient, clinic):
    return make_patient(clinic)


# ============================================================================
# ASSET CONTROL
# ============================================================================


@pytest.fixture()
def make_asset(db):
    from app.core.enums.asset_enums import (
        AssetCategory,
        AssetCondition,
        AssetOwnership,
        AssetStatus,
        MaintenanceStatus,
    )

    from app.modules.asset_control.models.asset_model import Asset

    counter = {"n": 0}

    def _make(
        clinic,
        asset_tag=None,
        name=None,
        category=AssetCategory.MEDICAL_EQUIPMENT,
        status=AssetStatus.ACTIVE,
        condition=AssetCondition.GOOD,
        ownership=AssetOwnership.CLINIC,
        is_active=True,
        **overrides,
    ):
        counter["n"] += 1

        if asset_tag is None:
            asset_tag = f"AST-{counter['n']:04d}"

        if name is None:
            name = f"Test Asset {counter['n']}"

        overrides.setdefault(
            "maintenance_status",
            MaintenanceStatus.NOT_REQUIRED,
        )

        asset = Asset(
            clinic_id=clinic.id,
            asset_tag=asset_tag,
            name=name,
            category=category,
            status=status,
            condition=condition,
            ownership=ownership,
            is_active=is_active,
            **overrides,
        )

        db.session.add(asset)
        db.session.flush()

        return asset

    return _make


@pytest.fixture()
def asset(make_asset, clinic):
    return make_asset(clinic)


# ============================================================================
# APPOINTMENT
# ============================================================================


@pytest.fixture()
def make_appointment(db):
    from app.core.enums.appointment_enums import (
        AppointmentStatus,
        AppointmentType,
    )

    from app.modules.appointment.models.appointment_model import (
        Appointment,
    )

    counter = {"n": 0}

    def _make(
        clinic,
        patient,
        staff,
        status=AppointmentStatus.SCHEDULED,
        appointment_type=AppointmentType.IN_PERSON,
        scheduled_start=None,
        scheduled_end=None,
        **overrides,
    ):
        counter["n"] += 1

        if scheduled_start is None:
            scheduled_start = (
                datetime.now(timezone.utc)
                + timedelta(days=counter["n"])
            )

        if scheduled_end is None:
            scheduled_end = (
                scheduled_start
                + timedelta(hours=1)
            )

        appointment = Appointment(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            status=status,
            appointment_type=appointment_type,
            **overrides,
        )

        db.session.add(appointment)
        db.session.flush()

        return appointment

    return _make


# ============================================================================
# CONSULTATION
# ============================================================================


@pytest.fixture()
def make_consultation(db):
    from app.core.enums.consultation_enums import (
        ConsultationStatus,
        ConsultationType,
    )

    from app.modules.consultation.models.consultation_model import (
        Consultation,
    )

    def _make(
        clinic,
        patient,
        staff,
        appointment=None,
        consultation_type=ConsultationType.GENERAL,
        status=ConsultationStatus.IN_PROGRESS,
        template=None,
        **overrides,
    ):
        consultation = Consultation(
            clinic_id=clinic.id,
            patient_id=patient.id,
            staff_id=staff.id,
            appointment_id=(
                appointment.id
                if appointment is not None
                else None
            ),
            consultation_type=consultation_type,
            status=status,
            template_id=(
                template.id
                if template is not None
                else None
            ),
            **overrides,
        )

        db.session.add(consultation)
        db.session.flush()

        return consultation

    return _make


@pytest.fixture()
def make_template(db):
    from app.modules.consultation.models.consultation_model import (
        ConsultationTemplate,
    )

    counter = {"n": 0}

    def _make(
        clinic=None,
        name=None,
        specialty=None,
        structure=None,
        is_active=True,
        **overrides,
    ):
        counter["n"] += 1

        if name is None:
            name = (
                "Test Consultation Template "
                f"{counter['n']}"
            )

        if structure is None:
            structure = {
                "sections": [
                    "chief_complaint",
                    "symptoms",
                    "diagnosis",
                    "treatment_plan",
                ]
            }

        template = ConsultationTemplate(
            clinic_id=(
                clinic.id
                if clinic is not None
                else None
            ),
            name=name,
            specialty=specialty,
            structure=structure,
            is_active=is_active,
            **overrides,
        )

        db.session.add(template)
        db.session.flush()

        return template

    return _make


# ============================================================================
# PHARMACY
# ============================================================================


@pytest.fixture()
def make_drug(db):
    from app.modules.pharmacy.models.pharmacy_model import Drug

    counter = {"n": 0}

    def _make(clinic=None, **overrides):
        counter["n"] += 1

        overrides.setdefault(
            "name",
            f"Test Drug {counter['n']}",
        )

        overrides.setdefault(
            "is_active",
            True,
        )

        drug = Drug(
            clinic_id=(
                clinic.id
                if clinic is not None
                else None
            ),
            **overrides,
        )

        db.session.add(drug)
        db.session.flush()

        return drug

    return _make


@pytest.fixture()
def make_drug_batch(db):
    from app.modules.pharmacy.models.pharmacy_model import DrugBatch

    counter = {"n": 0}

    def _make(clinic, drug, **overrides):
        counter["n"] += 1

        overrides.setdefault(
            "batch_number",
            f"BATCH-{counter['n']}",
        )

        overrides.setdefault(
            "quantity_on_hand",
            100,
        )

        overrides.setdefault(
            "reorder_level",
            20,
        )

        overrides.setdefault(
            "expiry_date",
            date.today() + timedelta(days=90),
        )

        batch = DrugBatch(
            clinic_id=clinic.id,
            drug_id=drug.id,
            **overrides,
        )

        db.session.add(batch)
        db.session.flush()

        return batch

    return _make


# ============================================================================
# PRESCRIPTION
# ============================================================================


@pytest.fixture()
def make_prescription(db):
    from app.core.enums.prescription_enums import (
        PrescriptionStatus,
    )

    from app.modules.prescription.models.prescription_model import (
        Prescription,
    )

    def _make(
        clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        **overrides,
    ):
        prescription = Prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            status=status,
            **overrides,
        )

        db.session.add(prescription)
        db.session.flush()

        return prescription

    return _make


@pytest.fixture()
def make_prescription_item(db):
    from app.modules.prescription.models.prescription_model import (
        PrescriptionItem,
    )

    def _make(
        prescription,
        drug,
        quantity=30,
        **overrides,
    ):
        item = PrescriptionItem(
            prescription_id=prescription.id,
            drug_id=drug.id,
            quantity=quantity,
            **overrides,
        )

        db.session.add(item)
        db.session.flush()

        return item

    return _make


# ============================================================================
# WARD
# ============================================================================


@pytest.fixture()
def make_ward(db):
    from app.modules.ward.models.ward_model import Ward

    counter = {"n": 0}

    def _make(
        clinic,
        capacity=5,
        **overrides,
    ):
        counter["n"] += 1

        overrides.setdefault(
            "name",
            f"Test Ward {counter['n']}",
        )

        ward = Ward(
            clinic_id=clinic.id,
            capacity=capacity,
            **overrides,
        )

        db.session.add(ward)
        db.session.flush()

        return ward

    return _make


@pytest.fixture()
def make_bed(db):
    from app.modules.ward.models.ward_model import Bed

    counter = {"n": 0}

    def _make(
        ward,
        **overrides,
    ):
        counter["n"] += 1

        overrides.setdefault(
            "bed_number",
            f"B{counter['n']}",
        )

        bed = Bed(
            ward_id=ward.id,
            **overrides,
        )

        db.session.add(bed)
        db.session.flush()

        return bed

    return _make


# ============================================================================
# LAB
# ============================================================================


@pytest.fixture()
def make_lab_test(db):
    from app.modules.lab.models.lab_model import LabTest

    counter = {"n": 0}

    def _make(clinic=None, **overrides):
        counter["n"] += 1

        overrides.setdefault(
            "name",
            f"Test Lab Test {counter['n']}",
        )

        test = LabTest(
            clinic_id=(
                clinic.id
                if clinic is not None
                else None
            ),
            **overrides,
        )

        db.session.add(test)
        db.session.flush()

        return test

    return _make


@pytest.fixture()
def make_lab_order(db):
    from app.core.enums.lab_enums import LabOrderStatus

    from app.modules.lab.models.lab_model import (
        LabOrder,
        LabOrderItem,
    )

    counter = {"n": 0}

    def _make(
        clinic,
        patient,
        staff,
        tests,
        status=LabOrderStatus.ORDERED,
        **overrides,
    ):
        counter["n"] += 1

        overrides.setdefault(
            "qr_code",
            f"LAB-TEST-{counter['n']}",
        )

        order = LabOrder(
            clinic_id=clinic.id,
            patient_id=patient.id,
            ordered_by_id=staff.id,
            status=status,
            **overrides,
        )

        db.session.add(order)
        db.session.flush()

        for test in tests:
            db.session.add(
                LabOrderItem(
                    order_id=order.id,
                    test_id=test.id,
                )
            )

        db.session.flush()

        return order

    return _make


# ============================================================================
# AUDIT
# ============================================================================


@pytest.fixture()
def make_audit_log(db):
    from app.core.audit.models.audit_model import AuditLog
    from app.core.enums.audit_enums import AuditAction

    counter = {"n": 0}

    def _make(
        user=None,
        user_id=None,
        action=AuditAction.CREATE,
        entity_type="Patient",
        entity_id=None,
        description="Test audit log",
        old_value=None,
        new_value=None,
        ip_address=None,
        **overrides,
    ):
        counter["n"] += 1

        if entity_id is None:
            entity_id = counter["n"]

        if user is not None:
            user_id = user.id

        log = AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            description=description,
            old_value=old_value,
            new_value=new_value,
            ip_address=ip_address,
            **overrides,
        )

        db.session.add(log)
        db.session.flush()

        return log

    return _make


# ============================================================================
# AI
# ============================================================================


@pytest.fixture()
def mock_ai_provider(monkeypatch):
    import app.modules.ai.services.ai_service as ai_service

    state = {
        "response": {},
        "last_call": None,
    }

    def _fake_call_openai(
        feature,
        payload,
    ):
        state["last_call"] = {
            "feature": feature,
            "payload": payload,
        }

        return state["response"]

    monkeypatch.setattr(
        ai_service,
        "_call_openai",
        _fake_call_openai,
    )

    class Controller:
        def set_response(self, value):
            state["response"] = value

        @property
        def last_call(self):
            return state["last_call"]

    return Controller()


# ============================================================================
# RESPONSE ASSERTION HELPERS
# ============================================================================


@pytest.fixture()
def assert_domain_error():
    def _assert(
        response,
        status_code,
    ):
        body = response.get_json()

        assert response.status_code == status_code, body
        assert body["success"] is False
        assert "error" in body

        return body

    return _assert


@pytest.fixture()
def assert_forbidden():
    def _assert(response):
        body = response.get_json()

        assert response.status_code == 403, body
        assert body["error"] == "Insufficient permissions"

        return body

    return _assert


@pytest.fixture()
def assert_unauthorized():
    def _assert(response):
        body = response.get_json()

        assert response.status_code in (
            401,
            422,
        ), body

        assert "msg" in body

        return body

    return _assert


# ============================================================================
# DATABASE HELPERS
# ============================================================================


@pytest.fixture()
def get_by_id(db_session):
    def _get(
        model,
        object_id,
    ):
        return db_session.get(
            model,
            object_id,
        )

    return _get


@pytest.fixture()
def commit_db(db_session):
    def _commit():
        db_session.commit()

    return _commit


@pytest.fixture()
def rollback_db(db_session):
    def _rollback():
        db_session.rollback()

    return _rollback


# ============================================================================
# CHAT FIXTURES
# ============================================================================


@pytest.fixture()
def make_chat_usage(db_session):
    from app.modules.chat.models.chat_usage_model import ChatUsage

    def _make(
        clinic,
        user,
        usage_date=None,
        direct_created=0,
        group_created=0,
        department_created=0,
        team_created=0,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "user_id": user.id,
            "usage_date": (
                date.today()
                if usage_date is None
                else usage_date
            ),
            "direct_created": direct_created,
            "group_created": group_created,
            "department_created": department_created,
            "team_created": team_created,
            **overrides,
        }

        usage = ChatUsage(**values)
        db_session.add(usage)
        db_session.flush()

        return usage

    return _make


@pytest.fixture()
def make_conversation(db_session):
    from app.modules.chat.models.conversation_model import Conversation
    from app.core.enums.chat_enums import (
        ConversationStatus,
        ConversationType,
    )

    def _make(
        clinic,
        created_by,
        conversation_type=ConversationType.DIRECT,
        status=ConversationStatus.ACTIVE,
        direct_key=None,
        title=None,
        description=None,
        avatar_storage_key=None,
        patient=None,
        appointment=None,
        consultation=None,
        last_message_at=None,
        archived_at=None,
        closed_at=None,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "conversation_type": conversation_type,
            "status": status,
            "direct_key": direct_key,
            "title": title,
            "description": description,
            "avatar_storage_key": avatar_storage_key,
            "created_by_id": created_by.id,
            "patient_id": (
                patient.id
                if patient is not None
                else None
            ),
            "appointment_id": (
                appointment.id
                if appointment is not None
                else None
            ),
            "consultation_id": (
                consultation.id
                if consultation is not None
                else None
            ),
            "last_message_at": last_message_at,
            "archived_at": archived_at,
            "closed_at": closed_at,
            **overrides,
        }

        conversation = Conversation(**values)
        db_session.add(conversation)
        db_session.flush()

        return conversation

    return _make


@pytest.fixture()
def conversation(
    db_session,
    clinic,
    user,
    make_conversation,
):
    return make_conversation(
        clinic=clinic,
        created_by=user,
    )


@pytest.fixture()
def make_conversation_participant(db_session):
    from app.modules.chat.models.conversation_participant_model import (
        ConversationParticipant,
    )
    from app.core.enums.chat_enums import (
        ParticipantRole,
        ParticipantStatus,
    )

    def _make(
        clinic,
        conversation,
        user,
        role=ParticipantRole.MEMBER,
        status=ParticipantStatus.PENDING,
        joined_at=None,
        left_at=None,
        removed_at=None,
        last_read_message=None,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "conversation_id": conversation.id,
            "user_id": user.id,
            "role": role,
            "status": status,
            "joined_at": joined_at,
            "left_at": left_at,
            "removed_at": removed_at,
            "last_read_message_id": (
                last_read_message.id
                if last_read_message is not None
                else None
            ),
            **overrides,
        }

        participant = ConversationParticipant(**values)
        db_session.add(participant)
        db_session.flush()

        return participant

    return _make


@pytest.fixture()
def conversation_participant(
    db_session,
    clinic,
    conversation,
    user,
    make_conversation_participant,
):
    return make_conversation_participant(
        clinic=clinic,
        conversation=conversation,
        user=user,
    )


@pytest.fixture()
def make_message(db_session):
    from app.modules.chat.models.message_model import Message
    from app.core.enums.chat_enums import (
        MessagePriority,
        MessageStatus,
        MessageType,
    )

    def _make(
        clinic,
        conversation,
        sender,
        message_type=MessageType.TEXT,
        content="Test clinical chat message",
        reply_to_message=None,
        status=MessageStatus.PENDING,
        priority=MessagePriority.NORMAL,
        created_at=None,
        updated_at=None,
        edited_at=None,
        deleted_at=None,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "conversation_id": conversation.id,
            "sender_id": sender.id,
            "message_type": message_type,
            "content": content,
            "reply_to_message_id": (
                reply_to_message.id
                if reply_to_message is not None
                else None
            ),
            "status": status,
            "priority": priority,
            "edited_at": edited_at,
            "deleted_at": deleted_at,
            **overrides,
        }

        if created_at is not None:
            values["created_at"] = created_at

        if updated_at is not None:
            values["updated_at"] = updated_at

        message = Message(**values)
        db_session.add(message)
        db_session.flush()

        return message

    return _make


@pytest.fixture()
def message(
    db_session,
    clinic,
    conversation,
    user,
    make_message,
):
    return make_message(
        clinic=clinic,
        conversation=conversation,
        sender=user,
    )


@pytest.fixture()
def make_message_attachment(db_session):
    from app.modules.chat.models.message_attachment_model import (
        MessageAttachment,
    )
    from app.core.enums.chat_enums import AttachmentType

    def _make(
        clinic,
        message,
        attachment_type=None,
        file_name="test-file.txt",
        mime_type="text/plain",
        file_size_bytes=128,
        storage_key=None,
        checksum=None,
        created_at=None,
        updated_at=None,
        **overrides,
    ):
        if attachment_type is None:
            attachment_type = next(iter(AttachmentType))

        if storage_key is None:
            storage_key = (
                f"test/chat/{message.id}/attachment.txt"
            )

        values = {
            "clinic_id": clinic.id,
            "message_id": message.id,
            "attachment_type": attachment_type,
            "file_name": file_name,
            "mime_type": mime_type,
            "file_size_bytes": file_size_bytes,
            "storage_key": storage_key,
            "checksum": checksum,
            **overrides,
        }

        if created_at is not None:
            values["created_at"] = created_at

        if updated_at is not None:
            values["updated_at"] = updated_at

        attachment = MessageAttachment(**values)
        db_session.add(attachment)
        db_session.flush()

        return attachment

    return _make


@pytest.fixture()
def message_attachment(
    db_session,
    clinic,
    message,
    make_message_attachment,
):
    return make_message_attachment(
        clinic=clinic,
        message=message,
    )


@pytest.fixture()
def make_message_mention(db_session):
    from app.modules.chat.models.message_mention_model import MessageMention
    from app.core.enums.chat_enums import MentionType

    def _make(
        clinic,
        message,
        mention_type,
        mentioned_user=None,
        mentioned_patient=None,
        mentioned_conversation=None,
        position_start=None,
        position_end=None,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "message_id": message.id,
            "mention_type": mention_type,
            "mentioned_user_id": (
                mentioned_user.id
                if mentioned_user is not None
                else None
            ),
            "mentioned_patient_id": (
                mentioned_patient.id
                if mentioned_patient is not None
                else None
            ),
            "mentioned_conversation_id": (
                mentioned_conversation.id
                if mentioned_conversation is not None
                else None
            ),
            "position_start": position_start,
            "position_end": position_end,
            **overrides,
        }

        mention = MessageMention(**values)
        db_session.add(mention)
        db_session.flush()

        return mention

    return _make


@pytest.fixture()
def message_mention_user(
    db_session,
    clinic,
    message,
    user,
    make_message_mention,
):
    from app.core.enums.chat_enums import MentionType

    return make_message_mention(
        clinic=clinic,
        message=message,
        mention_type=MentionType.USER,
        mentioned_user=user,
    )


@pytest.fixture()
def make_message_pin(db_session):
    from app.modules.chat.models.message_pin_model import MessagePin
    from app.core.enums.chat_enums import PinStatus

    def _make(
        clinic,
        message,
        pinned_by,
        status=PinStatus.PINNED,
        pinned_at=None,
        expires_at=None,
        unpinned_at=None,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "message_id": message.id,
            "pinned_by_id": pinned_by.id,
            "status": status,
            "expires_at": expires_at,
            "unpinned_at": unpinned_at,
            **overrides,
        }

        if pinned_at is not None:
            values["pinned_at"] = pinned_at

        pin = MessagePin(**values)
        db_session.add(pin)
        db_session.flush()

        return pin

    return _make


@pytest.fixture()
def message_pin(
    db_session,
    clinic,
    message,
    user,
    make_message_pin,
):
    return make_message_pin(
        clinic=clinic,
        message=message,
        pinned_by=user,
    )


@pytest.fixture()
def make_message_reaction(db_session):
    from app.modules.chat.models.message_reaction_model import MessageReaction

    def _make(
        clinic,
        message,
        user,
        reaction="👍",
        created_at=None,
        updated_at=None,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "message_id": message.id,
            "user_id": user.id,
            "reaction": reaction,
            **overrides,
        }

        if created_at is not None:
            values["created_at"] = created_at

        if updated_at is not None:
            values["updated_at"] = updated_at

        reaction_obj = MessageReaction(**values)
        db_session.add(reaction_obj)
        db_session.flush()

        return reaction_obj

    return _make


@pytest.fixture()
def message_reaction(
    db_session,
    clinic,
    message,
    user,
    make_message_reaction,
):
    return make_message_reaction(
        clinic=clinic,
        message=message,
        user=user,
    )


@pytest.fixture()
def make_message_read_receipt(db_session):
    from app.modules.chat.models.message_read_receipt_model import (
        MessageReadReceipt,
    )
    from app.core.enums.chat_enums import ReadReceiptStatus

    def _make(
        clinic,
        message,
        user,
        status=ReadReceiptStatus.DELIVERED,
        delivered_at=None,
        read_at=None,
        created_at=None,
        updated_at=None,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "message_id": message.id,
            "user_id": user.id,
            "status": status,
            "delivered_at": delivered_at,
            "read_at": read_at,
            **overrides,
        }

        if created_at is not None:
            values["created_at"] = created_at

        if updated_at is not None:
            values["updated_at"] = updated_at

        receipt = MessageReadReceipt(**values)
        db_session.add(receipt)
        db_session.flush()

        return receipt

    return _make


@pytest.fixture()
def message_read_receipt(
    db_session,
    clinic,
    message,
    user,
    make_message_read_receipt,
):
    return make_message_read_receipt(
        clinic=clinic,
        message=message,
        user=user,
    )


@pytest.fixture()
def make_message_revision(db_session):
    from app.modules.chat.models.message_revision_model import MessageRevision

    def _make(
        clinic,
        message,
        edited_by,
        revision_number=1,
        previous_content="Original message content",
        previous_message_type="text",
        created_at=None,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "message_id": message.id,
            "edited_by_id": edited_by.id,
            "revision_number": revision_number,
            "previous_content": previous_content,
            "previous_message_type": previous_message_type,
            **overrides,
        }

        if created_at is not None:
            values["created_at"] = created_at

        revision = MessageRevision(**values)
        db_session.add(revision)
        db_session.flush()

        return revision

    return _make


@pytest.fixture()
def message_revision(
    db_session,
    clinic,
    message,
    user,
    make_message_revision,
):
    return make_message_revision(
        clinic=clinic,
        message=message,
        edited_by=user,
    )


@pytest.fixture()
def make_chat_outbox(db_session):
    from app.modules.chat.models.chat_outbox_model import ChatOutbox

    def _make(
        clinic,
        message=None,
        event_type="message.created",
        payload=None,
        status="pending",
        attempts=0,
        available_at=None,
        processed_at=None,
        last_error=None,
        **overrides,
    ):
        values = {
            "clinic_id": clinic.id,
            "message_id": (
                message.id
                if message is not None
                else None
            ),
            "event_type": event_type,
            "payload": (
                {}
                if payload is None
                else payload
            ),
            "status": status,
            "attempts": attempts,
            "processed_at": processed_at,
            "last_error": last_error,
            **overrides,
        }

        if available_at is not None:
            values["available_at"] = available_at

        outbox = ChatOutbox(**values)
        db_session.add(outbox)
        db_session.flush()

        return outbox

    return _make


@pytest.fixture()
def chat_outbox(
    db_session,
    clinic,
    message,
    make_chat_outbox,
):
    return make_chat_outbox(
        clinic=clinic,
        message=message,
    )


@pytest.fixture()
def no_audit(monkeypatch):
    monkeypatch.setattr(
        "app.modules.chat.services.conversation_service.create_audit_log",
        lambda *args, **kwargs: None,
    )


# ============================================================================
# SOCKETIO
# ============================================================================


@pytest.fixture()
def chat_socket_client_for(app):
    def _make(
        user=None,
        *,
        token=None,
        auth=None,
        namespace="/chat",
    ):
        if auth is None:
            if token is not None:
                auth = {
                    "access_token": token,
                }

            elif user is not None:
                claim_role = user.role

                if hasattr(claim_role, "value"):
                    claim_role = claim_role.value

                with app.test_request_context():
                    token = create_access_token(
                        identity=str(user.id),
                        additional_claims={
                            "role": claim_role,
                            "token_version": user.token_version,
                        },
                    )

                auth = {
                    "access_token": token,
                }

        return socketio.test_client(
            app,
            namespace=namespace,
            auth=auth,
        )

    return _make



# ============================================================================
# DASHBOARD SUPPORT FIXTURES
# ============================================================================


@pytest.fixture()
def make_inventory_item(db):
    from app.core.enums.inventory_enums import InventoryCategory
    from app.modules.inventory.models.inventory_model import InventoryItem

    counter = {"n": 0}

    def _make(
        clinic,
        name=None,
        category=InventoryCategory.MEDICAL_SUPPLY,
        sku=None,
        barcode=None,
        unit="pcs",
        quantity_on_hand=100,
        reorder_level=10,
        is_active=True,
        **overrides,
    ):
        counter["n"] += 1

        if name is None:
            name = f"Test Inventory Item {counter['n']}"

        if sku is None:
            sku = f"SKU-{counter['n']:06d}"

        if barcode is None:
            barcode = f"BAR-{counter['n']:06d}"

        item = InventoryItem(
            clinic_id=clinic.id,
            name=name,
            category=category,
            sku=sku,
            barcode=barcode,
            unit=unit,
            quantity_on_hand=quantity_on_hand,
            reorder_level=reorder_level,
            is_active=is_active,
            **overrides,
        )

        db.session.add(item)
        db.session.flush()

        return item

    return _make


@pytest.fixture()
def make_invoice(db):
    from decimal import Decimal

    from app.core.enums.billing_enums import InvoiceStatus
    from app.modules.billing.models.billing_model import Invoice

    counter = {"n": 0}

    def _make(
        clinic,
        patient,
        appointment=None,
        invoice_number=None,
        total_amount=Decimal("100.00"),
        amount_paid=Decimal("0.00"),
        status=InvoiceStatus.ISSUED,
        due_date=None,
        is_insurance_claim=False,
        insurance_provider=None,
        created_at=None,
        updated_at=None,
        **overrides,
    ):
        counter["n"] += 1

        if invoice_number is None:
            invoice_number = f"INV-{counter['n']:06d}"

        invoice = Invoice(
            clinic_id=clinic.id,
            patient_id=patient.id,
            appointment_id=(
                appointment.id
                if appointment is not None
                else None
            ),
            invoice_number=invoice_number,
            total_amount=total_amount,
            amount_paid=amount_paid,
            status=status,
            due_date=due_date,
            is_insurance_claim=is_insurance_claim,
            insurance_provider=insurance_provider,
            **overrides,
        )

        if created_at is not None:
            invoice.created_at = created_at

        if updated_at is not None:
            invoice.updated_at = updated_at

        db.session.add(invoice)
        db.session.flush()

        return invoice

    return _make


@pytest.fixture()
def make_payment(db):
    from decimal import Decimal

    from app.core.enums.billing_enums import (
        PaymentMethod,
        PaymentStatus,
    )
    from app.modules.billing.models.billing_model import Payment

    counter = {"n": 0}

    def _make(
        invoice,
        amount=Decimal("25.00"),
        method=PaymentMethod.CASH,
        status=PaymentStatus.SUCCESSFUL,
        gateway=None,
        reference=None,
        gateway_transaction_id=None,
        failure_reason=None,
        created_at=None,
        updated_at=None,
        paid_at=None,
        **overrides,
    ):
        counter["n"] += 1

        if reference is None:
            reference = f"PAY-{counter['n']:06d}"

        payment = Payment(
            invoice_id=invoice.id,
            amount=amount,
            method=method,
            status=status,
            gateway=gateway,
            reference=reference,
            gateway_transaction_id=gateway_transaction_id,
            failure_reason=failure_reason,
            paid_at=paid_at,
            **overrides,
        )

        if created_at is not None:
            payment.created_at = created_at

        if updated_at is not None:
            payment.updated_at = updated_at

        db.session.add(payment)
        db.session.flush()

        return payment

    return _make


@pytest.fixture()
def make_ambulance_vehicle(db):
    from app.core.enums.ambulance_enums import (
        EquipmentLevel,
        VehicleStatus,
    )
    from app.modules.ambulance.models.ambulance_model import AmbulanceVehicle

    counter = {"n": 0}

    def _make(
        clinic,
        plate_number=None,
        equipment_level=EquipmentLevel.BLS,
        capacity=1,
        status=VehicleStatus.AVAILABLE,
        last_service_date=None,
        **overrides,
    ):
        counter["n"] += 1

        if plate_number is None:
            plate_number = f"AMB-{counter['n']:04d}"

        vehicle = AmbulanceVehicle(
            clinic_id=clinic.id,
            plate_number=plate_number,
            equipment_level=equipment_level,
            capacity=capacity,
            status=status,
            last_service_date=last_service_date,
            **overrides,
        )

        db.session.add(vehicle)
        db.session.flush()

        return vehicle

    return _make


@pytest.fixture()
def make_ambulance_trip(db):
    from app.core.enums.ambulance_enums import (
        TripStatus,
        TripType,
    )
    from app.modules.ambulance.models.ambulance_model import AmbulanceTrip

    def _make(
        clinic,
        trip_type=TripType.NON_EMERGENCY,
        status=TripStatus.REQUESTED,
        vehicle=None,
        patient=None,
        driver=None,
        paramedic=None,
        admission=None,
        invoice=None,
        pickup_address=None,
        pickup_lat=None,
        pickup_lng=None,
        destination_address=None,
        destination_lat=None,
        destination_lng=None,
        created_at=None,
        updated_at=None,
        requested_at=None,
        dispatched_at=None,
        pickup_at=None,
        completed_at=None,
        cancelled_at=None,
        cancellation_reason=None,
        notes=None,
        **overrides,
    ):
        trip = AmbulanceTrip(
            clinic_id=clinic.id,
            vehicle_id=(
                vehicle.id
                if vehicle is not None
                else None
            ),
            patient_id=(
                patient.id
                if patient is not None
                else None
            ),
            driver_id=(
                driver.id
                if driver is not None
                else None
            ),
            paramedic_id=(
                paramedic.id
                if paramedic is not None
                else None
            ),
            admission_id=(
                admission.id
                if admission is not None
                else None
            ),
            invoice_id=(
                invoice.id
                if invoice is not None
                else None
            ),
            trip_type=trip_type,
            status=status,
            pickup_address=pickup_address,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            destination_address=destination_address,
            destination_lat=destination_lat,
            destination_lng=destination_lng,
            cancellation_reason=cancellation_reason,
            notes=notes,
            **overrides,
        )

        if created_at is not None:
            trip.created_at = created_at

        if updated_at is not None:
            trip.updated_at = updated_at

        if requested_at is not None:
            trip.requested_at = requested_at

        if dispatched_at is not None:
            trip.dispatched_at = dispatched_at

        if pickup_at is not None:
            trip.pickup_at = pickup_at

        if completed_at is not None:
            trip.completed_at = completed_at

        if cancelled_at is not None:
            trip.cancelled_at = cancelled_at

        db.session.add(trip)
        db.session.flush()

        return trip

    return _make


@pytest.fixture()
def make_ai_log(db):
    from decimal import Decimal

    from app.core.enums.ai_enums import (
        AIFeature,
        AIApprovalStatus,
        AIRiskLevel,
    )
    from app.modules.ai.models.ai_model import AILog

    counter = {"n": 0}

    def _make(
        clinic,
        patient=None,
        user=None,
        feature_used=AIFeature.DRUG_INTERACTION_CHECK,
        risk_level=AIRiskLevel.LOW,
        model="test-model",
        model_version="test-v1",
        input_context_version="v1",
        generated_by_system=True,
        input_data=None,
        output_data=None,
        approval_status=AIApprovalStatus.PENDING,
        credits_used=1,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        estimated_cost=Decimal("0.010000"),
        cost_currency="USD",
        created_at=None,
        updated_at=None,
        **overrides,
    ):
        counter["n"] += 1

        log = AILog(
            clinic_id=clinic.id,
            patient_id=(
                patient.id
                if patient is not None
                else None
            ),
            user_id=(
                user.id
                if user is not None
                else None
            ),
            feature_used=feature_used,
            risk_level=risk_level,
            model=model,
            model_version=model_version,
            input_context_version=input_context_version,
            generated_by_system=generated_by_system,
            input_data=(
                {}
                if input_data is None
                else input_data
            ),
            output_data=(
                {}
                if output_data is None
                else output_data
            ),
            approval_status=approval_status,
            credits_used=credits_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost=estimated_cost,
            cost_currency=cost_currency,
            **overrides,
        )

        if created_at is not None:
            log.created_at = created_at

        if updated_at is not None:
            log.updated_at = updated_at

        db.session.add(log)
        db.session.flush()

        return log

    return _make
