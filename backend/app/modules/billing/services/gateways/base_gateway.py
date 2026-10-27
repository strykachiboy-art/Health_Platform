from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any


class GatewayRejectedError(RuntimeError):
    """The payment provider explicitly rejected the request."""


class GatewayUnknownOutcomeError(RuntimeError):
    """The payment provider outcome cannot be known safely."""


class GatewayTransientError(RuntimeError):
    """The payment provider is temporarily unavailable for a safe read."""


def is_transient_gateway_http_status(
    status_code: Any,
) -> bool:
    return (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and (
            status_code in {408, 425, 429}
            or status_code >= 500
        )
    )


class PaymentGatewayBase(ABC):
    @abstractmethod
    def initialize_payment(
        self,
        *,
        reference: str,
        amount: Decimal,
        currency: str,
        customer_email: str,
        callback_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def verify_payment(
        self,
        *,
        reference: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def handle_webhook(
        self,
        *,
        payload: bytes,
        signature: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError
