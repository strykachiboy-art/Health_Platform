from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from app.modules.billing.services.gateways.flutterwave_gateway import (
    FlutterwaveGateway,
)
from app.modules.billing.services.gateways.base_gateway import (
    GatewayRejectedError,
    GatewayUnknownOutcomeError,
)


@pytest.fixture
def flutterwave_credentials():
    return {
        "secret_key": "flw_test_secret",
        "webhook_secret": "flw_webhook_secret",
        "public_key": "flw_test_public",
    }


@pytest.fixture
def gateway(flutterwave_credentials):
    return FlutterwaveGateway(
        credentials=flutterwave_credentials,
    )


class FakeResponse:
    def __init__(
        self,
        *,
        ok=True,
        status_code=200,
        json_data=None,
        json_error=None,
    ):
        self.ok = ok
        self.status_code = status_code
        self._json_data = json_data
        self._json_error = json_error
        self.headers = {}
        self.closed = False

    def json(self):
        if self._json_error:
            raise self._json_error

        return self._json_data

    def iter_content(self, chunk_size):
        if self._json_error:
            raise self._json_error
        yield json.dumps(self._json_data).encode("utf-8")

    def close(self):
        self.closed = True
# ---------------------------------------------------------------------------
# Phase 9 - Gate 4 / Slice 1
# Provider identity replay and failure semantics
# ---------------------------------------------------------------------------


def test_initialize_payment_reuses_stable_tx_ref_on_retry(
    gateway,
    monkeypatch,
):
    first_response = FakeResponse(
        ok=True,
        json_data={
            "status": "success",
            "data": {
                "id": 1001,
                "tx_ref": "FLW-RETRY-001",
                "link": (
                    "https://flutterwave.test/pay/1"
                ),
            },
        },
    )

    second_response = FakeResponse(
        ok=True,
        json_data={
            "status": "success",
            "data": {
                "id": 1001,
                "tx_ref": "FLW-RETRY-001",
                "link": (
                    "https://flutterwave.test/pay/1"
                ),
            },
        },
    )

    mock_post = Mock(
        side_effect=[
            first_response,
            second_response,
        ]
    )

    monkeypatch.setattr(
        "app.modules.billing.services.gateways.flutterwave_gateway.requests.post",
        mock_post,
    )

    first = gateway.initialize_payment(
        reference="FLW-RETRY-001",
        amount=Decimal("100"),
        currency="NGN",
        customer_email="patient@example.com",
    )

    second = gateway.initialize_payment(
        reference="FLW-RETRY-001",
        amount=Decimal("100"),
        currency="NGN",
        customer_email="patient@example.com",
    )

    assert first["reference"] == "FLW-RETRY-001"
    assert second["reference"] == "FLW-RETRY-001"

    assert mock_post.call_count == 2

    first_payload = (
        mock_post.call_args_list[0]
        .kwargs["json"]
    )

    second_payload = (
        mock_post.call_args_list[1]
        .kwargs["json"]
    )

    assert first_payload["tx_ref"] == "FLW-RETRY-001"
    assert second_payload["tx_ref"] == "FLW-RETRY-001"
    assert (
        first_payload["tx_ref"]
        == second_payload["tx_ref"]
    )


def test_initialize_payment_preserves_tx_ref_when_provider_rejects_request(
    gateway,
    monkeypatch,
):
    response = FakeResponse(
        ok=False,
        json_data={
            "status": "error",
            "message": "Transaction already exists",
        },
    )

    mock_post = Mock(
        return_value=response
    )

    monkeypatch.setattr(
        "app.modules.billing.services.gateways.flutterwave_gateway.requests.post",
        mock_post,
    )

    with pytest.raises(
        RuntimeError,
        match="Flutterwave payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="FLW-REJECTED-001",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )

    payload = mock_post.call_args.kwargs["json"]

    assert payload["tx_ref"] == "FLW-REJECTED-001"


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
        "app.modules.billing.services.gateways.flutterwave_gateway.requests.post",
        mock_post,
    )

    with pytest.raises(
        RuntimeError,
        match="Flutterwave payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="FLW-TIMEOUT-001",
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
        "app.modules.billing.services.gateways.flutterwave_gateway.requests.post",
        Mock(
            side_effect=requests.Timeout(
                "provider timeout"
            )
        ),
    )

    with pytest.raises(
        GatewayUnknownOutcomeError,
        match="Flutterwave payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="FLW-UNKNOWN-001",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )


def test_initialize_payment_provider_rejection_is_known_failure(
    gateway,
    monkeypatch,
):
    response = FakeResponse(
        ok=False,
        json_data={
            "status": "error",
            "message": "Transaction already exists",
        },
    )

    monkeypatch.setattr(
        "app.modules.billing.services.gateways.flutterwave_gateway.requests.post",
        Mock(return_value=response),
    )

    with pytest.raises(
        GatewayRejectedError,
        match="Flutterwave payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="FLW-REJECTED-002",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )
