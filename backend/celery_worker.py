import os

from app.extensions import celery


class ContextTask(celery.Task):
    """Ensures every Celery task runs inside the Flask app context."""

    def __call__(self, *args, **kwargs):
        with flask_app.app_context():
            return self.run(*args, **kwargs)


celery.Task = ContextTask


from app import create_app


flask_app = create_app(
    os.environ.get(
        "FLASK_ENV",
        "development",
    )
)


from app.modules.appointment.services import appointment_service
from app.modules.billing.services import billing_service
from app.modules.clinic.services import clinic_service
from app.core.notifications.services import notification_service
from app.modules.chat.tasks import chat_outbox_task
from app.core.backup import backup_tasks
from app.core.observability import alert_tasks