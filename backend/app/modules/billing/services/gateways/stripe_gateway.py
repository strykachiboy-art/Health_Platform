from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import stripe

from app.modules.billing.services.gateways.response_utils import (
    ProviderResponseTooLargeError,
    read_response_body_limited,
)

from app.modules.billing.services.gateways.base_gateway import (
    GatewayRejectedError,
    GatewayUnknownOutcomeError,
    PaymentGatewayBase,
)


STRIPE_HTTP_TIMEOUT_SECONDS = 30
STRIPE_MAX_NETWORK_RETRIES = 0
STRIPE_PAYMENT_INTENT_SEARCH_LIMIT = 100


def _stripe_object_field(value: object, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)

    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return getter(name, default)
        except (AttributeError, TypeError):
            pass

    return getattr(value, name, default)


def _is_stripe_idempotency_error(error: BaseException) -> bool:
    """Recognize Stripe's explicit idempotency-key parameter mismatch.

    Gateway initialization wraps Stripe exceptions, so inspect the exception
    cause/context chain instead of treating every wrapped failure as a mismatch.
    """
    error_type = getattr(stripe, "IdempotencyError", None)
    if not isinstance(error_type, type):
        return False

    current = error
    seen: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, error_type):
            return True
        current = current.__cause__ or current.__context__

    return False


class BoundedStripeRequestsClient(stripe.RequestsClient):
    """Stripe RequestsClient adapter that bounds normal response bodies."""

    def request(self, method, url, headers, post_data=None):
        kwargs = {
            "stream": True,
            "timeout": self._timeout,
            "verify": (
                stripe.ca_bundle_path
                if self._verify_ssl_certs
                else False
            ),
        }
        if self._proxy:
            kwargs["proxies"] = self._proxy

        if getattr(self._thread_local, "session", None) is None:
            self._thread_local.session = (
                self._session or self.requests.Session()
            )

        try:
            response = self._thread_local.session.request(
                method,
                url,
                headers=headers,
                data=post_data,
                **kwargs,
            )
        except Exception as exc:
            self._handle_request_error(exc)

        status_code = response.status_code
        response_headers = response.headers
        try:
            body = read_response_body_limited(response)
        except ProviderResponseTooLargeError:
            raise
        except Exception as exc:
            self._handle_request_error(exc)

        return body, status_code, response_headers


def _build_stripe_http_client():
    return BoundedStripeRequestsClient(
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
        except ProviderResponseTooLargeError as exc:
            raise GatewayUnknownOutcomeError(
                "Stripe initialization response exceeded the byte limit; "
                "payment outcome remains unknown"
            ) from exc
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

    def _search_payment_intent_id_for_payment(
        self,
        *,
        reference: str,
        amount: Decimal,
        currency: str,
        clinic_id: int,
        invoice_id: int,
        payment_id: int,
    ) -> str | None:
        """Return only one exact local-payment match from Stripe Search.

        Stripe Search is eventually consistent, so an empty, ambiguous,
        paginated, or malformed response is a miss, not proof of failure.
        The caller must retrieve and verify the candidate before settlement.
        """
        identifiers = (clinic_id, invoice_id, payment_id)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in identifiers
        ):
            return None

        try:
            reference = self._normalize_reference(reference)
            currency = self._normalize_currency(currency)
            expected_amount = self._to_smallest_unit(
                Decimal(str(amount)),
                currency,
            )
        except (InvalidOperation, TypeError, ValueError):
            return None

        query = (
            f'metadata["clinic_id"]:"{clinic_id}" AND '
            f'metadata["clinic_payment_id"]:"{payment_id}" AND '
            f'metadata["invoice_id"]:"{invoice_id}"'
        )

        response = self._client.v1.payment_intents.search(
            {
                "query": query,
                "limit": STRIPE_PAYMENT_INTENT_SEARCH_LIMIT,
            }
        )

        if _stripe_object_field(response, "has_more", False):
            return None

        results = _stripe_object_field(response, "data")
        if not isinstance(results, (list, tuple)) or len(results) != 1:
            return None

        intent = results[0]
        raw_metadata = _stripe_object_field(intent, "metadata", {}) or {}
        try:
            metadata = dict(raw_metadata)
        except (TypeError, ValueError):
            return None

        if (
            metadata.get("reference") != reference
            or metadata.get("clinic_id") != str(clinic_id)
            or metadata.get("clinic_payment_id") != str(payment_id)
            or metadata.get("invoice_id") != str(invoice_id)
        ):
            return None

        raw_amount = _stripe_object_field(intent, "amount")
        if (
            isinstance(raw_amount, bool)
            or not isinstance(raw_amount, int)
            or raw_amount != expected_amount
        ):
            return None

        raw_currency = _stripe_object_field(intent, "currency")
        if not isinstance(raw_currency, str) or raw_currency.lower() != currency:
            return None

        transaction_id = _stripe_object_field(intent, "id")
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            return None

        return transaction_id.strip()

    def recover_payment_intent_id(
        self,
        *,
        reference: str,
        amount: Decimal,
        currency: str,
        customer_email: str | None,
        idempotency_key: str | None,
        clinic_id: int,
        invoice_id: int,
        payment_id: int,
        allow_idempotent_replay: bool,
    ) -> str | None:
        """Recover the PaymentIntent locator without treating it as success.

        Within the conservative age window, replay the same POST idempotency
        key first. Try current metadata, then the legacy metadata shape used
        before local payment identifiers were attached. Outside the window,
        never repeat the POST: Search is read-only and may return a temporary
        miss because Stripe Search is eventually consistent.
        """
        replay_key_is_valid = (
            isinstance(idempotency_key, str)
            and bool(idempotency_key.strip())
        )

        if (
            allow_idempotent_replay is True
            and replay_key_is_valid
            and isinstance(customer_email, str)
            and bool(customer_email.strip())
        ):
            tracked_metadata = {
                "clinic_id": clinic_id,
                "clinic_payment_id": payment_id,
                "invoice_id": invoice_id,
            }

            # First replay the current request shape. The second shape supports
            # a still-replayable request created before tracking metadata was
            # added. Stripe rejects parameter mismatch for retained keys.
            for replay_index, replay_metadata in enumerate(
                (tracked_metadata, None)
            ):
                try:
                    replay_result = self.initialize_payment(
                        reference=reference,
                        amount=amount,
                        currency=currency,
                        customer_email=customer_email,
                        metadata=replay_metadata,
                        idempotency_key=idempotency_key,
                    )
                except Exception as exc:
                    # Change request shape only when Stripe explicitly reports
                    # that this idempotency key was reused with different params.
                    if replay_index == 0 and _is_stripe_idempotency_error(exc):
                        continue
                    # Transient, unknown, and other rejected outcomes must not
                    # trigger a second POST with different parameters.
                    break

                transaction_id = (
                    replay_result.get("transaction_id")
                    if isinstance(replay_result, dict)
                    else None
                )
                if isinstance(transaction_id, str) and transaction_id.strip():
                    return transaction_id.strip()

                # A malformed/unusable response is not evidence of parameter
                # mismatch. Do not make a second POST with another shape.
                break

        try:
            return self._search_payment_intent_id_for_payment(
                reference=reference,
                amount=amount,
                currency=currency,
                clinic_id=clinic_id,
                invoice_id=invoice_id,
                payment_id=payment_id,
            )
        except Exception:
            # Search outages and eventual-consistency misses remain pending.
            return None

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