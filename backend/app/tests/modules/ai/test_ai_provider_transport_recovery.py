from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.ai_enums import AIFeature
from app.extensions import db
from app.modules.ai.ai_provider_exceptions import (
    AIProviderError,
    AIProviderFailureClass,
    AIProviderHTTPError,
)
from app.modules.ai.models.ai_model import AILog
from app.modules.ai.services import ai_service
from app.modules.clinic.models.clinic_model import Clinic


def make_drug_result():
    return {
        "summary": "No clinically significant interaction found.",
        "interactions": [],
        "recommendations": [
            "Continue routine monitoring.",
        ],
    }


def install_fake_openai(
    monkeypatch,
    provider_exception=None,
):
    module = ModuleType("openai")

    class FakeCompletions:
        def create(self, **kwargs):
            if provider_exception is not None:
                raise provider_exception

            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"summary":"No clinically significant '
                                'interaction found.","interactions":[],'
                                '"recommendations":["Continue routine '
                                'monitoring."]}'
                            ),
                        ),
                    ),
                ],
            )

    class FakeOpenAI:
        def __init__(self, api_key, **client_options):
            self.chat = SimpleNamespace(
                completions=FakeCompletions(),
            )

    module.OpenAI = FakeOpenAI

    monkeypatch.setitem(
        sys.modules,
        "openai",
        module,
    )


def test_timeout_failure_is_retryable(
    app,
    monkeypatch,
):
    class APITimeoutError(Exception):
        pass

    with app.app_context():
        app.config["OPENAI_API_KEY"] = "test-key"

        install_fake_openai(
            monkeypatch,
            APITimeoutError("provider timeout"),
        )

        with pytest.raises(
            AIProviderError
        ) as exc_info:
            ai_service._call_openai(
                feature=AIFeature.DRUG_INTERACTION_CHECK,
                payload={
                    "drug_names": [
                        "Aspirin",
                        "Warfarin",
                    ],
                },
            )

        assert exc_info.value.failure_class is (
            AIProviderFailureClass.RETRYABLE
        )
        assert exc_info.value.status_code == 503


def test_connection_failure_is_retryable(
    app,
    monkeypatch,
):
    class APIConnectionError(Exception):
        pass

    with app.app_context():
        app.config["OPENAI_API_KEY"] = "test-key"

        install_fake_openai(
            monkeypatch,
            APIConnectionError("provider connection failed"),
        )

        with pytest.raises(
            AIProviderError
        ) as exc_info:
            ai_service._call_openai(
                feature=AIFeature.DRUG_INTERACTION_CHECK,
                payload={
                    "drug_names": [
                        "Aspirin",
                        "Warfarin",
                    ],
                },
            )

        assert exc_info.value.failure_class is (
            AIProviderFailureClass.RETRYABLE
        )
        assert exc_info.value.status_code == 503


@pytest.mark.parametrize(
    "status_code",
    [
        429,
        500,
        502,
        503,
        504,
    ],
)
def test_retryable_http_failures_are_classified(
    app,
    monkeypatch,
    status_code,
):
    provider_exception = type(
        "ProviderHTTPError",
        (Exception,),
        {
            "status_code": status_code,
        },
    )("provider http failure")

    with app.app_context():
        app.config["OPENAI_API_KEY"] = "test-key"

        install_fake_openai(
            monkeypatch,
            provider_exception,
        )

        with pytest.raises(
            AIProviderHTTPError
        ) as exc_info:
            ai_service._call_openai(
                feature=AIFeature.DRUG_INTERACTION_CHECK,
                payload={
                    "drug_names": [
                        "Aspirin",
                        "Warfarin",
                    ],
                },
            )

        assert exc_info.value.status_code == status_code
        assert exc_info.value.failure_class is (
            AIProviderFailureClass.RETRYABLE
        )


def test_unknown_outcome_requires_reconciliation(
    app,
    monkeypatch,
):
    class UnknownOutcomeError(Exception):
        pass

    with app.app_context():
        app.config["OPENAI_API_KEY"] = "test-key"

        install_fake_openai(
            monkeypatch,
            UnknownOutcomeError("request outcome unknown"),
        )

        with pytest.raises(
            AIProviderError
        ) as exc_info:
            ai_service._call_openai(
                feature=AIFeature.DRUG_INTERACTION_CHECK,
                payload={
                    "drug_names": [
                        "Aspirin",
                        "Warfarin",
                    ],
                },
            )

        assert exc_info.value.failure_class is (
            AIProviderFailureClass.RECONCILIATION_REQUIRED
        )
        assert exc_info.value.status_code == 503


def test_retryable_provider_failure_rolls_back_ai_state(
    app,
    clinic,
):
    clinic_id = clinic.id

    before_credits = clinic.ai_credits
    before_requests = clinic.ai_requests_this_month
    before_ai_logs = (
        db.session.query(AILog)
        .filter(AILog.clinic_id == clinic_id)
        .count()
    )
    before_audits = db.session.query(AuditLog).count()

    db.session.commit()

    def failing_provider(feature, payload):
        raise AIProviderError(
            "temporary provider outage",
            failure_class=AIProviderFailureClass.RETRYABLE,
        )

    with pytest.raises(
        AIProviderError
    ) as exc_info:
        ai_service._run_feature(
            feature=AIFeature.DRUG_INTERACTION_CHECK,
            clinic_id=clinic_id,
            payload={
                "drug_names": [
                    "Aspirin",
                    "Warfarin",
                ],
            },
            provider=failing_provider,
        )

    assert exc_info.value.failure_class is (
        AIProviderFailureClass.RETRYABLE
    )

    clinic_state = db.session.get(
        Clinic,
        clinic_id,
    )

    assert clinic_state is not None
    assert clinic_state.ai_credits == before_credits
    assert clinic_state.ai_requests_this_month == before_requests

    after_ai_logs = (
        db.session.query(AILog)
        .filter(AILog.clinic_id == clinic_id)
        .count()
    )
    after_audits = db.session.query(AuditLog).count()

    assert after_ai_logs == before_ai_logs
    assert after_audits == before_audits


def test_recovery_after_transport_failure_succeeds_cleanly(
    app,
    clinic,
):
    clinic_id = clinic.id

    before_credits = clinic.ai_credits
    before_requests = clinic.ai_requests_this_month

    db.session.commit()

    calls = {
        "count": 0,
    }

    def failing_provider(feature, payload):
        calls["count"] += 1

        raise AIProviderError(
            "temporary provider outage",
            failure_class=AIProviderFailureClass.RETRYABLE,
        )

    with pytest.raises(
        AIProviderError
    ):
        ai_service._run_feature(
            feature=AIFeature.DRUG_INTERACTION_CHECK,
            clinic_id=clinic_id,
            payload={
                "drug_names": [
                    "Aspirin",
                    "Warfarin",
                ],
            },
            provider=failing_provider,
        )

    clinic_state = db.session.get(
        Clinic,
        clinic_id,
    )

    assert clinic_state is not None
    assert clinic_state.ai_credits == before_credits
    assert clinic_state.ai_requests_this_month == before_requests
    assert calls["count"] == 1

    result = ai_service._run_feature(
        feature=AIFeature.DRUG_INTERACTION_CHECK,
        clinic_id=clinic_id,
        payload={
            "drug_names": [
                "Aspirin",
                "Warfarin",
            ],
        },
        provider=lambda feature, payload: make_drug_result(),
    )

    assert result == make_drug_result()

    clinic_state = db.session.get(
        Clinic,
        clinic_id,
    )

    assert clinic_state is not None
    assert clinic_state.ai_credits == before_credits - 1
    assert clinic_state.ai_requests_this_month == (
        before_requests + 1
    )

    logs = (
        db.session.query(AILog)
        .filter(AILog.clinic_id == clinic_id)
        .all()
    )

    assert len(logs) == 1
    assert logs[0].output_data == make_drug_result()


def test_reconciliation_failure_is_not_retried_inside_service(
    app,
    clinic,
):
    clinic_id = clinic.id

    db.session.commit()

    calls = {
        "count": 0,
    }

    def reconciliation_provider(feature, payload):
        calls["count"] += 1

        raise AIProviderError(
            "request outcome unknown",
            failure_class=(
                AIProviderFailureClass.RECONCILIATION_REQUIRED
            ),
        )

    with pytest.raises(
        AIProviderError
    ) as exc_info:
        ai_service._run_feature(
            feature=AIFeature.DRUG_INTERACTION_CHECK,
            clinic_id=clinic_id,
            payload={
                "drug_names": [
                    "Aspirin",
                    "Warfarin",
                ],
            },
            provider=reconciliation_provider,
        )

    assert exc_info.value.failure_class is (
        AIProviderFailureClass.RECONCILIATION_REQUIRED
    )
    assert calls["count"] == 1