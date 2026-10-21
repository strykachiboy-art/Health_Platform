from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentMethod,
    PaymentStatus,
)


# ============================================================================
# Constants
# ============================================================================


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500

MAX_INSURANCE_PROVIDER_LENGTH = 120
MAX_PAYMENT_REFERENCE_LENGTH = 120
MAX_GATEWAY_TRANSACTION_ID_LENGTH = 255


# ============================================================================
# Invoice Item Schemas
# ============================================================================


class InvoiceItemRequest(BaseModel):
    description: str = Field(
        ...,
        min_length=1,
        max_length=255,
    )

    quantity: StrictInt = Field(
        default=1,
        gt=0,
    )

    unit_price: Decimal = Field(
        ...,
        ge=0,
    )

    @field_validator("description")
    @classmethod
    def validate_description(
        cls,
        value: str,
    ) -> str:
        value = value.strip()

        if not value:
            raise ValueError(
                "Description cannot be empty"
            )

        return value

    @field_validator("unit_price")
    @classmethod
    def validate_unit_price(
        cls,
        value: Decimal,
    ) -> Decimal:
        if not value.is_finite():
            raise ValueError(
                "Unit price must be a finite value"
            )

        return value

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class InvoiceItemResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: StrictInt
    invoice_id: StrictInt
    description: str
    quantity: StrictInt
    unit_price: Decimal
    subtotal: Decimal


# ============================================================================
# Invoice Schemas
# ============================================================================


class CreateInvoiceRequest(BaseModel):
    patient_id: StrictInt = Field(
        ...,
        gt=0,
    )

    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
    )

    appointment_id: StrictInt | None = Field(
        default=None,
        gt=0,
    )

    due_date: date | None = None

    is_insurance_claim: StrictBool = False

    insurance_provider: str | None = Field(
        default=None,
        max_length=MAX_INSURANCE_PROVIDER_LENGTH,
    )

    items: list[InvoiceItemRequest] = Field(
        ...,
        min_length=1,
    )

    @field_validator("currency")
    @classmethod
    def normalize_currency(
        cls,
        value: str,
    ) -> str:
        value = value.strip().upper()

        if (
            len(value) != 3
            or not value.isascii()
            or not value.isalpha()
        ):
            raise ValueError(
                "Currency must be a 3-letter ASCII code"
            )

        return value

    @field_validator("insurance_provider")
    @classmethod
    def validate_insurance_provider(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        return value or None

    @field_validator("items")
    @classmethod
    def validate_items(
        cls,
        value: list[InvoiceItemRequest],
    ) -> list[InvoiceItemRequest]:
        if not value:
            raise ValueError(
                "At least one invoice item is required"
            )

        return value

    @model_validator(mode="after")
    def validate_insurance_claim(self):
        if (
            self.is_insurance_claim
            and not self.insurance_provider
        ):
            raise ValueError(
                "Insurance provider is required "
                "for an insurance claim"
            )

        if not self.is_insurance_claim:
            self.insurance_provider = None

        return self

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: StrictInt
    clinic_id: StrictInt
    patient_id: StrictInt
    appointment_id: StrictInt | None

    invoice_number: str
    currency: str | None = None

    total_amount: Decimal
    amount_paid: Decimal

    status: InvoiceStatus

    due_date: date | None

    is_insurance_claim: bool
    insurance_provider: str | None

    created_at: datetime
    updated_at: datetime

    items: list[InvoiceItemResponse] = Field(
        default_factory=list,
    )


class OutstandingInvoiceResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: StrictInt
    clinic_id: StrictInt
    patient_id: StrictInt
    appointment_id: StrictInt | None

    invoice_number: str
    currency: str | None = None

    total_amount: Decimal
    amount_paid: Decimal

    status: InvoiceStatus

    due_date: date | None

    is_insurance_claim: bool
    insurance_provider: str | None

    created_at: datetime
    updated_at: datetime


# ============================================================================
# Outstanding Invoice Query
# ============================================================================


class OutstandingInvoiceQuery(BaseModel):
    page: StrictInt = Field(
        default=DEFAULT_PAGE,
        gt=0,
    )

    per_page: StrictInt = Field(
        default=DEFAULT_PER_PAGE,
        gt=0,
        le=MAX_PER_PAGE,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


# ============================================================================
# Payment Schemas
# ============================================================================


class InitializePaymentRequest(BaseModel):
    invoice_id: StrictInt = Field(
        ...,
        gt=0,
    )

    amount: Decimal = Field(
        ...,
        gt=0,
    )

    method: PaymentMethod

    gateway: PaymentGateway

    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
    )

    reference: str | None = Field(
        default=None,
        max_length=MAX_PAYMENT_REFERENCE_LENGTH,
    )

    @field_validator("amount")
    @classmethod
    def validate_amount(
        cls,
        value: Decimal,
    ) -> Decimal:
        if not value.is_finite():
            raise ValueError(
                "Amount must be a finite value"
            )

        if value <= 0:
            raise ValueError(
                "Amount must be greater than zero"
            )

        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(
        cls,
        value: str,
    ) -> str:
        value = value.strip().upper()

        if len(value) != 3 or not value.isalpha():
            raise ValueError(
                "Currency must be a 3-letter code"
            )

        return value

    @field_validator("reference")
    @classmethod
    def normalize_reference(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        return value or None

    @model_validator(mode="after")
    def validate_gateway_data(self):
        electronic_methods = {
            PaymentMethod.CARD,
            PaymentMethod.BANK_TRANSFER,
            PaymentMethod.MOBILE_MONEY,
        }

        if self.method not in electronic_methods:
            raise ValueError(
                "Gateway orchestration requires "
                "an electronic payment method"
            )

        return self

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class RecordPaymentRequest(BaseModel):
    invoice_id: StrictInt = Field(
        ...,
        gt=0,
    )

    amount: Decimal = Field(
        ...,
        gt=0,
    )

    method: PaymentMethod

    gateway: PaymentGateway | None = None

    reference: str | None = Field(
        default=None,
        max_length=MAX_PAYMENT_REFERENCE_LENGTH,
    )

    gateway_transaction_id: str | None = Field(
        default=None,
        max_length=MAX_GATEWAY_TRANSACTION_ID_LENGTH,
    )

    @field_validator("amount")
    @classmethod
    def validate_amount(
        cls,
        value: Decimal,
    ) -> Decimal:
        if not value.is_finite():
            raise ValueError(
                "Amount must be a finite value"
            )

        if value <= 0:
            raise ValueError(
                "Amount must be greater than zero"
            )

        return value

    @field_validator(
        "reference",
        "gateway_transaction_id",
    )
    @classmethod
    def normalize_optional_strings(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        return value or None

    @model_validator(mode="after")
    def validate_gateway_data(self):
        electronic_methods = {
            PaymentMethod.CARD,
            PaymentMethod.BANK_TRANSFER,
            PaymentMethod.MOBILE_MONEY,
        }

        if (
            self.gateway is not None
            and self.method not in electronic_methods
        ):
            raise ValueError(
                "Payment gateway cannot be used "
                f"with payment method "
                f"'{self.method.value}'"
            )

        if (
            self.gateway is not None
            and not self.gateway_transaction_id
        ):
            raise ValueError(
                "Gateway transaction ID is required "
                "for gateway payments"
            )

        return self

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class PaymentResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: StrictInt
    invoice_id: StrictInt

    amount: Decimal
    currency: str | None = None

    method: PaymentMethod
    status: PaymentStatus

    gateway: PaymentGateway | None

    reference: str | None
    gateway_transaction_id: str | None

    failure_reason: str | None

    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None