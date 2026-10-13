from decimal import Decimal
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.billing_enums import PaymentGateway
from app.core.exceptions import ConflictError
from app.modules.billing.services import (
    payment_orchestration_service as orchestration,
)
from app.modules.billing.services import (
    payment_webhook_service as webhooks,
)
from app.modules.billing.services.gateways.paystack_gateway import (
    PaystackGateway,
)


def _payment(*, currency="NGN"):
    return SimpleNamespace(
        id=1,
        invoice_id=10,
        amount=Decimal("100.00"),
        gateway=PaymentGateway.PAYSTACK,
        currency=currency,
        reference="PAY-1",
        gateway_transaction_id="123",
    )


def _provider_result(*, amount=Decimal("100.00"), currency="NGN"):
    return {
        "provider": "paystack",
        "reference": "PAY-1",
        "transaction_id": "123",
        "status": "success",
        "paid": True,
        "amount": amount,
        "currency": currency,
    }


def _validate_webhook(*, amount=Decimal("100.00"), currency="NGN"):
    return webhooks._validate_payment_match(
        payment=_payment(),
        gateway=PaymentGateway.PAYSTACK,
        reference="PAY-1",
        transaction_id="123",
        amount=amount,
        currency=currency,
        status="successful",
    )


def test_provider_result_uses_one_canonical_amount_unit():
    payment = _payment()

    accepted = orchestration._validate_provider_result(
        payment,
        _provider_result(amount=Decimal("100.00")),
    )
    assert accepted["amount"] == Decimal("100.00")

    with pytest.raises(ConflictError, match="amount"):
        orchestration._validate_provider_result(
            payment,
            _provider_result(amount=10000),
        )


def test_successful_provider_result_requires_amount():
    with pytest.raises(ConflictError, match="amount"):
        orchestration._validate_provider_result(
            _payment(),
            _provider_result(amount=None),
        )


@pytest.mark.parametrize("currency", ["USD", "", None])
def test_successful_provider_result_requires_expected_currency(currency):
    with pytest.raises(ConflictError, match="currency"):
        orchestration._validate_provider_result(
            _payment(),
            _provider_result(currency=currency),
        )


def test_legacy_gateway_payment_without_currency_fails_closed():
    with pytest.raises(ConflictError, match="currency"):
        orchestration._validate_provider_result(
            _payment(currency=None),
            _provider_result(),
        )


def test_webhook_match_accepts_canonical_amount_and_expected_currency():
    assert _validate_webhook() is None


def test_webhook_match_rejects_paystack_minor_units_at_service_boundary():
    with pytest.raises(ConflictError, match="amount"):
        _validate_webhook(amount=10000)


@pytest.mark.parametrize(
    ("amount", "currency"),
    [
        (None, "NGN"),
        (Decimal("100.00"), "USD"),
        (Decimal("100.00"), ""),
        (Decimal("100.00"), None),
    ],
)
def test_successful_webhook_requires_exact_amount_and_currency(
    amount,
    currency,
):
    with pytest.raises(ConflictError):
        _validate_webhook(amount=amount, currency=currency)


def test_paystack_verification_normalizes_minor_units(monkeypatch):
    response = Mock()
    response.ok = True
    response.json.return_value = {
        "status": True,
        "data": {
            "id": 123,
            "reference": "PAY-1",
            "status": "success",
            "amount": 10000,
            "currency": "NGN",
        },
    }
    mock_get = Mock(return_value=response)
    monkeypatch.setattr(
        "app.modules.billing.services.gateways.paystack_gateway.requests.get",
        mock_get,
    )

    gateway = PaystackGateway(
        credentials={"secret_key": "sk_test_secret"}
    )
    result = gateway.verify_payment(reference="PAY-1")

    assert result["amount"] == Decimal("100.00")
    assert result["currency"] == "NGN"


@pytest.mark.parametrize("currency", [None, 123, [], "   "])
def test_paystack_verification_rejects_missing_or_non_string_currency(
    monkeypatch,
    currency,
):
    response = Mock()
    response.ok = True
    response.json.return_value = {
        "status": True,
        "data": {
            "id": 123,
            "reference": "PAY-1",
            "status": "success",
            "amount": 10000,
            "currency": currency,
        },
    }
    monkeypatch.setattr(
        "app.modules.billing.services.gateways.paystack_gateway.requests.get",
        Mock(return_value=response),
    )

    gateway = PaystackGateway(
        credentials={"secret_key": "sk_test_secret"}
    )
    with pytest.raises(ValueError, match="Currency is required"):
        gateway.verify_payment(reference="PAY-1")

def test_paystack_webhook_normalizes_minor_units():

    secret = "sk_test_secret"
    gateway = PaystackGateway(
        credentials={"secret_key": secret}
    )

    payload = json.dumps(
        {
            "event": "charge.success",
            "data": {
                "id": 123,
                "reference": "PAY-1",
                "status": "success",
                "amount": 10000,
                "currency": "NGN",
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")

    signature = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha512,
    ).hexdigest()

    result = gateway.handle_webhook(
        payload=payload,
        signature=signature,
    )

    assert result["amount"] == Decimal("100.00")
    assert result["currency"] == "NGN"