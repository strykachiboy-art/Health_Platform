from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import stripe

from app.modules.billing.services.gateways.base_gateway import (
    GatewayRejectedError,
    GatewayUnknownOutcomeError,
    PaymentGatewayBase,
)


STRIPE_HTTP_TIMEOUT_SECONDS = 30
STRIPE_MAX_NETWORK_RETRIES = 0


def _build_stripe_http_client():
    return stripe.RequestsClient(
        timeout=STRIPE_HTTP_TIMEOUT_SECONDS,
    )


_STRIPE_HTTP_CLIENT = _build_stripe_http_client()


class StripeGateway(PaymentGatewayBase):
    ZERO_DECIMAL_CURRENCIES = {
        "bif",
        "clp",
        "djf",
        "gnf",
        "jpy",
        "kmf",
        "krw",
        "mga",
        "pyg",
        "rwf",
        "ugx",
        "vnd",
        "vuv",
        "xaf",
        "xof",
        "xpf",
    }

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
    ):
        if not isinstance(credentials, dict):
            raise ValueError(
                "Stripe credentials are invalid"
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
                "Stripe secret key is not configured"
            )

        if (
            not isinstance(
                self.webhook_secret,
                str,
            )
            or not self.webhook_secret.strip()
        ):
            raise ValueError(
                "Stripe webhook secret is not configured"
            )

        self.secret_key = self.secret_key.strip()
        self.webhook_secret = (
            self.webhook_secret.strip()
        )

        self._client = stripe.StripeClient(
            api_key=self.secret_key,
            http_client=_STRIPE_HTTP_CLIENT,
            max_network_retries=STRIPE_MAX_NETWORK_RETRIES,
        )

    def _create_payment_intent(self, **request_params: Any):
        api_key = request_params.pop("api_key", None)
        if api_key != self.secret_key:
            raise ValueError("Stripe API key does not match configured client")

        idempotency_key = request_params.pop("idempotency_key", None)
        options: dict[str, str] = {}
        if idempotency_key is not None:
            options["idempotency_key"] = idempotency_key

        return self._client.v1.payment_intents.create(
            request_params,
            options=options,
        )

    def _retrieve_payment_intent(
        self,
        reference: str,
        *,
        api_key: str,
    ):
        if api_key != self.secret_key:
            raise ValueError("Stripe API key does not match configured client")

        return self._client.v1.payment_intents.retrieve(reference)

    @classmethod
    def _to_smallest_unit(
        cls,
        amount: Decimal,
        currency: str,
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

        currency = cls._normalize_currency(
            currency
        )

        if currency in cls.ZERO_DECIMAL_CURRENCIES:
            if (
                amount
                != amount.to_integral_value()
            ):
                raise ValueError(
                    f"{currency.upper()} does not support "
                    "fractional amounts"
                )

            return int(amount)

        smallest_unit = (
            amount * Decimal("100")
        )

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

        return currency.strip().lower()

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
    def _normalize_idempotency_key(
        idempotency_key: str | None,
    ) -> str | None:
        if idempotency_key is None:
            return None

        if not isinstance(
            idempotency_key,
            str,
        ):
            raise ValueError(
                "Idempotency-Key must be a string"
            )

        idempotency_key = idempotency_key.strip()

        if not idempotency_key:
            raise ValueError(
                "Idempotency-Key cannot be blank"
            )

        if len(idempotency_key) > 255:
            raise ValueError(
                "Idempotency-Key cannot exceed 255 characters"
            )

        return idempotency_key

    @staticmethod
    def _from_smallest_unit(
        amount,
        currency: str,
    ) -> Decimal:
        try:
            amount = Decimal(str(amount))
        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "Invalid provider payment amount"
            ) from exc

        if not amount.is_finite():
            raise ValueError(
                "Invalid provider payment amount"
            )

        currency = currency.lower()

        if currency in StripeGateway.ZERO_DECIMAL_CURRENCIES:
            return amount

        return amount / Decimal("100")

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

        idempotency_key = (
            self._normalize_idempotency_key(
                idempotency_key
            )
        )

        currency = self._normalize_currency(
            currency
        )

        customer_email = self._normalize_email(
            customer_email
        )

        amount = Decimal(str(amount))

        amount_in_smallest_unit = (
            self._to_smallest_unit(
                amount,
                currency,
            )
        )

        payment_metadata = {
            "reference": reference,
        }

        if metadata:
            payment_metadata.update(
                {
                    str(key): str(value)
                    for key, value in metadata.items()
                }
            )

        if callback_url:
            payment_metadata["callback_url"] = (
                callback_url
            )

        params: dict[str, Any] = {
            "amount": amount_in_smallest_unit,
            "currency": currency,
            "automatic_payment_methods": {
                "enabled": True,
            },
            "receipt_email": customer_email,
            "metadata": payment_metadata,
        }

        if idempotency_key is not None:
            params["idempotency_key"] = (
                idempotency_key
            )

        try:
            intent = self._create_payment_intent(
                **params,
                api_key=self.secret_key,
            )
        except stripe.StripeError as exc:
            http_status = getattr(
                exc,
                "http_status",
                None,
            )

            if (
                isinstance(
                    http_status,
                    int,
                )
                and 400 <= http_status < 500
                and http_status != 429
            ):
                raise GatewayRejectedError(
                    "Stripe payment initialization "
                    f"failed: {exc}"
                ) from exc

            raise GatewayUnknownOutcomeError(
                "Stripe payment initialization "
                f"failed: {exc}"
            ) from exc

        if not intent or not getattr(
            intent,
            "id",
            None,
        ):
            raise RuntimeError(
                "Stripe returned an invalid "
                "PaymentIntent"
            )

        return {
            "provider": "stripe",
            "reference": reference,
            "transaction_id": intent.id,
            "status": intent.status,
            "amount": amount,
            "currency": currency.upper(),
            "client_secret": getattr(
                intent,
                "client_secret",
                None,
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
            intent = self._retrieve_payment_intent(
                reference,
                api_key=self.secret_key,
            )
        except stripe.StripeError as exc:
            raise RuntimeError(
                "Stripe payment verification "
                f"failed: {exc}"
            ) from exc

        transaction_id = getattr(
            intent,
            "id",
            None,
        )

        if not transaction_id:
            raise RuntimeError(
                "Stripe returned an invalid "
                "PaymentIntent"
            )

        currency = (
            getattr(
                intent,
                "currency",
                "",
            )
            or ""
        ).lower()

        metadata = getattr(
            intent,
            "metadata",
            {},
        ) or {}

        if not isinstance(
            metadata,
            dict,
        ):
            metadata = dict(metadata)

        return {
            "provider": "stripe",
            "reference": (
                metadata.get("reference")
                or reference
            ),
            "transaction_id": transaction_id,
            "status": getattr(
                intent,
                "status",
                None,
            ),
            "amount": self._from_smallest_unit(
                getattr(
                    intent,
                    "amount",
                    None,
                ),
                currency,
            ),
            "currency": currency.upper(),
            "paid": (
                getattr(
                    intent,
                    "status",
                    None,
                )
                == "succeeded"
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
            webhook_signature = headers.get(
                "Stripe-Signature"
            )

        if not webhook_signature:
            raise ValueError(
                "Stripe webhook signature is required"
            )

        try:
            event = stripe.Webhook.construct_event(
                payload,
                webhook_signature,
                self.webhook_secret,
            )
        except ValueError as exc:
            raise ValueError(
                "Invalid Stripe webhook payload"
            ) from exc
        except stripe.SignatureVerificationError as exc:
            raise ValueError(
                "Invalid Stripe webhook signature"
            ) from exc

        if not isinstance(event, dict):
            raise ValueError(
                "Invalid Stripe webhook event"
            )

        event_type = event.get("type")

        if not event_type:
            raise ValueError(
                "Stripe webhook event type is missing"
            )

        event_data = event.get("data") or {}

        event_object = (
            event_data.get("object")
            if isinstance(
                event_data,
                dict,
            )
            else None
        )

        if not isinstance(
            event_object,
            dict,
        ):
            raise ValueError(
                "Invalid Stripe webhook event data"
            )

        metadata = (
            event_object.get("metadata")
            or {}
        )

        if not isinstance(
            metadata,
            dict,
        ):
            metadata = {}

        normalized: dict[str, Any] = {
            "provider": "stripe",
            "event_id": event.get("id"),
            "event_type": event_type,
            "livemode": bool(
                event.get("livemode", False)
            ),
            "transaction_id": (
                event_object.get("id")
            ),
            "reference": metadata.get(
                "reference"
            ),
        }

        currency = (
            event_object.get("currency")
            or ""
        ).lower()

        provider_amount = event_object.get(
            "amount"
        )

        normalized["amount"] = (
            self._from_smallest_unit(
                provider_amount,
                currency,
            )
            if provider_amount is not None
            else None
        )

        normalized["currency"] = (
            currency.upper()
        )

        if event_type == (
            "payment_intent.succeeded"
        ):
            normalized["status"] = (
                "successful"
            )

        elif event_type == (
            "payment_intent.payment_failed"
        ):
            last_error = (
                event_object.get(
                    "last_payment_error"
                )
                or {}
            )

            normalized["status"] = "failed"
            normalized["failure_reason"] = (
                last_error.get("message")
                or "Stripe payment failed"
            )

        else:
            normalized["status"] = (
                event_object.get("status")
            )

        return normalized