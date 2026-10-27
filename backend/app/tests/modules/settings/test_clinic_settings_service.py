from __future__ import annotations

import pytest

from app.core.enums.clinic_enums import ClinicStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.modules.settings.models.clinic_settings import ClinicSettings
from app.modules.settings.schemas.clinic_settings import (
    ClinicSettingsCreateSchema,
)
from app.modules.settings.services.clinic_settings_service import (
    create_clinic_settings,
    ensure_clinic_settings,
)


class TestCreateClinicSettings:
    def test_create_clinic_settings_success(
        self,
        db_session,
        clinic,
    ):
        payload = ClinicSettingsCreateSchema(
            language="en",
            date_format="DD/MM/YYYY",
            time_format="12h",
            notification_preferences={
                "email": True,
                "sms": False,
            },
            feature_flags={
                "ai": True,
                "chat": False,
            },
            operational_preferences={
                "appointment_buffer": 15,
            },
            security_preferences={
                "session_timeout": 30,
            },
            system_preferences={
                "maintenance_mode": False,
            },
            is_enabled=True,
        )

        settings = create_clinic_settings(
            clinic.id,
            payload,
        )

        assert settings.id is not None
        assert settings.clinic_id == clinic.id
        assert settings.language == "en"
        assert settings.date_format == "DD/MM/YYYY"
        assert settings.time_format == "12h"

        assert settings.notification_preferences == {
            "email": True,
            "sms": False,
        }

        assert settings.feature_flags == {
            "ai": True,
            "chat": False,
        }

        assert settings.operational_preferences == {
            "appointment_buffer": 15,
        }

        assert settings.security_preferences == {
            "session_timeout": 30,
        }

        assert settings.system_preferences == {
            "maintenance_mode": False,
        }

        assert settings.is_enabled is True
        assert settings.version == 1

        persisted = db_session.get(
            ClinicSettings,
            settings.id,
        )

        assert persisted is not None
        assert persisted.clinic_id == clinic.id

    def test_create_clinic_settings_uses_schema_defaults(
        self,
        db_session,
        clinic,
    ):
        payload = ClinicSettingsCreateSchema()

        settings = create_clinic_settings(
            clinic.id,
            payload,
        )

        assert settings.clinic_id == clinic.id
        assert settings.language == "en"
        assert settings.date_format == "YYYY-MM-DD"
        assert settings.time_format == "24h"

        assert settings.notification_preferences == {}
        assert settings.feature_flags == {}
        assert settings.operational_preferences == {}
        assert settings.security_preferences == {}
        assert settings.system_preferences == {}

        assert settings.is_enabled is True
        assert settings.version == 1

        persisted = db_session.get(
            ClinicSettings,
            settings.id,
        )

        assert persisted is not None

    def test_create_clinic_settings_rejects_nonexistent_clinic(
        self,
        app,
    ):
        payload = ClinicSettingsCreateSchema()

        with pytest.raises(NotFoundError, match="Clinic not found"):
            create_clinic_settings(
                999999,
                payload,
            )

    def test_create_clinic_settings_rejects_inactive_clinic(
        self,
        make_clinic,
    ):
        clinic = make_clinic(
            status=ClinicStatus.INACTIVE,
        )

        payload = ClinicSettingsCreateSchema()

        with pytest.raises(
            ValidationError,
            match="Clinic must be active to modify settings",
        ):
            create_clinic_settings(
                clinic.id,
                payload,
            )

    def test_create_clinic_settings_rejects_suspended_clinic(
        self,
        make_clinic,
    ):
        clinic = make_clinic(
            status=ClinicStatus.SUSPENDED,
        )

        payload = ClinicSettingsCreateSchema()

        with pytest.raises(
            ValidationError,
            match="Clinic must be active to modify settings",
        ):
            create_clinic_settings(
                clinic.id,
                payload,
            )

    def test_create_clinic_settings_rejects_duplicate_settings(
        self,
        clinic,
        clinic_settings,
    ):
        payload = ClinicSettingsCreateSchema(
            language="en",
        )

        with pytest.raises(
            ConflictError,
            match="Clinic settings already exist",
        ):
            create_clinic_settings(
                clinic.id,
                payload,
            )

    def test_create_clinic_settings_isolated_between_clinics(
        self,
        make_clinic,
        db_session,
    ):
        clinic_one = make_clinic(
            name="Clinic One",
        )
        clinic_two = make_clinic(
            name="Clinic Two",
        )

        first_payload = ClinicSettingsCreateSchema(
            language="en",
        )

        second_payload = ClinicSettingsCreateSchema(
            language="fr",
        )

        first_settings = create_clinic_settings(
            clinic_one.id,
            first_payload,
        )

        second_settings = create_clinic_settings(
            clinic_two.id,
            second_payload,
        )

        assert first_settings.id != second_settings.id

        assert first_settings.clinic_id == clinic_one.id
        assert second_settings.clinic_id == clinic_two.id

        assert first_settings.language == "en"
        assert second_settings.language == "fr"

        persisted_first = db_session.get(
            ClinicSettings,
            first_settings.id,
        )
        persisted_second = db_session.get(
            ClinicSettings,
            second_settings.id,
        )

        assert persisted_first.clinic_id == clinic_one.id
        assert persisted_second.clinic_id == clinic_two.id

    def test_create_clinic_settings_does_not_create_for_inactive_clinic(
        self,
        make_clinic,
        db_session,
    ):
        clinic = make_clinic(
            status=ClinicStatus.INACTIVE,
        )

        payload = ClinicSettingsCreateSchema()

        with pytest.raises(ValidationError):
            create_clinic_settings(
                clinic.id,
                payload,
            )

        settings = (
            db_session.query(ClinicSettings)
            .filter(
                ClinicSettings.clinic_id == clinic.id,
            )
            .first()
        )

        assert settings is None

    def test_create_clinic_settings_preserves_explicit_disabled_state(
        self,
        clinic,
        db_session,
    ):
        payload = ClinicSettingsCreateSchema(
            is_enabled=False,
        )

        settings = create_clinic_settings(
            clinic.id,
            payload,
        )

        assert settings.is_enabled is False
        assert settings.version == 1

        persisted = db_session.get(
            ClinicSettings,
            settings.id,
        )

        assert persisted.is_enabled is False
        assert persisted.version == 1

class TestEnsureClinicSettings:
    def test_ensure_clinic_settings_commits_created_defaults(
        self,
        clinic,
        monkeypatch,
    ):
        from app.extensions import db

        commit_count = 0
        original_commit = db.session.commit

        def tracked_commit():
            nonlocal commit_count
            commit_count += 1
            return original_commit()

        monkeypatch.setattr(
            db.session,
            "commit",
            tracked_commit,
        )

        settings = ensure_clinic_settings(
            clinic.id,
        )

        assert settings.id is not None
        assert settings.clinic_id == clinic.id
        assert settings.language == "en"
        assert settings.date_format == "YYYY-MM-DD"
        assert settings.time_format == "24h"
        assert settings.version == 1
        assert commit_count == 1
