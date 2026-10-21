from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentMethod,
    PaymentStatus,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.modules.billing.models.billing_model import (
    Invoice,
    InvoiceItem,
)
from app.modules.billing.services import billing_service


# ============================================================================
# Utility Helpers
# ============================================================================


class FakePage:
    def __init__(
        self,
        items=None,
        page=1,
        per_page=50,
        total=None,
    ):
        self.items = items or []
        self.page = page
        self.per_page = per_page
        self.total = (
            len(self.items)
            if total is None
            else total
        )
        self.pages = (
            0
            if self.total == 0
            else (
                self.total
                + self.per_page
                - 1
            )
            // self.per_page
        )
        self.has_next = (
            self.page < self.pages
        )
        self.has_prev = (
            self.page > 1
        )


class FakeQuery:
    def __init__(
        self,
        result=None,
        results=None,
        page=None,
    ):
        self.result = result
        self.results = results or []
        self.page_result = page
        self.filter_calls = []
        self.order_by_calls = []
        self.with_for_update_called = False
        self.paginate_calls = []

    def filter(self, *args):
        self.filter_calls.append(args)
        return self

    def order_by(self, *args):
        self.order_by_calls.append(args)
        return self

    def with_for_update(self):
        self.with_for_update_called = True
        return self

    def first(self):
        return self.result

    def all(self):
        return self.results

    def paginate(
        self,
        page,
        per_page,
        error_out=False,
    ):
        self.paginate_calls.append(
            {
                "page": page,
                "per_page": per_page,
                "error_out": error_out,
            }
        )

        return self.page_result or FakePage(
            self.results,
            page=page,
            per_page=per_page,
        )


def patch_common_create_dependencies(
    monkeypatch,
):
    monkeypatch.setattr(
        billing_service,
        "_lock_clinic",
        lambda clinic_id: SimpleNamespace(
            id=clinic_id
        ),
    )

    monkeypatch.setattr(
        billing_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        billing_service,
        "create_audit_log",
        lambda **kwargs: None,
    )


def patch_common_payment_dependencies(
    monkeypatch,
):
    monkeypatch.setattr(
        billing_service,
        "_lock_clinic",
        lambda clinic_id: SimpleNamespace(
            id=clinic_id
        ),
    )

    monkeypatch.setattr(
        billing_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        billing_service,
        "create_audit_log",
        lambda **kwargs: None,
    )


# ============================================================================
# _validate_positive_id
# ============================================================================


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        -100,
        True,
        False,
        1.5,
        "1",
        None,
    ],
)
def test_validate_positive_id_rejects_invalid_values(
    value,
):
    with pytest.raises(
        ValidationError,
        match="Clinic ID must be a positive integer",
    ):
        billing_service._validate_positive_id(
            value,
            "Clinic ID",
        )


@pytest.mark.parametrize(
    "value",
    [
        1,
        2,
        100,
        999999,
    ],
)
def test_validate_positive_id_accepts_positive_integers(
    value,
):
    assert (
        billing_service._validate_positive_id(
            value,
            "Clinic ID",
        )
        is None
    )


# ============================================================================
# _validate_pagination
# ============================================================================


@pytest.mark.parametrize(
    "page,per_page",
    [
        (0, 50),
        (-1, 50),
        (True, 50),
        ("1", 50),
        (1, 0),
        (1, -1),
        (1, True),
        (1, "50"),
        (1, 501),
    ],
)
def test_validate_pagination_rejects_invalid_values(
    page,
    per_page,
):
    with pytest.raises(ValidationError):
        billing_service._validate_pagination(
            page,
            per_page,
        )


@pytest.mark.parametrize(
    "page,per_page",
    [
        (1, 50),
        (2, 100),
        (10, 500),
    ],
)
def test_validate_pagination_accepts_valid_values(
    page,
    per_page,
):
    assert (
        billing_service._validate_pagination(
            page,
            per_page,
        )
        is None
    )


# ============================================================================
# _to_decimal
# ============================================================================


@pytest.mark.parametrize(
    "value,expected",
    [
        ("10", Decimal("10")),
        ("10.50", Decimal("10.50")),
        (10, Decimal("10")),
        (10.25, Decimal("10.25")),
        (Decimal("25.75"), Decimal("25.75")),
        ("0", Decimal("0")),
    ],
)
def test_to_decimal_converts_valid_values(
    value,
    expected,
):
    assert (
        billing_service._to_decimal(value)
        == expected
    )


@pytest.mark.parametrize(
    "value",
    [
        "-1",
        "-10.50",
        -1,
        Decimal("-5.00"),
    ],
)
def test_to_decimal_rejects_negative_values(
    value,
):
    with pytest.raises(
        ValidationError,
        match="amount cannot be negative",
    ):
        billing_service._to_decimal(value)


@pytest.mark.parametrize(
    "value",
    [
        "not-a-number",
        None,
        object(),
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_to_decimal_rejects_invalid_values(
    value,
):
    with pytest.raises(
        ValidationError,
        match="Invalid amount",
    ):
        billing_service._to_decimal(value)


# ============================================================================
# _normalize_optional_string
# ============================================================================


def test_normalize_optional_string_returns_none_for_none():
    assert (
        billing_service._normalize_optional_string(
            None,
            "Reference",
        )
        is None
    )


def test_normalize_optional_string_strips_whitespace():
    assert (
        billing_service._normalize_optional_string(
            "  REF-123  ",
            "Reference",
        )
        == "REF-123"
    )


def test_normalize_optional_string_returns_none_for_blank():
    assert (
        billing_service._normalize_optional_string(
            "   ",
            "Reference",
        )
        is None
    )


def test_normalize_optional_string_rejects_non_string():
    with pytest.raises(
        ValidationError,
        match="Reference must be a string",
    ):
        billing_service._normalize_optional_string(
            123,
            "Reference",
        )


def test_normalize_optional_string_rejects_excessive_length():
    with pytest.raises(
        ValidationError,
        match="Reference cannot exceed 5 characters",
    ):
        billing_service._normalize_optional_string(
            "123456",
            "Reference",
            max_length=5,
        )


# ============================================================================
# _validate_boolean
# ============================================================================


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
    ],
)
def test_validate_boolean_accepts_bool(
    value,
):
    assert (
        billing_service._validate_boolean(
            value,
            "Insurance claim flag",
        )
        is None
    )


@pytest.mark.parametrize(
    "value",
    [
        1,
        0,
        "true",
        "false",
        None,
    ],
)
def test_validate_boolean_rejects_non_bool(
    value,
):
    with pytest.raises(
        ValidationError,
        match="Insurance claim flag must be a boolean",
    ):
        billing_service._validate_boolean(
            value,
            "Insurance claim flag",
        )


# ============================================================================
# _generate_invoice_number
# ============================================================================


def test_generate_invoice_number_format(
    monkeypatch,
):
    class FakeDate:
        @classmethod
        def today(cls):
            return date(2026, 9, 7)

    class FakeUUID:
        hex = "abcdef1234567890"

    monkeypatch.setattr(
        billing_service,
        "date",
        FakeDate,
    )

    monkeypatch.setattr(
        billing_service,
        "uuid4",
        lambda: FakeUUID(),
    )

    result = (
        billing_service._generate_invoice_number(
            42,
        )
    )

    assert (
        result
        == "INV-42-20260907-ABCDEF12"
    )


# ============================================================================
# _calculate_invoice_status
# ============================================================================


@pytest.mark.parametrize(
    "total,paid,expected",
    [
        (
            Decimal("100.00"),
            Decimal("0.00"),
            InvoiceStatus.ISSUED,
        ),
        (
            Decimal("100.00"),
            Decimal("25.00"),
            InvoiceStatus.PARTIALLY_PAID,
        ),
        (
            Decimal("100.00"),
            Decimal("100.00"),
            InvoiceStatus.PAID,
        ),
        (
            Decimal("100.00"),
            Decimal("125.00"),
            InvoiceStatus.PAID,
        ),
    ],
)
def test_calculate_invoice_status(
    total,
    paid,
    expected,
):
    invoice = SimpleNamespace(
        total_amount=total,
        amount_paid=paid,
    )

    assert (
        billing_service._calculate_invoice_status(
            invoice
        )
        == expected
    )


# ============================================================================
# _validate_invoice_items
# ============================================================================


def test_validate_invoice_items_success():
    result = (
        billing_service._validate_invoice_items(
            [
                {
                    "description": " Consultation ",
                    "quantity": 2,
                    "unit_price": "50.00",
                }
            ]
        )
    )

    assert result == [
        {
            "description": "Consultation",
            "quantity": 2,
            "unit_price": Decimal("50.00"),
        }
    ]


def test_validate_invoice_items_defaults_quantity():
    result = (
        billing_service._validate_invoice_items(
            [
                {
                    "description": "Consultation",
                    "unit_price": "100.00",
                }
            ]
        )
    )

    assert result == [
        {
            "description": "Consultation",
            "quantity": 1,
            "unit_price": Decimal("100.00"),
        }
    ]


def test_validate_invoice_items_requires_items():
    with pytest.raises(
        ValidationError,
        match="Invoice must contain at least one item",
    ):
        billing_service._validate_invoice_items([])


@pytest.mark.parametrize(
    "items",
    [
        None,
        {},
        "invalid",
        123,
    ],
)
def test_validate_invoice_items_rejects_invalid_collection(
    items,
):
    with pytest.raises(
        ValidationError,
        match="Invoice must contain at least one item",
    ):
        billing_service._validate_invoice_items(
            items
        )


def test_validate_invoice_items_rejects_non_dict():
    with pytest.raises(
        ValidationError,
        match="Each invoice item must be an object",
    ):
        billing_service._validate_invoice_items(
            ["invalid"]
        )


def test_validate_invoice_items_requires_description():
    with pytest.raises(
        ValidationError,
        match="Each invoice item requires a description",
    ):
        billing_service._validate_invoice_items(
            [
                {
                    "quantity": 1,
                    "unit_price": "100",
                }
            ]
        )


def test_validate_invoice_items_rejects_blank_description():
    with pytest.raises(
        ValidationError,
        match="Each invoice item requires a description",
    ):
        billing_service._validate_invoice_items(
            [
                {
                    "description": "   ",
                    "quantity": 1,
                    "unit_price": "100",
                }
            ]
        )


def test_validate_invoice_items_rejects_long_description():
    with pytest.raises(
        ValidationError,
        match="Invoice item description cannot exceed 255 characters",
    ):
        billing_service._validate_invoice_items(
            [
                {
                    "description": "x" * 256,
                    "quantity": 1,
                    "unit_price": "100",
                }
            ]
        )


@pytest.mark.parametrize(
    "quantity",
    [
        0,
        -1,
        True,
        False,
        1.5,
        "2",
    ],
)
def test_validate_invoice_items_rejects_invalid_quantity(
    quantity,
):
    with pytest.raises(
        ValidationError,
        match="Invoice item quantity must be a positive integer",
    ):
        billing_service._validate_invoice_items(
            [
                {
                    "description": "Consultation",
                    "quantity": quantity,
                    "unit_price": "100",
                }
            ]
        )


@pytest.mark.parametrize(
    "unit_price",
    [
        "-1",
        Decimal("-5.00"),
        "NaN",
        "Infinity",
    ],
)
def test_validate_invoice_items_rejects_invalid_unit_price(
    unit_price,
):
    with pytest.raises(
        ValidationError
    ):
        billing_service._validate_invoice_items(
            [
                {
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": unit_price,
                }
            ]
        )


# ============================================================================
# _get_invoice
# ============================================================================


def test_get_invoice_rejects_invalid_id():
    with pytest.raises(
        ValidationError,
        match="Invoice ID must be a positive integer",
    ):
        billing_service._get_invoice(0)


def test_get_invoice_returns_invoice(
    app,
    monkeypatch,
):
    invoice = SimpleNamespace(
        id=10,
        clinic_id=5,
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        FakeQuery(result=invoice),
    )

    with app.app_context():
        result = billing_service._get_invoice(
            10
        )

    assert result is invoice


def test_get_invoice_applies_clinic_filter(
    app,
    monkeypatch,
):
    invoice = SimpleNamespace(
        id=10,
        clinic_id=5,
    )

    query = FakeQuery(
        result=invoice
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    with app.app_context():
        result = billing_service._get_invoice(
            10,
            clinic_id=5,
        )

    assert result is invoice
    assert query.filter_calls


def test_get_invoice_locks_row(
    app,
    monkeypatch,
):
    invoice = SimpleNamespace(
        id=10,
        clinic_id=5,
    )

    query = FakeQuery(
        result=invoice
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    with app.app_context():
        result = billing_service._get_invoice(
            10,
            clinic_id=5,
            lock=True,
        )

    assert result is invoice
    assert query.with_for_update_called is True


def test_get_invoice_raises_not_found(
    app,
    monkeypatch,
):
    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        FakeQuery(result=None),
    )

    with app.app_context():
        with pytest.raises(
            NotFoundError,
            match="Invoice 10 not found",
        ):
            billing_service._get_invoice(10)


# ============================================================================
# _lock_clinic
# ============================================================================


def test_lock_clinic_rejects_invalid_id(
    app,
):
    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be a positive integer",
        ):
            billing_service._lock_clinic(0)


def test_lock_clinic_returns_clinic(
    app,
    monkeypatch,
):
    clinic = SimpleNamespace(
        id=10
    )

    query = FakeQuery(
        result=clinic
    )

    monkeypatch.setattr(
        billing_service.Clinic,
        "query",
        query,
    )

    with app.app_context():
        result = billing_service._lock_clinic(
            10
        )

    assert result is clinic
    assert query.with_for_update_called is True


def test_lock_clinic_raises_not_found(
    app,
    monkeypatch,
):
    monkeypatch.setattr(
        billing_service.Clinic,
        "query",
        FakeQuery(result=None),
    )

    with app.app_context():
        with pytest.raises(
            NotFoundError,
            match="Clinic 10 not found",
        ):
            billing_service._lock_clinic(10)


# ============================================================================
# _validate_invoice_relationships
# ============================================================================


def test_validate_invoice_relationships_rejects_invalid_clinic():
    with pytest.raises(
        ValidationError,
        match="Clinic ID must be a positive integer",
    ):
        billing_service._validate_invoice_relationships(
            clinic_id=0,
            patient_id=20,
        )


def test_validate_invoice_relationships_rejects_invalid_patient():
    with pytest.raises(
        ValidationError,
        match="Patient ID must be a positive integer",
    ):
        billing_service._validate_invoice_relationships(
            clinic_id=10,
            patient_id=0,
        )


def test_validate_invoice_relationships_rejects_invalid_appointment():
    with pytest.raises(
        ValidationError,
        match="Appointment ID must be a positive integer",
    ):
        billing_service._validate_invoice_relationships(
            clinic_id=10,
            patient_id=20,
            appointment_id=0,
        )


def test_validate_invoice_relationships_rejects_wrong_patient_clinic(
    monkeypatch,
):
    patient = SimpleNamespace(
        id=20,
        clinic_id=99,
    )

    monkeypatch.setattr(
        billing_service,
        "get_patient",
        lambda patient_id: patient,
    )

    with pytest.raises(
        ValidationError,
        match="Patient 20 does not belong to clinic 10",
    ):
        billing_service._validate_invoice_relationships(
            clinic_id=10,
            patient_id=20,
        )


def test_validate_invoice_relationships_returns_patient_without_appointment(
    monkeypatch,
):
    patient = SimpleNamespace(
        id=20,
        clinic_id=10,
    )

    monkeypatch.setattr(
        billing_service,
        "get_patient",
        lambda patient_id: patient,
    )

    result = (
        billing_service._validate_invoice_relationships(
            clinic_id=10,
            patient_id=20,
        )
    )

    assert result == (
        patient,
        None,
    )


def test_validate_invoice_relationships_rejects_missing_appointment(
    app,
    monkeypatch,
):
    patient = SimpleNamespace(
        id=20,
        clinic_id=10,
    )

    monkeypatch.setattr(
        billing_service,
        "get_patient",
        lambda patient_id: patient,
    )

    monkeypatch.setattr(
        billing_service.db.session,
        "get",
        lambda model, object_id: None,
    )

    with app.app_context():
        with pytest.raises(
            NotFoundError,
            match="Appointment 30 not found",
        ):
            billing_service._validate_invoice_relationships(
                clinic_id=10,
                patient_id=20,
                appointment_id=30,
            )


def test_validate_invoice_relationships_rejects_wrong_appointment_clinic(
    app,
    monkeypatch,
):
    patient = SimpleNamespace(
        id=20,
        clinic_id=10,
    )

    appointment = SimpleNamespace(
        id=30,
        clinic_id=99,
        patient_id=20,
    )

    monkeypatch.setattr(
        billing_service,
        "get_patient",
        lambda patient_id: patient,
    )

    monkeypatch.setattr(
        billing_service.db.session,
        "get",
        lambda model, object_id: appointment,
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Appointment 30 does not belong to clinic 10",
        ):
            billing_service._validate_invoice_relationships(
                clinic_id=10,
                patient_id=20,
                appointment_id=30,
            )


def test_validate_invoice_relationships_rejects_wrong_appointment_patient(
    app,
    monkeypatch,
):
    patient = SimpleNamespace(
        id=20,
        clinic_id=10,
    )

    appointment = SimpleNamespace(
        id=30,
        clinic_id=10,
        patient_id=99,
    )

    monkeypatch.setattr(
        billing_service,
        "get_patient",
        lambda patient_id: patient,
    )

    monkeypatch.setattr(
        billing_service.db.session,
        "get",
        lambda model, object_id: appointment,
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Appointment does not belong to the specified patient",
        ):
            billing_service._validate_invoice_relationships(
                clinic_id=10,
                patient_id=20,
                appointment_id=30,
            )


def test_validate_invoice_relationships_success_with_appointment(
    app,
    monkeypatch,
):
    patient = SimpleNamespace(
        id=20,
        clinic_id=10,
    )

    appointment = SimpleNamespace(
        id=30,
        clinic_id=10,
        patient_id=20,
    )

    monkeypatch.setattr(
        billing_service,
        "get_patient",
        lambda patient_id: patient,
    )

    monkeypatch.setattr(
        billing_service.db.session,
        "get",
        lambda model, object_id: appointment,
    )

    with app.app_context():
        result = (
            billing_service._validate_invoice_relationships(
                clinic_id=10,
                patient_id=20,
                appointment_id=30,
            )
        )

    assert result == (
        patient,
        appointment,
    )


# ============================================================================
# create_invoice
# ============================================================================


def test_create_invoice_success(
    app,
    db_session,
    make_clinic,
    make_patient,
    monkeypatch,
):
    clinic = make_clinic()
    patient = make_patient(
        clinic=clinic,
    )

    patch_common_create_dependencies(
        monkeypatch
    )

    monkeypatch.setattr(
        billing_service,
        "_generate_invoice_number",
        lambda clinic_id: (
            "INV-10-20260907-ABC12345"
        ),
    )

    with app.app_context():
        invoice = billing_service.create_invoice(
            clinic_id=clinic.id,
            patient_id=patient.id,
            currency="NGN",
            items=[
                {
                    "description": "Consultation",
                    "quantity": 2,
                    "unit_price": "50.00",
                },
                {
                    "description": "Lab",
                    "quantity": 1,
                    "unit_price": "25.00",
                },
            ],
        )

        assert invoice.id is not None
        assert invoice.clinic_id == clinic.id
        assert invoice.patient_id == patient.id
        assert invoice.appointment_id is None
        assert (
            invoice.invoice_number
            == "INV-10-20260907-ABC12345"
        )
        assert invoice.total_amount == Decimal(
            "125.00"
        )
        assert invoice.amount_paid == Decimal(
            "0.00"
        )
        assert invoice.status == (
            InvoiceStatus.ISSUED
        )

        db_session.flush()

        items = (
            InvoiceItem.query
            .filter(
                InvoiceItem.invoice_id
                == invoice.id
            )
            .order_by(
                InvoiceItem.id.asc()
            )
            .all()
        )

        assert len(items) == 2

        assert (
            items[0].description
            == "Consultation"
        )
        assert items[0].quantity == 2
        assert items[0].unit_price == Decimal(
            "50.00"
        )
        assert items[0].subtotal == Decimal(
            "100.00"
        )

        assert items[1].description == "Lab"
        assert items[1].quantity == 1
        assert items[1].unit_price == Decimal(
            "25.00"
        )
        assert items[1].subtotal == Decimal(
            "25.00"
        )


def test_create_invoice_with_appointment(
    app,
    make_clinic,
    make_patient,
    make_staff,
    make_appointment,
    monkeypatch,
):
    clinic = make_clinic()
    patient = make_patient(
        clinic=clinic,
    )
    staff = make_staff(
        clinic=clinic,
    )

    appointment = make_appointment(
        clinic=clinic,
        patient=patient,
        staff=staff,
        id=30,
    )

    appointment_stub = SimpleNamespace(
        id=appointment.id,
        clinic_id=appointment.clinic_id,
        patient_id=appointment.patient_id,
    )

    patch_common_create_dependencies(
        monkeypatch
    )

    original_get = billing_service.db.session.get

    monkeypatch.setattr(
        billing_service.db.session,
        "get",
        lambda model, object_id: (
            appointment_stub
            if getattr(model, "__tablename__", None)
            == "appointments"
            else original_get(
                model,
                object_id,
            )
        ),
    )

    with app.app_context():
        invoice = billing_service.create_invoice(
            clinic_id=clinic.id,
            patient_id=patient.id,
            currency="NGN",
            appointment_id=appointment.id,
            items=[
                {
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        )

        assert invoice.appointment_id == 30
        assert invoice.total_amount == Decimal(
            "100.00"
        )


def test_create_invoice_requires_insurance_provider_for_claim(
    app,
    make_clinic,
    make_patient,
    monkeypatch,
):
    clinic = make_clinic()
    patient = make_patient(
        clinic=clinic,
    )

    patch_common_create_dependencies(
        monkeypatch
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match=(
                "Insurance provider is required "
                "for an insurance claim"
            ),
        ):
            billing_service.create_invoice(
                clinic_id=clinic.id,
                patient_id=patient.id,
                currency="NGN",
                is_insurance_claim=True,
                items=[
                    {
                        "description": "Treatment",
                        "quantity": 1,
                        "unit_price": "100.00",
                    }
                ],
            )


def test_create_invoice_normalizes_insurance_provider(
    app,
    make_clinic,
    make_patient,
    monkeypatch,
):
    clinic = make_clinic()
    patient = make_patient(
        clinic=clinic,
    )

    patch_common_create_dependencies(
        monkeypatch
    )

    with app.app_context():
        invoice = billing_service.create_invoice(
            clinic_id=clinic.id,
            patient_id=patient.id,
            currency="NGN",
            is_insurance_claim=True,
            insurance_provider="  NHIA  ",
            items=[
                {
                    "description": "Treatment",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        )

        assert (
            invoice.insurance_provider
            == "NHIA"
        )


def test_create_invoice_clears_provider_for_non_insurance_claim(
    app,
    make_clinic,
    make_patient,
    monkeypatch,
):
    clinic = make_clinic()
    patient = make_patient(
        clinic=clinic,
    )

    patch_common_create_dependencies(
        monkeypatch
    )

    with app.app_context():
        invoice = billing_service.create_invoice(
            clinic_id=clinic.id,
            patient_id=patient.id,
            currency="NGN",
            is_insurance_claim=False,
            insurance_provider=(
                "Private Insurance"
            ),
            items=[
                {
                    "description": "Treatment",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        )

        assert invoice.insurance_provider is None


def test_create_invoice_rejects_non_boolean_insurance_flag(
    app,
    make_clinic,
    make_patient,
    monkeypatch,
):
    clinic = make_clinic()
    patient = make_patient(
        clinic=clinic,
    )

    patch_common_create_dependencies(
        monkeypatch
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Insurance claim flag must be a boolean",
        ):
            billing_service.create_invoice(
                clinic_id=clinic.id,
                patient_id=patient.id,
                currency="NGN",
                is_insurance_claim="true",
                items=[
                    {
                        "description": "Treatment",
                        "quantity": 1,
                        "unit_price": "100.00",
                    }
                ],
            )


# ============================================================================
# Payment Normalization
# ============================================================================


@pytest.mark.parametrize(
    "value,expected",
    [
        (
            PaymentMethod.CASH,
            PaymentMethod.CASH,
        ),
        (
            "cash",
            PaymentMethod.CASH,
        ),
        (
            "card",
            PaymentMethod.CARD,
        ),
        (
            "bank_transfer",
            PaymentMethod.BANK_TRANSFER,
        ),
        (
            "insurance",
            PaymentMethod.INSURANCE,
        ),
        (
            "mobile_money",
            PaymentMethod.MOBILE_MONEY,
        ),
    ],
)
def test_normalize_payment_method(
    value,
    expected,
):
    assert (
        billing_service._normalize_payment_method(
            value
        )
        == expected
    )


def test_normalize_payment_method_rejects_invalid():
    with pytest.raises(
        ValidationError,
        match="Invalid payment method",
    ):
        billing_service._normalize_payment_method(
            "bitcoin"
        )


@pytest.mark.parametrize(
    "value,expected",
    [
        (
            PaymentGateway.PAYSTACK,
            PaymentGateway.PAYSTACK,
        ),
        (
            "paystack",
            PaymentGateway.PAYSTACK,
        ),
        (
            "stripe",
            PaymentGateway.STRIPE,
        ),
        (
            "flutterwave",
            PaymentGateway.FLUTTERWAVE,
        ),
    ],
)
def test_normalize_payment_gateway(
    value,
    expected,
):
    assert (
        billing_service._normalize_payment_gateway(
            value
        )
        == expected
    )


def test_normalize_payment_gateway_accepts_none():
    assert (
        billing_service._normalize_payment_gateway(
            None
        )
        is None
    )


def test_normalize_payment_gateway_rejects_invalid():
    with pytest.raises(
        ValidationError,
        match="Invalid payment gateway",
    ):
        billing_service._normalize_payment_gateway(
            "bitcoin"
        )


# ============================================================================
# Gateway / Method Compatibility
# ============================================================================


@pytest.mark.parametrize(
    "method",
    [
        PaymentMethod.CARD,
        PaymentMethod.BANK_TRANSFER,
        PaymentMethod.MOBILE_MONEY,
    ],
)
def test_gateway_allowed_for_electronic_methods(
    method,
):
    assert (
        billing_service._validate_gateway_method(
            method,
            PaymentGateway.PAYSTACK,
        )
        is None
    )


@pytest.mark.parametrize(
    "method",
    [
        PaymentMethod.CASH,
        PaymentMethod.INSURANCE,
    ],
)
def test_gateway_rejected_for_non_electronic_methods(
    method,
):
    with pytest.raises(
        ValidationError,
        match="Payment gateway cannot be used",
    ):
        billing_service._validate_gateway_method(
            method,
            PaymentGateway.PAYSTACK,
        )


def test_gateway_none_allowed_for_cash():
    assert (
        billing_service._validate_gateway_method(
            PaymentMethod.CASH,
            None,
        )
        is None
    )


# ============================================================================
# Duplicate Gateway Payment
# ============================================================================


def test_find_duplicate_gateway_payment_returns_none_without_transaction_id():
    result = (
        billing_service._find_duplicate_gateway_payment(
            invoice_id=10,
            gateway_transaction_id=None,
        )
    )

    assert result is None


def test_find_duplicate_gateway_payment_returns_successful_payment(
    app,
    monkeypatch,
):
    payment = SimpleNamespace(
        id=50,
        invoice_id=10,
        gateway_transaction_id="TX-123",
        status=PaymentStatus.SUCCESSFUL,
    )

    monkeypatch.setattr(
        billing_service.Payment,
        "query",
        FakeQuery(result=payment),
    )

    with app.app_context():
        result = (
            billing_service._find_duplicate_gateway_payment(
                invoice_id=10,
                gateway_transaction_id="TX-123",
            )
        )

    assert result is payment


def test_find_duplicate_gateway_payment_returns_none_when_not_found(
    app,
    monkeypatch,
):
    monkeypatch.setattr(
        billing_service.Payment,
        "query",
        FakeQuery(result=None),
    )

    with app.app_context():
        result = (
            billing_service._find_duplicate_gateway_payment(
                invoice_id=10,
                gateway_transaction_id="TX-123",
            )
        )

    assert result is None


# ============================================================================
# record_payment
# ============================================================================


def _make_payment_invoice(
    clinic_id=10,
    invoice_id=100,
    total=Decimal("100.00"),
    paid=Decimal("0.00"),
    status=InvoiceStatus.ISSUED,
):
    from app.extensions import db
    from app.modules.clinic.models.clinic_model import Clinic
    from app.modules.patient.models.patient_model import Patient

    clinic = db.session.get(
        Clinic,
        clinic_id,
    )

    if clinic is None:
        clinic = Clinic(
            id=clinic_id,
            name=f"Billing Test Clinic {clinic_id}",
        )
        db.session.add(clinic)
        db.session.flush()

    patient = db.session.get(
        Patient,
        20,
    )

    if patient is None:
        patient = Patient(
            id=20,
            clinic_id=clinic_id,
            first_name="Billing",
            last_name="Patient",
            patient_number="BILLING-MRN-20",
        )
        db.session.add(patient)
        db.session.flush()

    invoice = Invoice(
        id=invoice_id,
        clinic_id=clinic_id,
        patient_id=patient.id,
        invoice_number=(
            "INV-10-20260907-ABC12345"
        ),
        currency="NGN",
        total_amount=total,
        amount_paid=paid,
        status=status,
    )

    db.session.add(invoice)
    db.session.flush()

    return invoice


def patch_payment_invoice(
    monkeypatch,
    invoice,
):
    monkeypatch.setattr(
        billing_service,
        "_get_invoice",
        lambda invoice_id,
        clinic_id=None,
        lock=False: invoice,
    )

    monkeypatch.setattr(
        billing_service,
        "_find_duplicate_gateway_payment",
        lambda invoice_id,
        transaction_id: None,
    )

    patch_common_payment_dependencies(
        monkeypatch
    )


def test_record_payment_success(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice()

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        payment = billing_service.record_payment(
            clinic_id=10,
            invoice_id=100,
            amount="40.00",
            method="cash",
            reference="  CASH-001  ",
        )

        assert payment.id is not None
        assert payment.invoice_id == 100
        assert payment.amount == Decimal(
            "40.00"
        )
        assert payment.method == (
            PaymentMethod.CASH
        )
        assert payment.status == (
            PaymentStatus.SUCCESSFUL
        )
        assert payment.gateway is None
        assert payment.reference == "CASH-001"
        assert (
            payment.gateway_transaction_id
            is None
        )
        assert payment.paid_at is not None

        assert invoice.amount_paid == Decimal(
            "40.00"
        )
        assert invoice.status == (
            InvoiceStatus.PARTIALLY_PAID
        )


def test_record_payment_uses_invoice_row_lock(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice()
    captured = {}

    patch_common_payment_dependencies(
        monkeypatch
    )

    def fake_get_invoice(
        invoice_id,
        clinic_id=None,
        lock=False,
    ):
        captured["lock"] = lock
        return invoice

    monkeypatch.setattr(
        billing_service,
        "_get_invoice",
        fake_get_invoice,
    )

    monkeypatch.setattr(
        billing_service,
        "_find_duplicate_gateway_payment",
        lambda invoice_id,
        transaction_id: None,
    )

    with app.app_context():
        billing_service.record_payment(
            clinic_id=10,
            invoice_id=100,
            amount="40.00",
            method=PaymentMethod.CASH,
        )

    assert captured["lock"] is True


def test_record_payment_fully_pays_invoice(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice()

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        payment = billing_service.record_payment(
            clinic_id=10,
            invoice_id=100,
            amount="100.00",
            method=PaymentMethod.CARD,
        )

        assert payment.amount == Decimal(
            "100.00"
        )
        assert invoice.amount_paid == Decimal(
            "100.00"
        )
        assert invoice.status == (
            InvoiceStatus.PAID
        )


def test_record_payment_rejects_cancelled_invoice(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice(
        status=InvoiceStatus.CANCELLED,
    )

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        with pytest.raises(
            ConflictError,
            match="Cannot record payment for cancelled invoice",
        ):
            billing_service.record_payment(
                clinic_id=10,
                invoice_id=100,
                amount="50.00",
                method=PaymentMethod.CASH,
            )


def test_record_payment_rejects_paid_invoice(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice(
        paid=Decimal("100.00"),
        status=InvoiceStatus.PAID,
    )

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        with pytest.raises(
            ConflictError,
            match="already fully paid",
        ):
            billing_service.record_payment(
                clinic_id=10,
                invoice_id=100,
                amount="10.00",
                method=PaymentMethod.CASH,
            )


def test_record_payment_rejects_zero_amount(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice()

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Payment amount must be greater than zero",
        ):
            billing_service.record_payment(
                clinic_id=10,
                invoice_id=100,
                amount=0,
                method=PaymentMethod.CASH,
            )


@pytest.mark.parametrize(
    "amount",
    [
        "-1",
        "-10.00",
    ],
)
def test_record_payment_rejects_negative_amount(
    app,
    monkeypatch,
    amount,
):
    invoice = _make_payment_invoice()

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="payment amount cannot be negative",
        ):
            billing_service.record_payment(
                clinic_id=10,
                invoice_id=100,
                amount=amount,
                method=PaymentMethod.CASH,
            )


@pytest.mark.parametrize(
    "amount",
    [
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_record_payment_rejects_non_finite_amount(
    app,
    monkeypatch,
    amount,
):
    invoice = _make_payment_invoice()

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Invalid payment amount",
        ):
            billing_service.record_payment(
                clinic_id=10,
                invoice_id=100,
                amount=amount,
                method=PaymentMethod.CASH,
            )


def test_record_payment_rejects_gateway_without_transaction_id(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice()

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Gateway transaction ID is required",
        ):
            billing_service.record_payment(
                clinic_id=10,
                invoice_id=100,
                amount="50.00",
                method=PaymentMethod.CARD,
                gateway=PaymentGateway.PAYSTACK,
            )


def test_record_payment_rejects_gateway_for_cash(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice()

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Payment gateway cannot be used",
        ):
            billing_service.record_payment(
                clinic_id=10,
                invoice_id=100,
                amount="50.00",
                method=PaymentMethod.CASH,
                gateway=PaymentGateway.PAYSTACK,
                gateway_transaction_id="TX-123",
            )


def test_record_payment_rejects_duplicate_gateway_transaction(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice()

    duplicate = SimpleNamespace(
        id=500,
        gateway_transaction_id="TX-123",
        status=PaymentStatus.SUCCESSFUL,
    )

    patch_common_payment_dependencies(
        monkeypatch
    )

    monkeypatch.setattr(
        billing_service,
        "_get_invoice",
        lambda invoice_id,
        clinic_id=None,
        lock=False: invoice,
    )

    monkeypatch.setattr(
        billing_service,
        "_find_duplicate_gateway_payment",
        lambda invoice_id,
        transaction_id: duplicate,
    )

    with app.app_context():
        with pytest.raises(
            ConflictError,
            match=(
                "successful payment with this "
                "gateway transaction ID"
            ),
        ):
            billing_service.record_payment(
                clinic_id=10,
                invoice_id=100,
                amount="50.00",
                method=PaymentMethod.CARD,
                gateway=PaymentGateway.PAYSTACK,
                gateway_transaction_id="TX-123",
            )


def test_record_payment_rejects_overpayment(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice(
        total=Decimal("100.00"),
        paid=Decimal("75.00"),
    )

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        with pytest.raises(
            ValidationError,
            match=(
                "Payment amount exceeds the "
                "remaining invoice balance"
            ),
        ):
            billing_service.record_payment(
                clinic_id=10,
                invoice_id=100,
                amount="30.00",
                method=PaymentMethod.CASH,
            )


def test_record_payment_gateway_success(
    app,
    monkeypatch,
):
    invoice = _make_payment_invoice()

    patch_payment_invoice(
        monkeypatch,
        invoice,
    )

    with app.app_context():
        payment = billing_service.record_payment(
            clinic_id=10,
            invoice_id=100,
            amount="100.00",
            method="card",
            gateway="paystack",
            gateway_transaction_id=" TX-123 ",
        )

        assert payment.amount == Decimal(
            "100.00"
        )
        assert payment.method == (
            PaymentMethod.CARD
        )
        assert payment.gateway == (
            PaymentGateway.PAYSTACK
        )
        assert (
            payment.gateway_transaction_id
            == "TX-123"
        )
        assert payment.status == (
            PaymentStatus.SUCCESSFUL
        )
        assert invoice.status == (
            InvoiceStatus.PAID
        )


# ============================================================================
# get_outstanding_invoices
# ============================================================================


def test_get_outstanding_invoices_returns_page(
    app,
    monkeypatch,
):
    invoices = [
        SimpleNamespace(id=1),
        SimpleNamespace(id=2),
    ]

    page = FakePage(
        invoices,
        page=1,
        per_page=50,
        total=2,
    )

    query = FakeQuery(
        results=invoices,
        page=page,
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    with app.app_context():
        result = (
            billing_service.get_outstanding_invoices(
                clinic_id=10,
            )
        )

    assert result is page
    assert result.items == invoices
    assert result.page == 1
    assert result.per_page == 50
    assert result.total == 2
    assert query.paginate_calls == [
        {
            "page": 1,
            "per_page": 50,
            "error_out": False,
        }
    ]


def test_get_outstanding_invoices_accepts_pagination(
    app,
    monkeypatch,
):
    page = FakePage(
        [
            SimpleNamespace(id=51),
        ],
        page=3,
        per_page=25,
        total=101,
    )

    query = FakeQuery(
        page=page
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    with app.app_context():
        result = (
            billing_service.get_outstanding_invoices(
                clinic_id=10,
                page=3,
                per_page=25,
            )
        )

    assert result is page
    assert query.paginate_calls == [
        {
            "page": 3,
            "per_page": 25,
            "error_out": False,
        }
    ]


def test_get_outstanding_invoices_supports_last_page(
    app,
    monkeypatch,
):
    page = FakePage(
        [
            SimpleNamespace(id=101),
        ],
        page=3,
        per_page=50,
        total=101,
    )

    query = FakeQuery(
        page=page
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    with app.app_context():
        result = (
            billing_service.get_outstanding_invoices(
                clinic_id=10,
                page=3,
                per_page=50,
            )
        )

    assert result.items[0].id == 101
    assert result.has_next is False
    assert result.has_prev is True


def test_get_outstanding_invoices_returns_empty_page(
    app,
    monkeypatch,
):
    page = FakePage(
        [],
        page=1,
        per_page=50,
        total=0,
    )

    query = FakeQuery(
        page=page
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    with app.app_context():
        result = (
            billing_service.get_outstanding_invoices(
                clinic_id=10,
                page=1,
                per_page=50,
            )
        )

    assert result.items == []
    assert result.total == 0
    assert result.pages == 0
    assert result.has_next is False
    assert result.has_prev is False


@pytest.mark.parametrize(
    "page,per_page",
    [
        (0, 50),
        (1, 0),
        (1, 501),
    ],
)
def test_get_outstanding_invoices_rejects_invalid_pagination(
    app,
    page,
    per_page,
):
    with app.app_context():
        with pytest.raises(
            ValidationError
        ):
            billing_service.get_outstanding_invoices(
                clinic_id=10,
                page=page,
                per_page=per_page,
            )


def test_get_outstanding_invoices_rejects_invalid_clinic_id(
    app,
):
    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be a positive integer",
        ):
            billing_service.get_outstanding_invoices(
                clinic_id=0,
            )


def test_get_outstanding_invoices_without_clinic(
    app,
    monkeypatch,
):
    page = FakePage(
        [
            SimpleNamespace(id=1),
        ],
        page=1,
        per_page=50,
        total=1,
    )

    query = FakeQuery(
        page=page
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    with app.app_context():
        result = (
            billing_service.get_outstanding_invoices()
        )

    assert result is page
    assert result.items[0].id == 1


# ============================================================================
# _mark_overdue_invoices
# ============================================================================


def test_mark_overdue_invoices_marks_expired_invoices(
    app,
    monkeypatch,
):
    invoice_one = SimpleNamespace(
        id=1,
        status=InvoiceStatus.ISSUED,
    )

    invoice_two = SimpleNamespace(
        id=2,
        status=InvoiceStatus.PARTIALLY_PAID,
    )

    query = FakeQuery(
        results=[
            invoice_one,
            invoice_two,
        ]
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    monkeypatch.setattr(
        billing_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    class FakeDate:
        @classmethod
        def today(cls):
            return date(2026, 9, 7)

    monkeypatch.setattr(
        billing_service,
        "date",
        FakeDate,
    )

    with app.app_context():
        result = (
            billing_service._mark_overdue_invoices(
                clinic_id=10,
            )
        )

    assert result == 2
    assert (
        invoice_one.status
        == InvoiceStatus.OVERDUE
    )
    assert (
        invoice_two.status
        == InvoiceStatus.OVERDUE
    )


def test_mark_overdue_invoices_without_clinic_is_supported_for_task(
    app,
    monkeypatch,
):
    invoice = SimpleNamespace(
        id=1,
        status=InvoiceStatus.ISSUED,
    )

    query = FakeQuery(
        results=[invoice]
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    monkeypatch.setattr(
        billing_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    with app.app_context():
        result = (
            billing_service._mark_overdue_invoices()
        )

    assert result == 1
    assert invoice.status == (
        InvoiceStatus.OVERDUE
    )


def test_mark_overdue_invoices_returns_zero_when_none_found(
    app,
    monkeypatch,
):
    query = FakeQuery(
        results=[]
    )

    monkeypatch.setattr(
        billing_service.Invoice,
        "query",
        query,
    )

    with app.app_context():
        result = (
            billing_service._mark_overdue_invoices(
                clinic_id=10,
            )
        )

    assert result == 0


def test_mark_overdue_invoices_rejects_invalid_clinic(
    app,
):
    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Clinic ID must be a positive integer",
        ):
            billing_service._mark_overdue_invoices(
                clinic_id=0,
            )


# ============================================================================
# mark_overdue_invoices
# ============================================================================


def test_mark_overdue_invoices_requires_clinic_id(
    app,
):
    with app.app_context():
        with pytest.raises(
            ValidationError,
            match="Clinic ID is required",
        ):
            billing_service.mark_overdue_invoices()


def test_mark_overdue_invoices_enforces_active_clinic(
    app,
    monkeypatch,
):
    captured = {}

    monkeypatch.setattr(
        billing_service,
        "ensure_clinic_active",
        lambda clinic_id: captured.setdefault(
            "clinic_id",
            clinic_id,
        ),
    )

    monkeypatch.setattr(
        billing_service,
        "_mark_overdue_invoices",
        lambda clinic_id=None: 7,
    )

    with app.app_context():
        result = (
            billing_service.mark_overdue_invoices(
                clinic_id=10,
            )
        )

    assert result == 7
    assert captured["clinic_id"] == 10


def test_mark_overdue_invoices_delegates_to_internal_function(
    app,
    monkeypatch,
):
    captured = {}

    monkeypatch.setattr(
        billing_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    def fake_mark_overdue(
        clinic_id=None,
    ):
        captured["clinic_id"] = clinic_id
        return 7

    monkeypatch.setattr(
        billing_service,
        "_mark_overdue_invoices",
        fake_mark_overdue,
    )

    with app.app_context():
        result = (
            billing_service.mark_overdue_invoices(
                clinic_id=10,
            )
        )

    assert result == 7
    assert captured["clinic_id"] == 10


# ============================================================================
# Celery Task
# ============================================================================


def test_mark_overdue_invoices_task_commits(
    monkeypatch,
):
    commit_called = []

    monkeypatch.setattr(
        billing_service,
        "_mark_overdue_invoices",
        lambda: 4,
    )

    monkeypatch.setattr(
        billing_service.db.session,
        "commit",
        lambda: commit_called.append(True),
    )

    result = (
        billing_service.mark_overdue_invoices_task()
    )

    assert result == 4
    assert commit_called == [True]


def test_mark_overdue_invoices_task_rolls_back_on_error(
    monkeypatch,
):
    rollback_called = []

    monkeypatch.setattr(
        billing_service,
        "_mark_overdue_invoices",
        lambda: (_ for _ in ()).throw(
            RuntimeError("database failure")
        ),
    )

    monkeypatch.setattr(
        billing_service.db.session,
        "rollback",
        lambda: rollback_called.append(True),
    )

    with pytest.raises(
        RuntimeError,
        match="database failure",
    ):
        billing_service.mark_overdue_invoices_task()

    assert rollback_called == [True]
