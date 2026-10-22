from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from app.modules.billing.services.gateways.paystack_gateway import (
    PaystackGateway,
)
from app.modules.billing.services.gateways.base_gateway import (
    GatewayRejectedError,
    GatewayUnknownOutcomeError,
)


def _enable_streaming_json(response):
    response.headers = {}
    response.close = Mock()
    response.iter_content.side_effect = lambda chunk_size: iter(
        [json.dumps(response.json()).encode("utf-8")]
    )


@pytest.fixture
def paystack_credentials():
    return {
        "secret_key": "sk_test_secret",
    }


@pytest.fixture
def gateway(paystack_credentials):
    return PaystackGateway(
        credentials=paystack_credentials,
    )
# ---------------------------------------------------------------------------
# Phase 9 - Gate 4 / Slice 1
# Provider identity replay and failure semantics
# ---------------------------------------------------------------------------


def test_initialize_payment_reuses_stable_reference_on_retry(
    gateway,
    monkeypatch,
):
    response = Mock()
    response.ok = True
    response.json.side_effect = [
        {
            "status": True,
            "data": {
                "reference": "PAY-RETRY-001",
                "authorization_url": (
                    "https://paystack.test/authorize/1"
                ),
                "access_code": "access-1",
            },
        },
        {
            "status": True,
            "data": {
                "reference": "PAY-RETRY-001",
                "authorization_url": (
                    "https://paystack.test/authorize/1"
                ),
                "access_code": "access-1",
            },
        },
    ]

    mock_post = Mock(
        side_effect=[
            response,
            response,
        ]
    )

    _enable_streaming_json(response)

    monkeypatch.setattr(
        "app.modules.billing.services.gateways.paystack_gateway.requests.post",
        mock_post,
    )

    first = gateway.initialize_payment(
        reference="PAY-RETRY-001",
        amount=Decimal("100"),
        currency="NGN",
        customer_email="patient@example.com",
    )

    second = gateway.initialize_payment(
        reference="PAY-RETRY-001",
        amount=Decimal("100"),
        currency="NGN",
        customer_email="patient@example.com",
    )

    assert first["reference"] == "PAY-RETRY-001"
    assert second["reference"] == "PAY-RETRY-001"

    assert mock_post.call_count == 2

    first_payload = mock_post.call_args_list[0].kwargs["json"]
    second_payload = mock_post.call_args_list[1].kwargs["json"]

    assert first_payload["reference"] == "PAY-RETRY-001"
    assert second_payload["reference"] == "PAY-RETRY-001"
    assert (
        first_payload["reference"]
        == second_payload["reference"]
    )


def test_initialize_payment_preserves_reference_when_provider_rejects_request(
    gateway,
    monkeypatch,
):
    response = Mock()
    response.ok = False
    response.json.return_value = {
        "status": False,
        "message": "Duplicate transaction",
    }

    mock_post = Mock(
        return_value=response
    )

    _enable_streaming_json(response)

    monkeypatch.setattr(
        "app.modules.billing.services.gateways.paystack_gateway.requests.post",
        mock_post,
    )

    with pytest.raises(
        RuntimeError,
        match="Paystack payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="PAY-REJECTED-001",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )

    payload = mock_post.call_args.kwargs["json"]

    assert payload["reference"] == "PAY-REJECTED-001"


def test_initialize_payment_maps_transport_failure_to_runtime_error(
    gateway,
    monkeypatch,
):
    mock_post = Mock(
        side_effect=requests.Timeout(
            "provider timeout"
        )
    )

    monkeypatch.setattr(
        "app.modules.billing.services.gateways.paystack_gateway.requests.post",
        mock_post,
    )

    with pytest.raises(
        RuntimeError,
        match="Paystack payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="PAY-TIMEOUT-001",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )

    mock_post.assert_called_once()

# ---------------------------------------------------------------------------
# Phase 9 - Gate 4 / Slice 2A
# Provider outcome classification
# ---------------------------------------------------------------------------


def test_initialize_payment_transport_failure_is_unknown_outcome(
    gateway,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.billing.services.gateways.paystack_gateway.requests.post",
        Mock(
            side_effect=requests.Timeout(
                "provider timeout"
            )
        ),
    )

    with pytest.raises(
        GatewayUnknownOutcomeError,
        match="Paystack payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="PAY-UNKNOWN-001",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )


def test_initialize_payment_provider_rejection_is_known_failure(
    gateway,
    monkeypatch,
):
    response = Mock()
    response.ok = False
    response.json.return_value = {
        "status": False,
        "message": "Duplicate transaction",
    }

    _enable_streaming_json(response)

    monkeypatch.setattr(
        "app.modules.billing.services.gateways.paystack_gateway.requests.post",
        Mock(return_value=response),
    )

    with pytest.raises(
        GatewayRejectedError,
        match="Paystack payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="PAY-REJECTED-002",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )
