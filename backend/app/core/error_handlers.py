from __future__ import annotations

import logging

from flask import Flask, g, has_request_context, jsonify, request
from pydantic import ValidationError as PydanticValidationError
from werkzeug.exceptions import HTTPException

from app.core.exceptions import DomainError
from app.core.observability.failure_events import (
    record_failure_event,
)
from app.core.observability.tracing import (
    get_trace_id,
)
from app import extensions


logger = logging.getLogger(__name__)


def _error_response(message, status_code, details=None, code=None):
    body = {
        "success": False,
        "error": message,
    }

    if code is not None:
        body["code"] = code

    if details is not None:
        body["details"] = details

    return jsonify(body), status_code


def _sanitize_pydantic_errors(errors):
    sanitized = []

    for error in errors:
        item = dict(error)
        item.pop("input", None)

        if "ctx" in item and isinstance(item["ctx"], dict):
            item["ctx"] = {
                key: str(value)
                for key, value in item["ctx"].items()
            }

        sanitized.append(item)

    return sanitized


def register_error_handlers(app: Flask) -> None:

    @app.errorhandler(DomainError)
    def handle_domain_error(error: DomainError):
        return _error_response(
            str(error),
            error.status_code,
            code=getattr(error, "code", None),
        )

    @app.errorhandler(PydanticValidationError)
    def handle_pydantic_error(error: PydanticValidationError):
        details = _sanitize_pydantic_errors(error.errors())

        return _error_response(
            "Validation error",
            422,
            details,
        )

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return _error_response(
            error.description,
            error.code or 500,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        request_id = None
        method = None
        route = None

        if has_request_context():
            request_id = getattr(
                g,
                "request_id",
                None,
            )
            method = request.method

            if request.url_rule is not None:
                route = request.url_rule.rule

        # Never emit the raw exception text here. Exception
        # messages can contain patient data or secrets from
        # downstream libraries. Request/trace IDs plus the safe
        # exception type preserve operational correlation.
        logger.error(
            "application.error",
            extra={
                "operational_event": True,
                "event": "application.error",
                "request_id": request_id,
                "trace_id": get_trace_id(),
                "method": method,
                "route": route,
                "status": 500,
                "error_type": type(error).__name__,
            },
        )

        try:
            record_failure_event(
                redis_client=extensions.redis_client,
                event_type="application.error",
                component="api",
                severity="error",
                request_id=request_id,
                trace_id=get_trace_id(),
                method=method,
                route=route,
                status=500,
                error_type=type(error).__name__,
            )
        except Exception:
            pass


        return _error_response(
            "Internal server error",
            500,
        )