from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from app.modules.billing.services.gateways.base_gateway import (
    GatewayRejectedError,
    GatewayUnknownOutcomeError,
    PaymentGatewayBase,
)


def _is_redirect_response(response: requests.Response) -> bool:
    status_code = getattr(response, "status_code", None)

    return (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 300 <= status_code < 400
    )

class PaystackGateway(PaymentGatewayBase):
    BASE_URL = "https://api.paystack.co"

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
    ):
        if not isinstance(credentials, dict):
            raise ValueError(
                "Paystack credentials are invalid"
            )

        self.secret_key = credentials.get(
            "secret_key"
        )

        if (
            not isinstance(self.secret_key, str)
            or not self.secret_key.strip()
        ):
            raise ValueError(
                "Paystack secret key is not configured"
            )

        self.secret_key = self.secret_key.strip()

    @staticmethod
    def _to_smallest_unit(
        amount: Decimal,
    ) -> int:
        try:
            amount = Decimal(str(amount))
        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "Invalid payment amount"
            ) from exc

        if not amount.is_finite():
            raise ValueError(
                "Invalid payment amount"
            )

        if amount <= 0:
            raise ValueError(
                "Payment amount must be greater than zero"
            )

        smallest_unit = amount * Decimal("100")

        if (
            smallest_unit
            != smallest_unit.to_integral_value()
        ):
            raise ValueError(
                "Amount has more precision than "
                "the currency supports"
            )

        return int(smallest_unit)

    @staticmethod
    def _from_smallest_unit(amount: Any) -> Decimal | None:
        """Normalize Paystack minor-unit amounts to major currency units."""
        if amount is None:
            return None

        try:
            amount = Decimal(str(amount))
        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "Invalid Paystack provider amount"
            ) from exc

        if (
            not amount.is_finite()
            or amount < 0
            or amount != amount.to_integral_value()
        ):
            raise ValueError(
                "Invalid Paystack provider amount"
            )

        return amount / Decimal("100")

    @staticmethod
    def _normalize_currency(
        currency: str,
    ) -> str:
        if (
            not isinstance(currency, str)
            or not currency.strip()
        ):
            raise ValueError(
                "Currency is required"
            )

        return currency.strip().upper()

    @staticmethod
    def _normalize_optional_currency(currency: Any) -> str:
        """Normalize optional webhook currency without accepting malformed types."""
        if currency is None:
            return ""
        if not isinstance(currency, str):
            raise ValueError("Currency is invalid")
        if not currency.strip():
            return ""
        return PaystackGateway._normalize_currency(currency)

    @staticmethod
    def _normalize_email(
        email: str,
    ) -> str:
        if (
            not isinstance(email, str)
            or not email.strip()
        ):
            raise ValueError(
                "Customer email is required"
            )

        return email.strip()

    @staticmethod
    def _normalize_reference(
        reference: str,
    ) -> str:
        if (
            not isinstance(reference, str)
            or not reference.strip()
        ):
            raise ValueError(
                "Payment reference is required"
            )

        reference = reference.strip()

        allowed_characters = set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789"
            "-._="
        )

        if any(
            character not in allowed_characters
            for character in reference
        ):
            raise ValueError(
                "Payment reference contains "
                "unsupported characters"
            )

        return reference

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                f"Bearer {self.secret_key}"
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

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
        reference = self._normalize_reference(
            reference
        )

        amount = Decimal(str(amount))

        currency = self._normalize_currency(
            currency
        )

        customer_email = self._normalize_email(
            customer_email
        )

        amount_in_smallest_unit = (
            self._to_smallest_unit(amount)
        )

        payment_metadata: dict[str, Any] = {
            "reference": reference,
        }

        if metadata:
            payment_metadata.update(
                metadata
            )

        payload: dict[str, Any] = {
            "email": customer_email,
            "amount": str(
                amount_in_smallest_unit
            ),
            "currency": currency,
            "reference": reference,
            "metadata": json.dumps(
                payment_metadata
            ),
        }

        if callback_url:
            payload["callback_url"] = callback_url

        try:
            response = requests.post(
                f"{self.BASE_URL}/transaction/initialize",
                headers=self._headers(),
                json=payload,
                timeout=30,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GatewayUnknownOutcomeError(
                "Paystack payment initialization "
                f"failed: {exc}"
            ) from exc

        if _is_redirect_response(response):
            raise GatewayUnknownOutcomeError(
                "Paystack payment initialization returned an unexpected redirect"
            )

        try:
            response_data = response.json()
        except ValueError as exc:
            raise GatewayUnknownOutcomeError(
                "Paystack returned an invalid "
                "response"
            ) from exc

        if (
            not response.ok
            or response_data.get("status")
            is not True
        ):
            message = response_data.get(
                "message",
                "Paystack payment initialization failed",
            )

            raise GatewayRejectedError(
                "Paystack payment initialization "
                f"failed: {message}"
            )

        data = response_data.get("data") or {}

        return {
            "provider": "paystack",
            "reference": data.get(
                "reference",
                reference,
            ),
            "transaction_id": None,
            "status": "initialized",
            "amount": amount,
            "currency": currency,
            "authorization_url": data.get(
                "authorization_url"
            ),
            "access_code": data.get(
                "access_code"
            ),
        }

    def verify_payment(
        self,
        *,
        reference: str,
    ) -> dict[str, Any]:
        reference = self._normalize_reference(
            reference
        )

        try:
            response = requests.get(
                f"{self.BASE_URL}/transaction/"
                f"verify/{reference}",
                headers=self._headers(),
                timeout=30,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                "Paystack payment verification "
                f"failed: {exc}"
            ) from exc

        if _is_redirect_response(response):
            raise RuntimeError(
                "Paystack payment verification returned an unexpected redirect"
            )

        try:
            response_data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Paystack returned an invalid "
                "verification response"
            ) from exc

        if (
            not response.ok
            or response_data.get("status")
            is not True
        ):
            message = response_data.get(
                "message",
                "Paystack payment verification failed",
            )

            raise RuntimeError(
                "Paystack payment verification "
                f"failed: {message}"
            )

        data = response_data.get("data") or {}

        transaction_status = data.get(
            "status"
        )

        return {
            "provider": "paystack",
            "reference": data.get(
                "reference",
                reference,
            ),
            "transaction_id": (
                str(data["id"])
                if data.get("id") is not None
                else None
            ),
            "status": transaction_status,
            "amount": self._from_smallest_unit(data.get("amount")),
            "currency": self._normalize_currency(data.get("currency")),

            "paid": (
                transaction_status == "success"
            ),
        }

    def handle_webhook(
        self,
        *,
        payload: bytes,
        signature: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not payload:
            raise ValueError(
                "Webhook payload is required"
            )

        webhook_signature = signature

        if (
            not webhook_signature
            and headers
        ):
            webhook_signature = (
                headers.get(
                    "x-paystack-signature"
                )
                or headers.get(
                    "X-Paystack-Signature"
                )
            )

        if not webhook_signature:
            raise ValueError(
                "Paystack webhook signature "
                "is required"
            )

        expected_signature = hmac.new(
            self.secret_key.encode("utf-8"),
            payload,
            hashlib.sha512,
        ).hexdigest()

        if not hmac.compare_digest(
            expected_signature,
            webhook_signature.strip(),
        ):
            raise ValueError(
                "Invalid Paystack webhook signature"
            )

        try:
            event = json.loads(
                payload.decode("utf-8")
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "Invalid Paystack webhook payload"
            ) from exc

        if not isinstance(event, dict):
            raise ValueError(
                "Invalid Paystack webhook payload"
            )

        event_type = event.get("event")

        if not event_type:
            raise ValueError(
                "Paystack webhook event type "
                "is missing"
            )

        event_data = event.get("data") or {}

        if not isinstance(event_data, dict):
            raise ValueError(
                "Invalid Paystack webhook data"
            )

        transaction_id = event_data.get("id")

        normalized: dict[str, Any] = {
            "provider": "paystack",
            "event_id": (
                str(transaction_id)
                if transaction_id is not None
                else hashlib.sha256(
                    payload
                ).hexdigest()
            ),
            "event_type": event_type,
            "livemode": (
                event_data.get("domain")
                == "live"
            ),
            "transaction_id": (
                str(transaction_id)
                if transaction_id is not None
                else None
            ),
            "reference": event_data.get(
                "reference"
            ),
            "amount": self._from_smallest_unit(
                event_data.get("amount")
            ),
            "currency": self._normalize_optional_currency(event_data.get("currency")),

        }

        transaction_status = (
            event_data.get("status")
        )

        if event_type == "charge.success":
            normalized["status"] = (
                "successful"
            )

        elif transaction_status in {
            "failed",
            "abandoned",
            "reversed",
        }:
            normalized["status"] = "failed"
            normalized["failure_reason"] = (
                event_data.get(
                    "gateway_response"
                )
                or event_data.get("message")
                or "Paystack payment failed"
            )

        else:
            normalized["status"] = (
                transaction_status
            )

        return normalized