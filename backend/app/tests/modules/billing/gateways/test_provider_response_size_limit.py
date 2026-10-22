from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import Mock

import pytest
import stripe

from app.modules.billing.services.gateways.base_gateway import (
    GatewayUnknownOutcomeError,
)
from app.modules.billing.services.gateways.flutterwave_gateway import (
    FlutterwaveGateway,
)
from app.modules.billing.services.gateways.paystack_gateway import (
    PaystackGateway,
)
from app.modules.billing.services.gateways.response_utils import (
    MAX_PROVIDER_RESPONSE_BYTES,
    ProviderResponseTooLargeError,
    read_json_response_limited,
    read_response_body_limited,
)
from app.modules.billing.services.gateways.stripe_gateway import (
    BoundedStripeRequestsClient,
    StripeGateway,
)


class StreamingResponse:
    def __init__(self, *, headers=None, chunks=(), status_code=200):
        self.headers = headers or {}
        self.chunks = list(chunks)
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.iterated = False
        self.closed = False

    def iter_content(self, chunk_size):
        self.iterated = True
        yield from self.chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


def test_declared_oversized_response_is_rejected_and_closed_before_reading():
    response = StreamingResponse(
        headers={"Content-Length": str(MAX_PROVIDER_RESPONSE_BYTES + 1)},
        chunks=[b"ignored"],
    )

    with pytest.raises(ProviderResponseTooLargeError):
        read_response_body_limited(response)

    assert response.closed is True
    assert response.iterated is False


def test_streamed_body_overflow_is_rejected_and_response_closed():
    response = StreamingResponse(chunks=[b"abc", b"de", b"ignored"])

    with pytest.raises(ProviderResponseTooLargeError):
        read_response_body_limited(response, max_bytes=4)

    assert response.closed is True
    assert response.iterated is True


def test_body_at_exact_limit_is_accepted_and_response_closed():
    response = StreamingResponse(chunks=[b"ab", b"cd"])

    assert read_response_body_limited(response, max_bytes=4) == b"abcd"
    assert response.closed is True


def test_json_is_parsed_only_after_bounded_stream_read():
    response = StreamingResponse(chunks=[b'{"status":', b' "ok"}'])

    assert read_json_response_limited(response, max_bytes=32) == {"status": "ok"}
    assert response.closed is True


@pytest.mark.parametrize("provider", ["paystack", "flutterwave"])
def test_oversized_initialization_response_remains_unknown_outcome(provider, monkeypatch):
    response = StreamingResponse(
        headers={"Content-Length": str(MAX_PROVIDER_RESPONSE_BYTES + 1)}
    )

    if provider == "paystack":
        gateway = PaystackGateway(credentials={"secret_key": "sk_test"})
        request_path = "app.modules.billing.services.gateways.paystack_gateway.requests.post"
    else:
        gateway = FlutterwaveGateway(
            credentials={"secret_key": "flw_test", "webhook_secret": "flw_webhook"}
        )
        request_path = "app.modules.billing.services.gateways.flutterwave_gateway.requests.post"

    request_mock = Mock(return_value=response)
    monkeypatch.setattr(request_path, request_mock)

    with pytest.raises(GatewayUnknownOutcomeError):
        gateway.initialize_payment(
            reference="SIZE-LIMIT-001",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )

    assert response.closed is True
    assert response.iterated is False
    request_mock.assert_called_once()
    assert request_mock.call_args.kwargs["stream"] is True


@pytest.mark.parametrize("provider", ["paystack", "flutterwave"])
def test_verification_http_requests_stream_response_bodies(provider, monkeypatch):
    reference = "PAY-SIZE-VERIFY-001"
    verification_reference = reference
    if provider == "paystack":
        gateway = PaystackGateway(credentials={"secret_key": "sk_test"})
        request_path = "app.modules.billing.services.gateways.paystack_gateway.requests.get"
        body = {
            "status": True,
            "data": {
                "id": 12345,
                "reference": reference,
                "status": "success",
                "amount": 10000,
                "currency": "NGN",
            },
        }
    else:
        gateway = FlutterwaveGateway(
            credentials={"secret_key": "flw_test", "webhook_secret": "flw_webhook"}
        )
        # Flutterwave's verification endpoint is keyed by numeric transaction ID;
        # tx_ref in the provider response remains the separate merchant reference.
        verification_reference = "12345"
        request_path = "app.modules.billing.services.gateways.flutterwave_gateway.requests.get"
        body = {
            "status": "success",
            "data": {
                "id": 12345,
                "tx_ref": reference,
                "status": "successful",
                "amount": 10000,
                "charged_amount": 10000,
                "currency": "NGN",
            },
        }

    response = StreamingResponse(chunks=[json.dumps(body).encode("utf-8")])
    request_mock = Mock(return_value=response)
    monkeypatch.setattr(request_path, request_mock)

    result = gateway.verify_payment(reference=verification_reference)

    assert result["transaction_id"] == "12345"
    assert result["reference"] == reference
    request_mock.assert_called_once()
    assert request_mock.call_args.kwargs["stream"] is True
    assert response.closed is True


def test_stripe_http_adapter_propagates_response_limit_from_pinned_transport(monkeypatch):
    request_mock = Mock(side_effect=ProviderResponseTooLargeError("too large"))
    monkeypatch.setattr(
        "app.modules.billing.services.gateways.stripe_gateway.request_payment_provider",
        request_mock,
    )
    client = BoundedStripeRequestsClient(timeout=30)

    with pytest.raises(ProviderResponseTooLargeError):
        client.request("GET", "https://api.stripe.com/v1/payment_intents", {})

    request_mock.assert_called_once_with(
        provider="stripe",
        method="GET",
        url="https://api.stripe.com/v1/payment_intents",
        headers={},
        data=None,
        timeout=30,
        verify_ssl_certs=True,
        proxy=None,
        ca_bundle_path=stripe.ca_bundle_path,
    )


def test_stripe_initialization_classifies_oversized_response_as_unknown(monkeypatch):
    gateway = StripeGateway(
        credentials={"secret_key": "sk_test_size_limit", "webhook_secret": "whsec_size_limit"}
    )
    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        Mock(side_effect=ProviderResponseTooLargeError("too large")),
    )

    with pytest.raises(GatewayUnknownOutcomeError, match="outcome remains unknown"):
        gateway.initialize_payment(
            reference="STRIPE-SIZE-LIMIT",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )
