from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.modules.billing.services.gateways.base_gateway import (
    GatewayUnknownOutcomeError,
)
from app.modules.billing.services.gateways.flutterwave_gateway import (
    FlutterwaveGateway,
)


@pytest.fixture
def gateway():
    return FlutterwaveGateway(
        credentials={
            "secret_key": "flw_test_redirect_guard",
            "webhook_secret": "flw_test_webhook_secret",
        },
    )


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
def test_initialize_payment_does_not_follow_redirects(
    gateway,
    monkeypatch,
    status_code,
):
    response = Mock()
    response.status_code = status_code
    response.ok = True

    mock_post = Mock(return_value=response)
    monkeypatch.setattr(
        "app.modules.billing.services.gateways.flutterwave_gateway.requests.post",
        mock_post,
    )

    with pytest.raises(
        GatewayUnknownOutcomeError,
        match="unexpected redirect",
    ):
        gateway.initialize_payment(
            reference="FLW-REDIRECT-001",
            amount=Decimal("100"),
            currency="NGN",
            customer_email="patient@example.com",
        )

    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["allow_redirects"] is False
    response.json.assert_not_called()


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
def test_verify_payment_does_not_follow_redirects(
    gateway,
    monkeypatch,
    status_code,
):
    response = Mock()
    response.status_code = status_code
    response.ok = True

    mock_get = Mock(return_value=response)
    monkeypatch.setattr(
        "app.modules.billing.services.gateways.flutterwave_gateway.requests.get",
        mock_get,
    )

    with pytest.raises(
        RuntimeError,
        match="unexpected redirect",
    ):
        gateway.verify_payment(reference="1001")

    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs["allow_redirects"] is False
    response.json.assert_not_called()