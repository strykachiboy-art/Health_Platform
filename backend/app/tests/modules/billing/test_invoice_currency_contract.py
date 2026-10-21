import pytest
from pydantic import ValidationError

from app.modules.billing.models.billing_model import Invoice
from app.modules.billing.schemas.billing_schema import (
    CreateInvoiceRequest,
    InvoiceResponse,
    OutstandingInvoiceResponse,
)


def _invoice_payload(**overrides):
    payload = {
        "patient_id": 1,
        "items": [
            {
                "description": "Consultation",
                "quantity": 1,
                "unit_price": "2500.00",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_invoice_request_requires_explicit_currency():
    with pytest.raises(ValidationError, match="currency"):
        CreateInvoiceRequest.model_validate(_invoice_payload())


@pytest.mark.parametrize(
    ("currency", "expected"),
    [
        ("NGN", "NGN"),
        (" usd ", "USD"),
        ("eur", "EUR"),
    ],
)
def test_invoice_request_normalizes_explicit_currency(currency, expected):
    payload = CreateInvoiceRequest.model_validate(
        _invoice_payload(currency=currency)
    )
    assert payload.currency == expected


@pytest.mark.parametrize("currency", ["US", "US1", "US$", ""])
def test_invoice_request_rejects_invalid_currency(currency):
    with pytest.raises(ValidationError):
        CreateInvoiceRequest.model_validate(
            _invoice_payload(currency=currency)
        )


def test_invoice_currency_column_allows_legacy_unknown_currency():
    column = Invoice.__table__.columns.get("currency")

    assert column is not None
    assert column.nullable is True


@pytest.mark.parametrize(
    "schema",
    [InvoiceResponse, OutstandingInvoiceResponse],
)
def test_invoice_response_contract_exposes_nullable_currency(schema):
    field = schema.model_fields.get("currency")

    assert field is not None
    assert field.annotation == (str | None)