from __future__ import annotations

import os
import re

from flask import Flask, request
from sqlalchemy.engine import make_url

from app.config import config_by_name
from app.extensions import (
    celery,
    db,
    init_extensions,
)
from app.core.api import register_api_blueprint
from app.core.api.versioning import validate_api_version
from app.core.error_handlers import register_error_handlers
from app.core.observability.health_routes import (
    health_bp,
)
from app.core.observability import (
    init_celery_metrics,
    init_db_metrics,
    init_request_metrics,
)
from app.core.observability.request_context import (
    init_request_context,
)
from app.core.observability.tracing import (
    init_tracing,
)
from app.core.security.http_security import (
    register_security_headers,
)
from app.core.security.trusted_proxy import (
    configure_trusted_proxy,
)


_API_VERSION_PATH_PATTERN = re.compile(
    r"^/api/(?P<version>v\d+)(?:/|$)"
)


def create_app(config_name=None):
    config_name = config_name or os.environ.get(
        "FLASK_ENV",
        "development",
    )

    if config_name not in config_by_name:
        raise ValueError(
            f"Unknown configuration: {config_name}"
        )

    app = Flask(__name__)

    app.config.from_object(
        config_by_name[config_name]
    )

    if config_name == "production":
        secret_key = os.environ.get(
            "SECRET_KEY"
        )

        jwt_secret_key = os.environ.get(
            "JWT_SECRET_KEY"
        )

        database_url = os.environ.get(
            "DATABASE_URL"
        )

        redis_url = os.environ.get(
            "REDIS_URL"
        )

        cors_origins = os.environ.get(
            "CORS_ALLOWED_ORIGINS"
        )

        backup_encryption_key = os.environ.get(
            "BACKUP_ENCRYPTION_KEY"
        )
        backup_encryption_key_version = os.environ.get(
            "BACKUP_ENCRYPTION_KEY_VERSION",
            "1",
        )

        if (
            not secret_key
            or not secret_key.strip()
        ):
            raise RuntimeError(
                "Production SECRET_KEY is not configured"
            )

        if (
            not jwt_secret_key
            or not jwt_secret_key.strip()
        ):
            raise RuntimeError(
                "Production JWT_SECRET_KEY is not configured"
            )

        if (
            not database_url
            or not database_url.strip()
        ):
            raise RuntimeError(
                "Production DATABASE_URL is not configured"
            )

        try:
            database_scheme = make_url(
                database_url
            ).get_backend_name()
        except Exception as exc:
            raise RuntimeError(
                "Production DATABASE_URL is invalid"
            ) from exc

        if database_scheme != "postgresql":
            raise RuntimeError(
                "Production DATABASE_URL must use PostgreSQL"
            )

        if (
            not redis_url
            or not redis_url.strip()
        ):
            raise RuntimeError(
                "Production REDIS_URL is not configured"
            )

        if (
            not cors_origins
            or not cors_origins.strip()
        ):
            raise RuntimeError(
                "Production CORS_ALLOWED_ORIGINS is not configured"
            )

        if (
            not backup_encryption_key
            or not backup_encryption_key.strip()
        ):
            raise RuntimeError(
                "Production BACKUP_ENCRYPTION_KEY is not configured"
            )

        app.config["SECRET_KEY"] = secret_key
        app.config["JWT_SECRET_KEY"] = jwt_secret_key
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
        app.config["REDIS_URL"] = redis_url
        app.config["CELERY_BROKER_URL"] = redis_url
        app.config["CELERY_RESULT_BACKEND"] = redis_url
        app.config["RATELIMIT_STORAGE_URI"] = redis_url
        app.config["CORS_ALLOWED_ORIGINS"] = cors_origins
        app.config["BACKUP_ENCRYPTION_KEY"] = backup_encryption_key
        app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = (
            backup_encryption_key_version
        )

        with app.app_context():
            from app.core.backup.backup_encryption import (
                validate_backup_encryption_configuration,
            )

            validate_backup_encryption_configuration()

    init_extensions(app)

    register_security_headers(app)

    init_celery_metrics(celery)
    init_db_metrics(app)
    init_request_context(app)
    init_tracing(app)
    init_request_metrics(app)

    with app.app_context():
        from app import models_registry  # noqa: F401
        from app.modules.chat.realtime import chat_socket  # noqa: F401

    register_error_handlers(app)
    register_api_version_boundary(app)
    register_blueprints(app)

    configure_trusted_proxy(app)

    return app


def register_api_version_boundary(
    app: Flask,
) -> None:

    @app.before_request
    def validate_api_version_boundary():
        match = _API_VERSION_PATH_PATTERN.match(
            request.path
        )

        if not match:
            return None

        validate_api_version(
            match.group("version")
        )

        return None


def register_blueprints(app):
    from app.core.web_routes import web_bp

    from app.core.audit.routes.audit_route import (
        audit_bp,
    )

    from app.core.auth.user.routes.auth_routes import (
        auth_bp,
    )

    from app.core.notifications.routes.notification_route import (
        notification_bp,
    )

    from app.modules.ai.routes.ai_route import (
        ai_bp,
    )

    from app.modules.ambulance.routes.ambulance_vehicle_routes import (
        vehicle_bp,
    )

    from app.modules.ambulance.routes.ambulance_trip_routes import (
        trip_bp,
    )

    from app.modules.appointment.routes.appointment_route import (
        appointment_bp,
    )

    from app.modules.billing.routes.billing_route import (
        billing_bp,
    )

    from app.modules.clinic.routes.clinic_route import (
        clinic_bp,
    )

    from app.modules.consultation.routes.consultation_route import (
        consultation_bp,
    )

    from app.modules.department.routes.department_route import (
        department_bp,
    )

    from app.modules.hie.routes.hie_route import (
        hie_bp,
    )

    from app.modules.inventory.routes.inventory_route import (
        inventory_bp,
    )

    from app.modules.lab.routes.lab_route import (
        lab_bp,
    )

    from app.modules.patient.routes.patient_route import (
        patient_bp,
    )

    from app.modules.pharmacy.routes.pharmacy_routes import (
        pharmacy_bp,
    )

    from app.modules.prescription.routes.prescription_routes import (
        prescription_bp,
    )

    from app.modules.reports.routes.reports_route import (
        reports_bp,
    )

    from app.modules.settings.routes.settings_routes import (
        settings_bp,
    )

    from app.modules.staff.routes.staff_route import (
        staff_bp,
    )

    from app.modules.staff.routes.staff_department_routes import (
        staff_department_bp,
    )

    from app.modules.ward.routes.ward_route import (
        ward_bp,
    )

    from app.core.auth.user.routes.user_device_routes import (
        user_device_bp,
    )

    from app.modules.asset_control.routes.asset_route import (
        asset_bp,
    )

    from app.modules.profile.routes.profile_route import (
        profile_bp,
    )

    from app.modules.access_control.routes.access_control_routes import (
        access_control_bp,
    )

    from app.modules.chat.routes.chat_routes import (
        chat_bp,
    )

    from app.modules.dashboard.routes.dashboard_route import (
        dashboard_bp,
    )

    from app.core.observability.operations_routes import (
        operations_bp,
    )

    from app.core.observability.failure_dashboard_routes import (
        failure_dashboard_bp,
    )

    from app.core.emergency_access.routes.emergency_access_routes import (
        emergency_access_bp,
    )

    from app.core.clinical_safety.routes.clinical_safety_routes import (
        clinical_safety_bp,
    )

    from app.modules.feedback.routes.feedback_routes import (
        feedback_bp,
    )

    app.register_blueprint(
        web_bp
    )

    app.register_blueprint(
        health_bp
    )

    api_blueprints = (
        auth_bp,
        vehicle_bp,
        trip_bp,
        hie_bp,
        pharmacy_bp,
        appointment_bp,
        audit_bp,
        ai_bp,
        billing_bp,
        clinic_bp,
        consultation_bp,
        department_bp,
        inventory_bp,
        lab_bp,
        patient_bp,
        prescription_bp,
        reports_bp,
        staff_bp,
        staff_department_bp,
        ward_bp,
        notification_bp,
        settings_bp,
        user_device_bp,
        asset_bp,
        profile_bp,
        access_control_bp,
        chat_bp,
        dashboard_bp,
        operations_bp,
        failure_dashboard_bp,
        emergency_access_bp,
        clinical_safety_bp,
        feedback_bp,
    )

    for blueprint in api_blueprints:
        register_api_blueprint(
            app,
            blueprint,
        )
