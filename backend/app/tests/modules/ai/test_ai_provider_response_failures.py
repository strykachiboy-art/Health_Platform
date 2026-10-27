from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.core.enums.ai_enums import AIFeature
from app.extensions import db
from app.modules.ai.ai_provider_exceptions import (
    AIProviderFailureClass,
    AIProviderResponseError,
)
from app.modules.ai.models.ai_model import AILog
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.ai.services import ai_service


def _install_fake_openai(
    monkeypatch,
    response,
):
    module = ModuleType("openai")

    class FakeCompletions:
        def create(self, **kwargs):
            return response

    class FakeOpenAI:
        def __init__(self, api_key, **client_options):
            self.chat = SimpleNamespace(
                completions=FakeCompletions()
            )

    module.OpenAI = FakeOpenAI

    monkeypatch.setitem(
        sys.modules,
        "openai",
        module,
    )


def _valid_drug_result():
    return {
        "summary": (
            "No clinically significant interaction found."
        ),
        "interactions": [],
        "recommendations": [
            "Continue routine monitoring.",
        ],
    }


@pytest.mark.parametrize(
    "response,expected_message",
    [
        (
            SimpleNamespace(choices=[]),
            "AI provider returned no choices",
        ),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=""
                        )
                    )
                ]
            ),
            "AI provider returned an empty response",
        ),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="   "
                        )
                    )
                ]
            ),
            "AI provider returned an empty response",
        ),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="not-json"
                        )
                    )
                ]
            ),
            "AI provider returned invalid JSON",
        ),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="[]"
                        )
                    )
                ]
            ),
            "AI provider must return a JSON object",
        ),
        (
            # choice with no .message -> content resolves to None
            SimpleNamespace(
                choices=[
                    SimpleNamespace()
                ]
            ),
            "AI provider returned an empty response",
        ),
        (
            # non-string content (e.g. list of parts)
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=["part"]
                        )
                    )
                ]
            ),
            "AI provider returned malformed response",
        ),
    ],
)
def test_openai_malformed_outputs_fail_closed(
    app,
    monkeypatch,
    response,
    expected_message,
):
    with app.app_context():
        app.config["OPENAI_API_KEY"] = "test-key"

        _install_fake_openai(
            monkeypatch,
            response,
        )

        with pytest.raises(
            AIProviderResponseError
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

        error = exc_info.value

        assert error.failure_class is (
            AIProviderFailureClass.FAIL_CLOSED
        )
        assert error.status_code == 422
        assert str(error) == expected_message


def test_openai_valid_json_object_remains_valid(
    app,
    monkeypatch,
):
    with app.app_context():
        app.config["OPENAI_API_KEY"] = "test-key"

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"summary":"valid"}'
                        )
                    )
                )
            ]
        )

        _install_fake_openai(
            monkeypatch,
            response,
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

        assert result == {
            "summary": "valid",
        }


@pytest.mark.parametrize(
    "provider_result",
    [
        None,
        [],
        "unexpected text",
    ],
)
def test_run_feature_rejects_non_object_output_and_rolls_back(
    app,
    clinic,
    provider_result,
):
    clinic_id = clinic.id
    credits_before = clinic.ai_credits
    requests_before = clinic.ai_requests_this_month

    db.session.commit()

    with pytest.raises(
        AIProviderResponseError
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
            provider=lambda feature, payload: provider_result,
        )

    assert exc_info.value.failure_class is (
        AIProviderFailureClass.FAIL_CLOSED
    )
    assert exc_info.value.status_code == 422

    clinic_state = db.session.get(
        Clinic,
        clinic_id,
    )

    assert clinic_state is not None
    assert clinic_state.ai_credits == credits_before
    assert clinic_state.ai_requests_this_month == (
        requests_before
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




def test_run_feature_rejects_unexpected_schema_and_rolls_back(
    app,
    clinic,
):
    clinic_id = clinic.id
    credits_before = clinic.ai_credits
    requests_before = clinic.ai_requests_this_month

    db.session.commit()

    with pytest.raises(
        AIProviderResponseError
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
            provider=lambda feature, payload: {
                "summary": "Invalid provider response",
                "unexpected": "blocked",
            },
        )

    assert exc_info.value.failure_class is (
        AIProviderFailureClass.FAIL_CLOSED
    )
    assert exc_info.value.status_code == 422
    assert str(exc_info.value) == (
        "AI provider returned invalid "
        "drug_interaction_check response"
    )

    clinic_state = db.session.get(
        Clinic,
        clinic_id,
    )

    assert clinic_state is not None
    assert clinic_state.ai_credits == credits_before
    assert clinic_state.ai_requests_this_month == (
        requests_before
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




def test_valid_provider_output_still_creates_log(
    app,
    clinic,
):
    # PostgreSQL uses a separate scoped session for the nested
    # app context below, so make the fixture baseline visible.
    db.session.commit()

    with app.app_context():
        result = ai_service._run_feature(
            feature=AIFeature.DRUG_INTERACTION_CHECK,
            clinic_id=clinic.id,
            payload={
                "drug_names": [
                    "Aspirin",
                    "Warfarin",
                ],
            },
            provider=lambda feature, payload: (
                _valid_drug_result()
            ),
        )

        assert result == _valid_drug_result()

        logs = (
            db.session.execute(
                db.select(AILog).where(
                    AILog.clinic_id == clinic.id,
                )
            )
            .scalars()
            .all()
        )

        assert len(logs) == 1
        assert logs[0].output_data == (
            _valid_drug_result()
        )
