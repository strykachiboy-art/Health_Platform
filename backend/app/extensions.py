from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
import redis

from app.core.auth.user.services.token_service import (
    is_token_revoked,
)


db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
cors = CORS()
limiter = Limiter(
    key_func=get_remote_address
)
socketio = SocketIO()
celery = Celery(__name__)

redis_client = None


@jwt.token_in_blocklist_loader
def check_if_token_revoked(
    jwt_header,
    jwt_payload,
):
    """
    JWT blocklist callback.

    Token revocation remains fail-closed through
    token_service.is_token_revoked().
    """
    return is_token_revoked(jwt_payload)


def _resolve_cors_origins(
    app,
):
    configured_origins = app.config.get(
        "CORS_ALLOWED_ORIGINS",
        "*",
    )

    if isinstance(
        configured_origins,
        str,
    ):
        configured_origins = (
            configured_origins.strip()
        )

        if configured_origins == "*":
            return "*"

        return [
            origin.strip()
            for origin in configured_origins.split(",")
            if origin.strip()
        ]

    if isinstance(
        configured_origins,
        (list, tuple, set),
    ):
        return [
            str(origin).strip()
            for origin in configured_origins
            if str(origin).strip()
        ]

    return configured_origins


def init_extensions(app):
    global redis_client

    db.init_app(app)
    migrate.init_app(
        app,
        db,
    )
    jwt.init_app(app)

    cors_origins = _resolve_cors_origins(
        app
    )

    cors.init_app(
        app,
        origins=cors_origins,
        supports_credentials=False,
    )

    limiter.init_app(app)

    redis_url = app.config["REDIS_URL"]

    redis_client = redis.StrictRedis.from_url(
        redis_url,
        decode_responses=True,
    )

    socketio_options = {
        "cors_allowed_origins": (
            cors_origins
        ),
    }

    if not app.config.get(
        "TESTING",
        False,
    ):
        socketio_options[
            "message_queue"
        ] = redis_url

    previous_message_queue = (
        getattr(
            socketio,
            "server_options",
            {},
        ).get("message_queue")
    )
    current_message_queue = (
        socketio_options.get("message_queue")
    )

    if (
        previous_message_queue
        != current_message_queue
    ):
        socketio.server = None
        socketio.server_options = {}

    socketio.init_app(
        app,
        **socketio_options,
    )

    broker_url = app.config.get(
        "CELERY_BROKER_URL",
        redis_url,
    )

    previous_broker_url = celery.conf.get(
        "broker_url"
    )

    if previous_broker_url != broker_url:
        from kombu import pools

        pools.reset()
        celery.amqp._producer_pool = None

    celery.conf.update(
        broker_url=broker_url,
        result_backend=app.config.get(
            "CELERY_RESULT_BACKEND",
            redis_url,
        ),
        task_acks_late=app.config.get(
            "CELERY_TASK_ACKS_LATE",
            False,
        ),
        task_reject_on_worker_lost=app.config.get(
            "CELERY_TASK_REJECT_ON_WORKER_LOST",
        ),
        worker_prefetch_multiplier=app.config.get(
            "CELERY_WORKER_PREFETCH_MULTIPLIER",
            4,
        ),
        timezone="UTC",
        beat_schedule={
            "check-upcoming-appointments-hourly": {
                "task": "check_upcoming_appointments",
                "schedule": 3600.0,
            },
            "mark-overdue-invoices-hourly": {
                "task": "mark_overdue_invoices",
                "schedule": 3600.0,
            },
            "process-chat-outbox-every-5-seconds": {
                "task": "process_chat_outbox",
                "schedule": 5.0,
            },
            "recover-stale-chat-outbox-every-minute": {
                "task": "recover_stale_chat_outbox",
                "schedule": 60.0,
            },
            "reset-monthly-ai-usage": {
                "task": "reset_monthly_ai_usage",
                "schedule": crontab(
                    day_of_month=1,
                    hour=0,
                    minute=0,
                ),
            },
            "run-scheduled-backup-daily": {
                "task": "run_scheduled_backup",
                "schedule": crontab(
                    hour=2,
                    minute=0,
                ),
            },
            "run-backup-retention-daily": {
                "task": "run_backup_retention",
                "schedule": crontab(
                    hour=3,
                    minute=0,
                ),
            },
        },
    )