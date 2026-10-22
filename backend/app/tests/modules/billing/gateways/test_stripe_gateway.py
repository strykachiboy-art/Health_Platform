from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock

import pytest
import stripe

from app.modules.billing.services.gateways import stripe_gateway
from app.modules.billing.services.gateways.stripe_gateway import (
    StripeGateway,
)
from app.modules.billing.services.gateways.base_gateway import (
    GatewayRejectedError,
    GatewayUnknownOutcomeError,
)


@pytest.fixture
def stripe_credentials():
    return {
        "secret_key": "sk_test_secret",
        "webhook_secret": "whsec_test_secret",
    }


@pytest.fixture
def gateway(stripe_credentials):
    return StripeGateway(
        credentials=stripe_credentials,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_gateway_requires_secret_key():
    credentials = {}

    with pytest.raises(
        ValueError,
        match="Stripe secret key is not configured",
    ):
        StripeGateway(
            credentials=credentials,
        )


@pytest.mark.parametrize(
    "credentials",
    [
        {"secret_key": ""},
        {"secret_key": None},
        {"secret_key": 123},
        {"webhook_secret": "whsec_test_secret"},
    ],
)
def test_gateway_rejects_invalid_secret_key(
    credentials,
):
    with pytest.raises(
        ValueError,
        match="Stripe secret key is not configured",
    ):
        StripeGateway(
            credentials=credentials,
        )


def test_gateway_does_not_mutate_global_stripe_api_key(
    stripe_credentials,
    monkeypatch,
):
    monkeypatch.setattr(
        stripe,
        "api_key",
        "existing_global_key",
    )

    gateway = StripeGateway(
        credentials=stripe_credentials,
    )

    assert gateway.secret_key == "sk_test_secret"
    assert stripe.api_key == "existing_global_key"


def test_build_stripe_http_client_uses_explicit_timeout(monkeypatch):
    client = Mock()
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(stripe, "RequestsClient", client_factory)

    assert stripe_gateway._build_stripe_http_client() is client
    client_factory.assert_called_once_with(timeout=30)


def test_gateway_uses_bounded_client_and_disables_sdk_retries(
    stripe_credentials,
    monkeypatch,
):
    sentinel_http_client = object()
    sentinel_stripe_client = object()
    client_factory = Mock(return_value=sentinel_stripe_client)

    monkeypatch.setattr(
        stripe_gateway,
        "_STRIPE_HTTP_CLIENT",
        sentinel_http_client,
    )
    monkeypatch.setattr(stripe, "StripeClient", client_factory)

    gateway_instance = StripeGateway(credentials=stripe_credentials)

    client_factory.assert_called_once_with(
        api_key="sk_test_secret",
        http_client=sentinel_http_client,
        max_network_retries=0,
    )
    assert gateway_instance._client is sentinel_stripe_client


@pytest.mark.parametrize(
    "request_params,expected_params,expected_options",
    [
        (
            {"amount": 100},
            {"amount": 100},
            {},
        ),
        (
            {"amount": 100, "idempotency_key": "billing-payment-001"},
            {"amount": 100},
            {"idempotency_key": "billing-payment-001"},
        ),
    ],
)
def test_create_payment_intent_uses_client_request_options(
    gateway,
    monkeypatch,
    request_params,
    expected_params,
    expected_options,
):
    service = gateway._client.v1.payment_intents
    mock_create = Mock(return_value=Mock())
    monkeypatch.setattr(service, "create", mock_create)

    gateway._create_payment_intent(
        api_key=gateway.secret_key,
        **request_params,
    )

    mock_create.assert_called_once_with(
        expected_params,
        options=expected_options,
    )


# ---------------------------------------------------------------------------
# Amount / normalization helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount,currency,expected",
    [
        (
            Decimal("100"),
            "usd",
            10000,
        ),
        (
            Decimal("100.50"),
            "usd",
            10050,
        ),
        (
            Decimal("100"),
            "ngn",
            10000,
        ),
        (
            Decimal("5000"),
            "jpy",
            5000,
        ),
        (
            Decimal("5000"),
            " JPY ",
            5000,
        ),
    ],
)
def test_to_smallest_unit(
    gateway,
    amount,
    currency,
    expected,
):
    assert gateway._to_smallest_unit(
        amount,
        currency,
    ) == expected


@pytest.mark.parametrize(
    "amount,currency,expected_message",
    [
        (
            Decimal("0"),
            "usd",
            "Payment amount must be greater than zero",
        ),
        (
            Decimal("-1"),
            "usd",
            "Payment amount must be greater than zero",
        ),
        (
            Decimal("NaN"),
            "usd",
            "Invalid payment amount",
        ),
        (
            Decimal("Infinity"),
            "usd",
            "Invalid payment amount",
        ),
        (
            Decimal("-Infinity"),
            "usd",
            "Invalid payment amount",
        ),
        (
            Decimal("10.001"),
            "usd",
            "Amount has more precision than",
        ),
        (
            Decimal("10.5"),
            "jpy",
            "JPY does not support fractional amounts",
        ),
    ],
)
def test_to_smallest_unit_rejects_invalid_amount(
    gateway,
    amount,
    currency,
    expected_message,
):
    with pytest.raises(
        ValueError,
        match=expected_message,
    ):
        gateway._to_smallest_unit(
            amount,
            currency,
        )


@pytest.mark.parametrize(
    "currency,expected",
    [
        (
            "USD",
            "usd",
        ),
        (
            " usd ",
            "usd",
        ),
        (
            "NGN",
            "ngn",
        ),
        (
            " JPY ",
            "jpy",
        ),
    ],
)
def test_normalize_currency(
    gateway,
    currency,
    expected,
):
    assert gateway._normalize_currency(
        currency
    ) == expected


@pytest.mark.parametrize(
    "currency",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_normalize_currency_rejects_invalid_value(
    gateway,
    currency,
):
    with pytest.raises(
        ValueError,
        match="Currency is required",
    ):
        gateway._normalize_currency(
            currency
        )


@pytest.mark.parametrize(
    "email,expected",
    [
        (
            "user@example.com",
            "user@example.com",
        ),
        (
            " user@example.com ",
            "user@example.com",
        ),
    ],
)
def test_normalize_email(
    gateway,
    email,
    expected,
):
    assert gateway._normalize_email(
        email
    ) == expected


@pytest.mark.parametrize(
    "email",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_normalize_email_rejects_invalid_value(
    gateway,
    email,
):
    with pytest.raises(
        ValueError,
        match="Customer email is required",
    ):
        gateway._normalize_email(
            email
        )


@pytest.mark.parametrize(
    "reference,expected",
    [
        (
            "INV-001",
            "INV-001",
        ),
        (
            " ref_123 ",
            "ref_123",
        ),
        (
            "payment-123",
            "payment-123",
        ),
    ],
)
def test_normalize_reference(
    gateway,
    reference,
    expected,
):
    assert gateway._normalize_reference(
        reference
    ) == expected


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_normalize_reference_rejects_invalid_value(
    gateway,
    reference,
):
    with pytest.raises(
        ValueError,
        match="Payment reference is required",
    ):
        gateway._normalize_reference(
            reference
        )


@pytest.mark.parametrize(
    "amount,currency,expected",
    [
        (
            10000,
            "usd",
            Decimal("100"),
        ),
        (
            12550,
            "NGN",
            Decimal("125.5"),
        ),
        (
            5000,
            "jpy",
            Decimal("5000"),
        ),
    ],
)
def test_from_smallest_unit(
    gateway,
    amount,
    currency,
    expected,
):
    assert gateway._from_smallest_unit(
        amount,
        currency,
    ) == expected


@pytest.mark.parametrize(
    "amount",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "not-a-number",
    ],
)
def test_from_smallest_unit_rejects_invalid_provider_amount(
    gateway,
    amount,
):
    with pytest.raises(
        ValueError,
        match="Invalid provider payment amount",
    ):
        gateway._from_smallest_unit(
            amount,
            "usd",
        )


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def test_initialize_payment_success(
    gateway,
    monkeypatch,
):
    intent = Mock()
    intent.id = "pi_123"
    intent.status = "requires_payment_method"
    intent.client_secret = "pi_secret_123"

    mock_create = Mock(
        return_value=intent
    )

    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        mock_create,
    )

    result = gateway.initialize_payment(
        reference="INV-001",
        amount=Decimal("125.50"),
        currency="NGN",
        customer_email=(
            " patient@example.com "
        ),
        callback_url=(
            "https://example.com/callback"
        ),
        metadata={
            "clinic_id": 10,
            "invoice_id": 25,
        },
    )

    assert result == {
        "provider": "stripe",
        "reference": "INV-001",
        "transaction_id": "pi_123",
        "status": "requires_payment_method",
        "amount": Decimal("125.50"),
        "currency": "NGN",
        "client_secret": "pi_secret_123",
    }

    mock_create.assert_called_once()

    _, kwargs = mock_create.call_args

    assert kwargs == {
        "api_key": "sk_test_secret",
        "amount": 12550,
        "currency": "ngn",
        "automatic_payment_methods": {
            "enabled": True,
        },
        "receipt_email": "patient@example.com",
        "metadata": {
            "reference": "INV-001",
            "clinic_id": "10",
            "invoice_id": "25",
            "callback_url": (
                "https://example.com/callback"
            ),
        },
    }


def test_initialize_payment_without_optional_fields(
    gateway,
    monkeypatch,
):
    intent = Mock()
    intent.id = "pi_456"
    intent.status = "requires_confirmation"
    intent.client_secret = "secret_456"

    mock_create = Mock(
        return_value=intent
    )

    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        mock_create,
    )

    result = gateway.initialize_payment(
        reference="REF-123",
        amount=Decimal("50"),
        currency="USD",
        customer_email="user@example.com",
    )

    assert result["provider"] == "stripe"
    assert result["reference"] == "REF-123"
    assert result["transaction_id"] == "pi_456"
    assert result["amount"] == Decimal("50")
    assert result["currency"] == "USD"

    _, kwargs = mock_create.call_args

    assert kwargs["api_key"] == "sk_test_secret"

    assert kwargs["metadata"] == {
        "reference": "REF-123",
    }


def test_initialize_payment_converts_metadata_values_to_strings(
    gateway,
    monkeypatch,
):
    intent = Mock()
    intent.id = "pi_789"
    intent.status = "requires_payment_method"
    intent.client_secret = "secret_789"

    mock_create = Mock(
        return_value=intent
    )

    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        mock_create,
    )

    gateway.initialize_payment(
        reference="REF-1",
        amount=Decimal("100"),
        currency="USD",
        customer_email="user@example.com",
        metadata={
            1: 25,
            "boolean": True,
            "none": None,
        },
    )

    _, kwargs = mock_create.call_args

    assert kwargs["api_key"] == "sk_test_secret"

    assert kwargs["metadata"] == {
        "reference": "REF-1",
        "1": "25",
        "boolean": "True",
        "none": "None",
    }


def test_initialize_payment_passes_idempotency_key_to_stripe(
    gateway,
    monkeypatch,
):
    intent = Mock()
    intent.id = "pi_idempotent"
    intent.status = "requires_payment_method"
    intent.client_secret = "secret_idempotent"

    mock_create = Mock(
        return_value=intent
    )

    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        mock_create,
    )

    result = gateway.initialize_payment(
        reference="REF-IDEMPOTENT",
        amount=Decimal("100"),
        currency="USD",
        customer_email="user@example.com",
        idempotency_key=" billing-payment-001 ",
    )

    assert result["transaction_id"] == "pi_idempotent"

    _, kwargs = mock_create.call_args

    assert kwargs["idempotency_key"] == (
        "billing-payment-001"
    )


@pytest.mark.parametrize(
    "idempotency_key,expected_message",
    [
        (
            "   ",
            "Idempotency-Key cannot be blank",
        ),
        (
            "x" * 256,
            "Idempotency-Key cannot exceed 255 characters",
        ),
    ],
)
def test_initialize_payment_rejects_invalid_idempotency_key(
    gateway,
    monkeypatch,
    idempotency_key,
    expected_message,
):
    mock_create = Mock()

    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        mock_create,
    )

    with pytest.raises(
        ValueError,
        match=expected_message,
    ):
        gateway.initialize_payment(
            reference="REF-INVALID-IDEMPOTENCY",
            amount=Decimal("100"),
            currency="USD",
            customer_email="user@example.com",
            idempotency_key=idempotency_key,
        )

    mock_create.assert_not_called()

def test_initialize_payment_handles_stripe_error(
    gateway,
    monkeypatch,
):
    mock_create = Mock(
        side_effect=stripe.StripeError(
            "card setup failed"
        )
    )

    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        mock_create,
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "Stripe payment initialization failed"
        ),
    ):
        gateway.initialize_payment(
            reference="REF-1",
            amount=Decimal("100"),
            currency="USD",
            customer_email="user@example.com",
        )


def test_initialize_payment_rejects_invalid_payment_intent(
    gateway,
    monkeypatch,
):
    intent = Mock()
    intent.id = None

    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        Mock(return_value=intent),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "Stripe returned an invalid PaymentIntent"
        ),
    ):
        gateway.initialize_payment(
            reference="REF-1",
            amount=Decimal("100"),
            currency="USD",
            customer_email="user@example.com",
        )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_verify_payment_success(
    gateway,
    monkeypatch,
):
    intent = Mock()
    intent.id = "pi_123"
    intent.status = "succeeded"
    intent.currency = "ngn"
    intent.amount = 12550
    intent.metadata = {
        "reference": "INV-001",
    }

    mock_retrieve = Mock(
        return_value=intent
    )

    monkeypatch.setattr(
        gateway,
        "_retrieve_payment_intent",
        mock_retrieve,
    )

    result = gateway.verify_payment(
        reference="pi_123"
    )

    assert result == {
        "provider": "stripe",
        "reference": "INV-001",
        "transaction_id": "pi_123",
        "status": "succeeded",
        "amount": Decimal("125.5"),
        "currency": "NGN",
        "paid": True,
    }

    mock_retrieve.assert_called_once_with(
        "pi_123",
        api_key="sk_test_secret",
    )


def test_verify_payment_uses_input_reference_when_metadata_missing(
    gateway,
    monkeypatch,
):
    intent = Mock()
    intent.id = "pi_123"
    intent.status = "processing"
    intent.currency = "usd"
    intent.amount = 5000
    intent.metadata = {}

    monkeypatch.setattr(
        gateway,
        "_retrieve_payment_intent",
        Mock(return_value=intent),
    )

    result = gateway.verify_payment(
        reference="pi_123"
    )

    assert result["reference"] == "pi_123"
    assert result["amount"] == Decimal("50")
    assert result["paid"] is False


def test_verify_payment_handles_stripe_error(
    gateway,
    monkeypatch,
):
    monkeypatch.setattr(
        gateway,
        "_retrieve_payment_intent",
        Mock(
            side_effect=stripe.StripeError(
                "payment intent not found"
            )
        ),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "Stripe payment verification failed"
        ),
    ):
        gateway.verify_payment(
            reference="pi_missing"
        )


def test_verify_payment_rejects_invalid_payment_intent(
    gateway,
    monkeypatch,
):
    intent = Mock()
    intent.id = None

    monkeypatch.setattr(
        gateway,
        "_retrieve_payment_intent",
        Mock(return_value=intent),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "Stripe returned an invalid PaymentIntent"
        ),
    ):
        gateway.verify_payment(
            reference="pi_invalid"
        )


@pytest.mark.parametrize(
    "status,paid",
    [
        ("succeeded", True),
        ("processing", False),
        ("requires_action", False),
        ("requires_payment_method", False),
        ("canceled", False),
    ],
)
def test_verify_payment_normalizes_paid_status(
    gateway,
    monkeypatch,
    status,
    paid,
):
    intent = Mock()
    intent.id = "pi_test"
    intent.status = status
    intent.currency = "usd"
    intent.amount = 10000
    intent.metadata = {}

    monkeypatch.setattr(
        gateway,
        "_retrieve_payment_intent",
        Mock(return_value=intent),
    )

    result = gateway.verify_payment(
        reference="pi_test"
    )

    assert result["status"] == status
    assert result["paid"] is paid
    assert result["amount"] == Decimal("100")


# ---------------------------------------------------------------------------
# Zero-decimal currencies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "currency",
    [
        "jpy",
        "clp",
        "krw",
        "vnd",
        "xaf",
    ],
)
def test_zero_decimal_currency_uses_whole_units(
    gateway,
    currency,
):
    assert gateway._to_smallest_unit(
        Decimal("5000"),
        currency,
    ) == 5000


@pytest.mark.parametrize(
    "currency",
    [
        "jpy",
        "clp",
        "krw",
        "vnd",
        "xaf",
    ],
)
def test_zero_decimal_currency_rejects_fractional_amount(
    gateway,
    currency,
):
    with pytest.raises(
        ValueError,
        match=(
            f"{currency.upper()} does not support "
            "fractional amounts"
        ),
    ):
        gateway._to_smallest_unit(
            Decimal("5000.50"),
            currency,
        )
# ---------------------------------------------------------------------------
# Phase 9 - Gate 4 / Slice 2A
# Provider outcome classification
# ---------------------------------------------------------------------------


def test_initialize_payment_stripe_error_without_http_status_is_unknown_outcome(
    gateway,
    monkeypatch,
):
    error = stripe.StripeError(
        "provider connection failed"
    )

    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        Mock(side_effect=error),
    )

    with pytest.raises(
        GatewayUnknownOutcomeError,
        match="Stripe payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="REF-UNKNOWN-001",
            amount=Decimal("100"),
            currency="USD",
            customer_email="user@example.com",
        )


def test_initialize_payment_stripe_client_error_is_known_failure(
    gateway,
    monkeypatch,
):
    error = stripe.StripeError(
        "invalid request"
    )
    error.http_status = 400

    monkeypatch.setattr(
        gateway,
        "_create_payment_intent",
        Mock(side_effect=error),
    )

    with pytest.raises(
        GatewayRejectedError,
        match="Stripe payment initialization failed",
    ):
        gateway.initialize_payment(
            reference="REF-REJECTED-001",
            amount=Decimal("100"),
            currency="USD",
            customer_email="user@example.com",
        )
