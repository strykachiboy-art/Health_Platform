from __future__ import annotations

import base64
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

class FlutterwaveGateway(PaymentGatewayBase):
    BASE_URL = "https://api.flutterwave.com/v3"

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
    ):
        if not isinstance(credentials, dict):
            raise ValueError(
                "Flutterwave credentials are invalid"
            )

        self.secret_key = credentials.get(
            "secret_key"
        )

        self.webhook_secret = credentials.get(
            "webhook_secret"
        )

        if (
            not isinstance(self.secret_key, str)
            or not self.secret_key.strip()
        ):
            raise ValueError(
                "Flutterwave secret key is not configured"
            )

        if (
            not isinstance(
                self.webhook_secret,
                str,
            )
            or not self.webhook_secret.strip()
        ):
            raise ValueError(
                "Flutterwave webhook secret is not configured"
            )

        self.secret_key = self.secret_key.strip()
        self.webhook_secret = (
            self.webhook_secret.strip()
        )

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

        return reference.strip()

    @staticmethod
    def _normalize_amount(
        amount: Decimal,
    ) -> Decimal:
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

        return amount

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
        amount = self._normalize_amount(
            amount
        )
        currency = self._normalize_currency(
            currency
        )
        customer_email = self._normalize_email(
            customer_email
        )

        payload: dict[str, Any] = {
            "tx_ref": reference,
            "amount": str(amount),
            "currency": currency,
            "customer": {
                "email": customer_email,
            },
        }

        if callback_url:
            payload["redirect_url"] = callback_url

        if metadata:
            payload["meta"] = [
                {
                    "metaname": str(key),
                    "metavalue": str(value),
                }
                for key, value in metadata.items()
            ]

        try:
            response = requests.post(
                f"{self.BASE_URL}/payments",
                headers=self._headers(),
                json=payload,
                timeout=30,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GatewayUnknownOutcomeError(
                "Flutterwave payment initialization "
                f"failed: {exc}"
            ) from exc

        if _is_redirect_response(response):
            raise GatewayUnknownOutcomeError(
                "Flutterwave payment initialization returned an unexpected redirect"
            )

        try:
            response_data = response.json()
        except ValueError as exc:
            raise GatewayUnknownOutcomeError(
                "Flutterwave returned an invalid response"
            ) from exc

        if (
            not response.ok
            or response_data.get("status")
            != "success"
        ):
            message = response_data.get(
                "message",
                "Flutterwave payment initialization failed",
            )

            raise GatewayRejectedError(
                "Flutterwave payment initialization "
                f"failed: {message}"
            )

        data = response_data.get("data") or {}

        return {
            "provider": "flutterwave",
            "reference": reference,
            "transaction_id": (
                str(data["id"])
                if data.get("id") is not None
                else None
            ),
            "status": "initialized",
            "amount": amount,
            "currency": currency,
            "payment_link": data.get("link"),
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
            transaction_id = int(reference)
        except ValueError as exc:
            raise ValueError(
                "Flutterwave verification requires "
                "a transaction ID"
            ) from exc

        if transaction_id <= 0:
            raise ValueError(
                "Flutterwave transaction ID must be positive"
            )

        try:
            response = requests.get(
                f"{self.BASE_URL}/transactions/"
                f"{transaction_id}/verify",
                headers=self._headers(),
                timeout=30,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                "Flutterwave payment verification "
                f"failed: {exc}"
            ) from exc

        if _is_redirect_response(response):
            raise RuntimeError(
                "Flutterwave payment verification returned an unexpected redirect"
            )

        try:
            response_data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Flutterwave returned an invalid "
                "verification response"
            ) from exc

        if (
            not response.ok
            or response_data.get("status")
            != "success"
        ):
            message = response_data.get(
                "message",
                "Flutterwave payment verification failed",
            )

            raise RuntimeError(
                "Flutterwave payment verification "
                f"failed: {message}"
            )

        data = response_data.get("data") or {}

        transaction_status = data.get(
            "status"
        )

        return {
            "provider": "flutterwave",
            "reference": data.get("tx_ref"),
            "transaction_id": (
                str(data["id"])
                if data.get("id") is not None
                else None
            ),
            "status": transaction_status,
            "amount": data.get("amount"),
            "charged_amount": data.get(
                "charged_amount"
            ),
            "currency": (
                data.get("currency") or ""
            ).upper(),
            "paid": (
                transaction_status
                == "successful"
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
                    "flutterwave-signature"
                )
                or headers.get(
                    "Flutterwave-Signature"
                )
            )

        if not webhook_signature:
            raise ValueError(
                "Flutterwave webhook signature "
                "is required"
            )

        expected_signature = base64.b64encode(
            hmac.new(
                self.webhook_secret.encode("utf-8"),
                payload,
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        if not hmac.compare_digest(
            expected_signature,
            webhook_signature.strip(),
        ):
            raise ValueError(
                "Invalid Flutterwave webhook signature"
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
                "Invalid Flutterwave webhook payload"
            ) from exc

        if not isinstance(event, dict):
            raise ValueError(
                "Invalid Flutterwave webhook payload"
            )

        event_type = (
            event.get("event")
            or event.get("type")
        )

        if not event_type:
            raise ValueError(
                "Flutterwave webhook event type "
                "is missing"
            )

        event_data = event.get("data") or {}

        if not isinstance(event_data, dict):
            raise ValueError(
                "Invalid Flutterwave webhook data"
            )

        transaction_id = event_data.get("id")

        normalized = {
            "provider": "flutterwave",
            "event_id": (
                str(event["id"])
                if event.get("id") is not None
                else (
                    str(transaction_id)
                    if transaction_id is not None
                    else hashlib.sha256(
                        payload
                    ).hexdigest()
                )
            ),
            "event_type": event_type,
            "transaction_id": (
                str(transaction_id)
                if transaction_id is not None
                else None
            ),
            "reference": event_data.get(
                "tx_ref"
            ),
            "amount": event_data.get(
                "amount"
            ),
            "currency": (
                event_data.get("currency")
                or ""
            ).upper(),
        }

        transaction_status = event_data.get(
            "status"
        )

        if (
            event_type == "charge.completed"
            and transaction_status
            == "successful"
        ):
            normalized["status"] = (
                "successful"
            )

        elif transaction_status in {
            "failed",
            "cancelled",
            "canceled",
        }:
            normalized["status"] = "failed"
            normalized["failure_reason"] = (
                event_data.get(
                    "processor_response"
                )
                or event_data.get("message")
                or "Flutterwave payment failed"
            )

        else:
            normalized["status"] = (
                transaction_status
            )

        return normalized