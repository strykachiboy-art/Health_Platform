from __future__ import annotations

import logging
import re
from typing import Any


_REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "password",
        "password_hash",
        "secret",
        "secret_key",
        "token",
        "access_token",
        "refresh_token",
        "credentials",
        "api_key",
        "patient_name",
        "first_name",
        "last_name",
        "date_of_birth",
        "diagnosis",
        "clinical_note",
        "note_text",
        "medical_record",
        "emirates_id",
        "umrn",
        "phone",
        "email",
        "address",
    }
)

_INLINE_SENSITIVE = re.compile(
    r"(?i)\b("
    + "|".join(
        re.escape(key)
        for key in sorted(
            _SENSITIVE_KEYS,
            key=len,
            reverse=True,
        )
    )
    + r")\b\s*[:=]\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def _key_is_sensitive(
    key: Any,
) -> bool:
    if not isinstance(key, str):
        return False

    return key.strip().lower() in _SENSITIVE_KEYS


def sanitize_log_value(
    value: Any,
    *,
    key: Any = None,
):
    if _key_is_sensitive(
        key
    ):
        return _REDACTED

    if isinstance(value, dict):
        return {
            item_key: sanitize_log_value(
                item_value,
                key=item_key,
            )
            for item_key, item_value
            in value.items()
        }

    if isinstance(value, list):
        return [
            sanitize_log_value(
                item
            )
            for item in value
        ]

    if isinstance(value, tuple):
        return tuple(
            sanitize_log_value(
                item
            )
            for item in value
        )

    if isinstance(value, set):
        return {
            sanitize_log_value(
                item
            )
            for item in value
        }

    if isinstance(value, str):
        return _INLINE_SENSITIVE.sub(
            lambda match: (
                f"{match.group(1)}={_REDACTED}"
            ),
            value,
        )

    return value


class PHISafeLogFilter(logging.Filter):
    def filter(
        self,
        record: logging.LogRecord,
    ) -> bool:
        if not record.args:
            record.msg = sanitize_log_value(
                record.msg
            )

        if isinstance(
            record.args,
            dict,
        ):
            record.args = sanitize_log_value(
                record.args
            )
        elif isinstance(
            record.args,
            tuple,
        ):
            record.args = tuple(
                sanitize_log_value(
                    value
                )
                for value in record.args
            )

        standard = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
        }

        for key, value in list(
            record.__dict__.items()
        ):
            if key in standard:
                continue

            record.__dict__[key] = (
                sanitize_log_value(
                    value,
                    key=key,
                )
            )

        if record.exc_text:
            record.exc_text = sanitize_log_value(
                record.exc_text
            )

        return True


def install_phi_safe_logging(
    app,
) -> None:
    root_logger = logging.getLogger()

    handlers = []

    for handler in (
        list(root_logger.handlers)
        + list(app.logger.handlers)
    ):
        if handler not in handlers:
            handlers.append(
                handler
            )

    for handler in handlers:
        if any(
            isinstance(
                item,
                PHISafeLogFilter,
            )
            for item in handler.filters
        ):
            continue

        handler.addFilter(
            PHISafeLogFilter()
        )
