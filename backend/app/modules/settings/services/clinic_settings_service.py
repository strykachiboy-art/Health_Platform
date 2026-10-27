from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.settings.models.clinic_settings import ClinicSettings
from app.modules.settings.schemas.clinic_settings import (
    ClinicSettingsCreateSchema,
    ClinicSettingsUpdateSchema,
)
from app.core.utils.decorators import transactional
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)


def _get_clinic(clinic_id: int) -> Clinic:
    clinic = db.session.get(
        Clinic,
        clinic_id,
    )

    if clinic is None:
        raise NotFoundError("Clinic not found")

    return clinic


def _ensure_active_clinic(clinic: Clinic) -> None:
    if clinic.status.value != "active":
        raise ValidationError(
            "Clinic must be active to modify settings"
        )


def _get_settings(clinic_id: int) -> ClinicSettings:
    statement = select(
        ClinicSettings
    ).where(
        ClinicSettings.clinic_id == clinic_id
    )

    settings = db.session.execute(
        statement
    ).scalar_one_or_none()

    if settings is None:
        raise NotFoundError("Clinic settings not found")

    return settings


def _settings_data(
    payload: ClinicSettingsCreateSchema,
) -> dict[str, Any]:
    return payload.model_dump(
        exclude_unset=True,
    )


@transactional
def create_clinic_settings(
    clinic_id: int,
    payload: ClinicSettingsCreateSchema,
) -> ClinicSettings:
    clinic = _get_clinic(clinic_id)
    _ensure_active_clinic(clinic)

    statement = select(
        ClinicSettings
    ).where(
        ClinicSettings.clinic_id == clinic_id
    )

    existing = db.session.execute(
        statement
    ).scalar_one_or_none()

    if existing is not None:
        raise ConflictError(
            "Clinic settings already exist"
        )

    settings = ClinicSettings(
        clinic_id=clinic_id,
        **_settings_data(payload),
    )

    db.session.add(settings)

    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()

        raise ConflictError(
            "Clinic settings already exist"
        ) from exc

    return settings


def get_clinic_settings(
    clinic_id: int,
) -> ClinicSettings:
    _get_clinic(clinic_id)

    return _get_settings(clinic_id)


@transactional
def update_clinic_settings(
    clinic_id: int,
    payload: ClinicSettingsUpdateSchema,
    expected_version: int | None = None,
) -> ClinicSettings:
    clinic = _get_clinic(clinic_id)
    _ensure_active_clinic(clinic)

    settings = _get_settings(clinic_id)

    if expected_version is not None:
        if expected_version < 1:
            raise ValidationError(
                "Expected version must be greater than or equal to 1"
            )

        if settings.version != expected_version:
            raise ConflictError(
                "Clinic settings were modified by another request"
            )

    changes = payload.model_dump(
        exclude_unset=True,
        exclude_none=True,
    )

    if not changes:
        raise ValidationError(
            "At least one settings field must be provided"
        )

    for field, value in changes.items():
        setattr(
            settings,
            field,
            value,
        )

    settings.version += 1

    try:
        db.session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "Unable to update clinic settings"
        ) from exc

    return settings


@transactional
def enable_clinic_settings(
    clinic_id: int,
) -> ClinicSettings:
    clinic = _get_clinic(clinic_id)
    _ensure_active_clinic(clinic)

    settings = _get_settings(clinic_id)

    if settings.is_enabled:
        return settings

    settings.is_enabled = True
    settings.version += 1

    return settings


@transactional
def disable_clinic_settings(
    clinic_id: int,
) -> ClinicSettings:
    clinic = _get_clinic(clinic_id)
    _ensure_active_clinic(clinic)

    settings = _get_settings(clinic_id)

    if not settings.is_enabled:
        return settings

    settings.is_enabled = False
    settings.version += 1

    return settings


@transactional
def ensure_clinic_settings(
    clinic_id: int,
) -> ClinicSettings:
    """
    Return existing settings or create defaults for the clinic.
    """

    clinic = _get_clinic(clinic_id)

    statement = select(
        ClinicSettings
    ).where(
        ClinicSettings.clinic_id == clinic_id
    )

    settings = db.session.execute(
        statement
    ).scalar_one_or_none()

    if settings is not None:
        return settings

    _ensure_active_clinic(clinic)

    settings = ClinicSettings(
        clinic_id=clinic_id,
    )

    db.session.add(settings)

    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()

        statement = select(
            ClinicSettings
        ).where(
            ClinicSettings.clinic_id == clinic_id
        )

        settings = db.session.execute(
            statement
        ).scalar_one_or_none()

        if settings is None:
            raise ConflictError(
                "Unable to initialize clinic settings"
            ) from exc

    return settings