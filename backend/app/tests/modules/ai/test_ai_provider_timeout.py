from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.core.enums.ai_enums import AIFeature
from app.core.exceptions import ValidationError
from app.extensions import db
from app.modules.ai.ai_provider_exceptions import (
    AIProviderError,
    AIProviderFailureClass,
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
    *,
    provider_exception=None,
    capture=None,
):
    module = ModuleType("openai")

    class FakeCompletions:
        def create(self, **kwargs):
            if capture is not None:
                capture.update(kwargs)

            if provider_exception is not None:
                raise provider_exception

            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"summary":"No clinically '
                                'significant interaction found.",'
                                '"interactions":[],"recommendations":'
                                '["Continue routine monitoring."]}'
                            ),
                        ),
                    ),
                ],
            )

    module.client_options = {}

    class FakeOpenAI:
        def __init__(self, api_key, **client_options):
            module.client_options = {
                "api_key": api_key,
                **client_options,
            }
            self.chat = SimpleNamespace(
                completions=FakeCompletions(),
            )

    module.OpenAI = FakeOpenAI

    monkeypatch.setitem(
        sys.modules,
        "openai",
        module,
    )

    return module


def test_provider_timeout_default_is_configured(
    app,
):
    assert (
        app.config["AI_PROVIDER_TIMEOUT_SECONDS"]
        == 30
    )

    assert (
        ai_service._get_provider_timeout_seconds()
        == 30.0
    )


@pytest.mark.parametrize(
    "invalid_value",
    [
        0,
        -1,
        True,
        False,
        "30",
        None,
    ],
)
def test_provider_timeout_rejects_invalid_configuration(
    app,
    invalid_value,
):
    app.config[
        "AI_PROVIDER_TIMEOUT_SECONDS"
    ] = invalid_value

    with pytest.raises(
        ValidationError,
        match=(
            "AI_PROVIDER_TIMEOUT_SECONDS "
            "must be a positive number"
        ),
    ):
        ai_service._get_provider_timeout_seconds()


def test_provider_timeout_is_passed_to_openai(
    app,
    monkeypatch,
):
    capture = {}

    app.config[
        "OPENAI_API_KEY"
    ] = "test-key"

    fake_openai = install_fake_openai(
        monkeypatch,
        capture=capture,
    )

    result = ai_service._call_openai(
        feature=AIFeature.DRUG_INTERACTION_CHECK,
        payload={
            "drug_names": [
                "Aspirin",
                "Warfarin",
            ],
        },
    )

    assert result == make_drug_result()
    assert capture["timeout"] == 30.0
    assert fake_openai.client_options == {
        "api_key": "test-key",
        "base_url": "https://api.openai.com/v1",
        "timeout": 30.0,
        "max_retries": 0,
    }


def test_provider_timeout_failure_is_retryable(
    app,
    monkeypatch,
):
    class APITimeoutError(Exception):
        pass

    app.config[
        "OPENAI_API_KEY"
    ] = "test-key"

    install_fake_openai(
        monkeypatch,
        provider_exception=APITimeoutError(
            "provider timeout"
        ),
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


def test_provider_timeout_rolls_back_ai_state(
    app,
    clinic,
    monkeypatch,
):
    clinic_id = clinic.id

    before_credits = clinic.ai_credits
    before_requests = clinic.ai_requests_this_month

    db.session.commit()

    class APITimeoutError(Exception):
        pass

    app.config[
        "OPENAI_API_KEY"
    ] = "test-key"

    install_fake_openai(
        monkeypatch,
        provider_exception=APITimeoutError(
            "slow provider response"
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
            provider=ai_service._call_openai,
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
    assert clinic_state.ai_requests_this_month == (
        before_requests
    )

    logs = (
        db.session.execute(
            db.select(AILog).where(
                AILog.clinic_id == clinic_id,
            )
        )
        .scalars()
        .all()
    )

    assert logs == []