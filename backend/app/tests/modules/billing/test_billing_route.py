import pytest

from decimal import Decimal

from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentMethod,
    PaymentStatus,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
)


class FakePage:
    def __init__(
        self,
        items,
        page=1,
        per_page=50,
        total=None,
        pages=None,
        has_next=False,
        has_prev=False,
    ):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = len(items) if total is None else total
        self.pages = (
            0
            if self.total == 0
            else (
                (self.total + self.per_page - 1) // self.per_page
                if pages is None
                else pages
            )
        )
        self.has_next = has_next
        self.has_prev = has_prev


def _admin_headers(
    app,
    make_user,
    auth_headers_for,
    clinic,
):
    user = make_user(
        clinic=clinic,
        role=Role.ADMIN,
    )

    return auth_headers_for(user)


def _invoice_response(
    invoice_id=1,
    clinic_id=1,
    patient_id=10,
    appointment_id=None,
    invoice_number="INV-1-20260907-ABC12345",
    currency="NGN",
    total_amount="100.00",
    amount_paid="0.00",
    status=InvoiceStatus.ISSUED,
    due_date=None,
    is_insurance_claim=False,
    insurance_provider=None,
):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    return Invoice(
        id=invoice_id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        invoice_number=invoice_number,
        currency=currency,
        total_amount=total_amount,
        amount_paid=amount_paid,
        status=status,
        due_date=due_date,
        is_insurance_claim=is_insurance_claim,
        insurance_provider=insurance_provider,
        created_at=now,
        updated_at=now,
    )


def _payment_response(
    payment_id=1,
    invoice_id=1,
    amount="50.00",
    method=PaymentMethod.CASH,
    status=PaymentStatus.SUCCESSFUL,
    gateway=None,
    reference=None,
    gateway_transaction_id=None,
    failure_reason=None,
    paid_at=None,
):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    return Payment(
        id=payment_id,
        invoice_id=invoice_id,
        amount=amount,
        method=method,
        status=status,
        gateway=gateway,
        reference=reference,
        gateway_transaction_id=gateway_transaction_id,
        failure_reason=failure_reason,
        created_at=now,
        updated_at=now,
        paid_at=paid_at or now,
    )


# ---------------------------------------------------------------------------
# Authentication / authorization
# ---------------------------------------------------------------------------


def test_billing_endpoints_require_authentication(
    client,
):
    endpoints = [
        (
            "post",
            "/api/v1/billing/invoices",
            {
                "patient_id": 1,
                "items": [
                    {
                        "description": "Consultation",
                        "quantity": 1,
                        "unit_price": "100.00",
                    }
                ],
            },
        ),
        (
            "get",
            "/api/v1/billing/invoices/outstanding",
            None,
        ),
        (
            "post",
            "/api/v1/billing/payments",
            {
                "invoice_id": 1,
                "amount": "50.00",
                "method": "cash",
            },
        ),
        (
            "post",
            "/api/v1/billing/invoices/mark-overdue",
            None,
        ),
    ]

    for method, path, payload in endpoints:
        response = getattr(client, method)(
            path,
            json=payload,
        )

        assert response.status_code in (401, 422)


@pytest.mark.parametrize(
    "method,path,payload",
    [
        (
            "post",
            "/api/v1/billing/invoices",
            {
                "patient_id": 20,
                "items": [
                    {
                        "description": "Consultation",
                        "quantity": 1,
                        "unit_price": "100.00",
                    }
                ],
            },
        ),
        (
            "get",
            "/api/v1/billing/invoices/outstanding",
            None,
        ),
        (
            "post",
            "/api/v1/billing/payments",
            {
                "invoice_id": 100,
                "amount": "50.00",
                "method": "cash",
            },
        ),
        (
            "post",
            "/api/v1/billing/invoices/mark-overdue",
            None,
        ),
    ],
)
def test_billing_endpoints_reject_non_admin(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    method,
    path,
    payload,
):
    user = make_user(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    headers = auth_headers_for(user)

    response = getattr(client, method)(
        path,
        headers=headers,
        json=payload,
    )

    assert response.status_code == 403


def test_authenticated_user_without_clinic_is_rejected(
    app,
    client,
    make_user,
    auth_headers_for,
):
    user = make_user(
        role=Role.ADMIN,
    )

    headers = auth_headers_for(user)

    response = client.get(
        "/api/v1/billing/invoices/outstanding",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "clinic" in body["error"].lower()


# ---------------------------------------------------------------------------
# Create invoice
# ---------------------------------------------------------------------------


def test_create_invoice_success(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    invoice = _invoice_response(
        invoice_id=1,
        clinic_id=clinic.id,
        patient_id=20,
        appointment_id=30,
        total_amount="200.00",
        amount_paid="0.00",
        status=InvoiceStatus.ISSUED,
        is_insurance_claim=True,
        insurance_provider="Test Insurance",
    )

    captured = {}

    def fake_create_invoice(**kwargs):
        captured.update(kwargs)
        return invoice

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.create_invoice",
        fake_create_invoice,
    )

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": 20,
            "appointment_id": 30,
            "due_date": "2026-09-30",
            "is_insurance_claim": True,
            "insurance_provider": "Test Insurance",
            "items": [
                {
                    "description": "Consultation",
                    "quantity": 2,
                    "unit_price": "100.00",
                }
            ],
        },
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == 1
    assert body["data"]["clinic_id"] == clinic.id
    assert body["data"]["patient_id"] == 20
    assert body["data"]["appointment_id"] == 30
    assert body["data"]["total_amount"] == "200.00"
    assert body["data"]["amount_paid"] == "0.00"
    assert body["data"]["status"] == "issued"
    assert body["data"]["is_insurance_claim"] is True
    assert body["data"]["insurance_provider"] == "Test Insurance"

    assert captured["clinic_id"] == clinic.id
    assert captured["patient_id"] == 20
    assert captured["appointment_id"] == 30
    assert captured["is_insurance_claim"] is True
    assert captured["insurance_provider"] == "Test Insurance"

    assert captured["items"] == [
        {
            "description": "Consultation",
            "quantity": 2,
            "unit_price": Decimal("100.00"),
        }
    ]


def test_create_invoice_uses_authenticated_clinic(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    invoice = _invoice_response(
        clinic_id=clinic.id,
        patient_id=20,
    )

    captured = {}

    def fake_create_invoice(**kwargs):
        captured.update(kwargs)
        return invoice

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.create_invoice",
        fake_create_invoice,
    )

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": 20,
            "items": [
                {
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        },
    )

    assert response.status_code == 201
    assert captured["clinic_id"] == clinic.id


def test_create_invoice_rejects_invalid_payload(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": -1,
            "items": [],
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_create_invoice_rejects_missing_items(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": 20,
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False


def test_create_invoice_rejects_invalid_item_quantity(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": 20,
            "items": [
                {
                    "description": "Consultation",
                    "quantity": 0,
                    "unit_price": "100.00",
                }
            ],
        },
    )

    assert response.status_code == 422


def test_create_invoice_rejects_negative_unit_price(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": 20,
            "items": [
                {
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": "-1.00",
                }
            ],
        },
    )

    assert response.status_code == 422


def test_create_invoice_rejects_unknown_field(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": 20,
            "unknown": "blocked",
            "items": [
                {
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        },
    )

    assert response.status_code == 422


def test_create_invoice_rejects_non_object_json(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json=[],
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Request body must be a JSON object"


def test_create_invoice_returns_domain_error(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    def fake_create_invoice(**kwargs):
        raise ValidationError(
            "Patient does not belong to clinic"
        )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.create_invoice",
        fake_create_invoice,
    )

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": 20,
            "items": [
                {
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Patient does not belong to clinic"
    )


# ---------------------------------------------------------------------------
# Outstanding invoices
# ---------------------------------------------------------------------------


def test_get_outstanding_invoices_success(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    invoices = [
        _invoice_response(
            invoice_id=1,
            clinic_id=clinic.id,
            patient_id=10,
            invoice_number="INV-001",
            total_amount="100.00",
            amount_paid="0.00",
            status=InvoiceStatus.ISSUED,
        ),
        _invoice_response(
            invoice_id=2,
            clinic_id=clinic.id,
            patient_id=11,
            invoice_number="INV-002",
            total_amount="200.00",
            amount_paid="50.00",
            status=InvoiceStatus.PARTIALLY_PAID,
        ),
    ]

    captured = {}

    def fake_get_outstanding_invoices(**kwargs):
        captured.update(kwargs)
        return FakePage(
            invoices,
            page=1,
            per_page=50,
            total=2,
            pages=1,
            has_next=False,
            has_prev=False,
        )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.get_outstanding_invoices",
        fake_get_outstanding_invoices,
    )

    response = client.get(
        "/api/v1/billing/invoices/outstanding",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["page"] == 1
    assert body["data"]["per_page"] == 50
    assert body["data"]["total"] == 2
    assert body["data"]["pages"] == 1
    assert body["data"]["has_next"] is False
    assert body["data"]["has_prev"] is False

    assert len(body["data"]["items"]) == 2
    assert body["data"]["items"][0]["invoice_number"] == "INV-001"
    assert body["data"]["items"][1]["invoice_number"] == "INV-002"

    assert captured["clinic_id"] == clinic.id
    assert captured["page"] == 1
    assert captured["per_page"] == 50


def test_get_outstanding_invoices_passes_pagination(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    captured = {}

    def fake_get_outstanding_invoices(**kwargs):
        captured.update(kwargs)
        return FakePage(
            [],
            page=2,
            per_page=10,
            total=30,
            pages=3,
            has_next=True,
            has_prev=True,
        )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.get_outstanding_invoices",
        fake_get_outstanding_invoices,
    )

    response = client.get(
        "/api/v1/billing/invoices/outstanding?page=2&per_page=10",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["page"] == 2
    assert body["data"]["per_page"] == 10
    assert body["data"]["total"] == 30
    assert body["data"]["pages"] == 3
    assert body["data"]["has_next"] is True
    assert body["data"]["has_prev"] is True

    assert captured["clinic_id"] == clinic.id
    assert captured["page"] == 2
    assert captured["per_page"] == 10


@pytest.mark.parametrize(
    "query_string",
    [
        "page=0",
        "page=-1",
        "page=abc",
        "per_page=0",
        "per_page=-1",
        "per_page=501",
        "per_page=abc",
        "page=1&unknown=value",
    ],
)
def test_get_outstanding_invoices_rejects_invalid_query(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    query_string,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.get(
        f"/api/v1/billing/invoices/outstanding?{query_string}",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid query parameters"


def test_get_outstanding_invoices_returns_empty_page(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.get_outstanding_invoices",
        lambda **kwargs: FakePage(
            [],
            page=1,
            per_page=50,
            total=0,
            pages=0,
            has_next=False,
            has_prev=False,
        ),
    )

    response = client.get(
        "/api/v1/billing/invoices/outstanding",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["items"] == []
    assert body["data"]["page"] == 1
    assert body["data"]["per_page"] == 50
    assert body["data"]["total"] == 0
    assert body["data"]["pages"] == 0
    assert body["data"]["has_next"] is False
    assert body["data"]["has_prev"] is False


def test_get_outstanding_invoices_returns_last_page(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    invoice = _invoice_response(
        invoice_id=99,
        clinic_id=clinic.id,
        patient_id=55,
        invoice_number="INV-LAST",
    )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.get_outstanding_invoices",
        lambda **kwargs: FakePage(
            [invoice],
            page=3,
            per_page=1,
            total=3,
            pages=3,
            has_next=False,
            has_prev=True,
        ),
    )

    response = client.get(
        "/api/v1/billing/invoices/outstanding?page=3&per_page=1",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["data"]["page"] == 3
    assert body["data"]["per_page"] == 1
    assert body["data"]["total"] == 3
    assert body["data"]["pages"] == 3
    assert body["data"]["has_next"] is False
    assert body["data"]["has_prev"] is True
    assert len(body["data"]["items"]) == 1
    assert body["data"]["items"][0]["invoice_number"] == "INV-LAST"


def test_get_outstanding_invoices_returns_domain_error(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    def fake_get_outstanding_invoices(**kwargs):
        raise ValidationError(
            "Invalid clinic"
        )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.get_outstanding_invoices",
        fake_get_outstanding_invoices,
    )

    response = client.get(
        "/api/v1/billing/invoices/outstanding",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid clinic"


# ---------------------------------------------------------------------------
# Record payment
# ---------------------------------------------------------------------------


def test_record_payment_success(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    payment = _payment_response(
        payment_id=1,
        invoice_id=100,
        amount="50.00",
        method=PaymentMethod.CASH,
        status=PaymentStatus.SUCCESSFUL,
    )

    captured = {}

    def fake_record_payment(**kwargs):
        captured.update(kwargs)
        return payment

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.record_payment",
        fake_record_payment,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": 100,
            "amount": "50.00",
            "method": "cash",
        },
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["id"] == 1
    assert body["data"]["invoice_id"] == 100
    assert body["data"]["amount"] == "50.00"
    assert body["data"]["method"] == "cash"
    assert body["data"]["status"] == "successful"

    assert captured["clinic_id"] == clinic.id
    assert captured["invoice_id"] == 100
    assert str(captured["amount"]) == "50.00"
    assert captured["method"] == PaymentMethod.CASH


def test_record_payment_with_gateway_success(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    payment = _payment_response(
        payment_id=2,
        invoice_id=100,
        amount="75.00",
        method=PaymentMethod.CARD,
        status=PaymentStatus.SUCCESSFUL,
        gateway=PaymentGateway.PAYSTACK,
        reference="REF-001",
        gateway_transaction_id="TXN-001",
    )

    captured = {}

    def fake_record_payment(**kwargs):
        captured.update(kwargs)
        return payment

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.record_payment",
        fake_record_payment,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": 100,
            "amount": "75.00",
            "method": "card",
            "gateway": "paystack",
            "reference": "REF-001",
            "gateway_transaction_id": "TXN-001",
        },
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["gateway"] == "paystack"
    assert body["data"]["reference"] == "REF-001"
    assert body["data"]["gateway_transaction_id"] == "TXN-001"

    assert captured["clinic_id"] == clinic.id
    assert captured["gateway"] == PaymentGateway.PAYSTACK
    assert captured["gateway_transaction_id"] == "TXN-001"


def test_record_payment_uses_authenticated_clinic(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    payment = _payment_response(
        invoice_id=100,
    )

    captured = {}

    def fake_record_payment(**kwargs):
        captured.update(kwargs)
        return payment

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.record_payment",
        fake_record_payment,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": 100,
            "amount": "50.00",
            "method": "cash",
        },
    )

    assert response.status_code == 201
    assert captured["clinic_id"] == clinic.id


def test_record_payment_rejects_invalid_payload(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": -1,
            "amount": "50.00",
            "method": "cash",
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False


def test_record_payment_rejects_negative_amount(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": 100,
            "amount": "-50.00",
            "method": "cash",
        },
    )

    assert response.status_code == 422


def test_record_payment_rejects_missing_required_fields(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={},
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert "error" in body


def test_record_payment_rejects_unknown_field(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": 100,
            "amount": "50.00",
            "method": "cash",
            "unknown": "blocked",
        },
    )

    assert response.status_code == 422


def test_record_payment_rejects_non_object_json(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json=[],
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Request body must be a JSON object"


def test_record_payment_returns_domain_error(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    def fake_record_payment(**kwargs):
        raise ConflictError(
            "Invoice is already fully paid"
        )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.record_payment",
        fake_record_payment,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": 100,
            "amount": "50.00",
            "method": "cash",
        },
    )

    assert response.status_code == 409

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Invoice is already fully paid"
    )


def test_record_payment_returns_not_found_error(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    def fake_record_payment(**kwargs):
        raise NotFoundError(
            "Invoice 100 not found"
        )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.record_payment",
        fake_record_payment,
    )

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": 100,
            "amount": "50.00",
            "method": "cash",
        },
    )

    assert response.status_code == 404

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invoice 100 not found"


# ---------------------------------------------------------------------------
# Mark overdue
# ---------------------------------------------------------------------------


def test_mark_overdue_invoices_success(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    captured = {}

    def fake_mark_overdue_invoices(**kwargs):
        captured.update(kwargs)
        return 3

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.mark_overdue_invoices",
        fake_mark_overdue_invoices,
    )

    response = client.post(
        "/api/v1/billing/invoices/mark-overdue",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["updated_count"] == 3
    assert captured["clinic_id"] == clinic.id


def test_mark_overdue_invoices_returns_zero(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.mark_overdue_invoices",
        lambda **kwargs: 0,
    )

    response = client.post(
        "/api/v1/billing/invoices/mark-overdue",
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["updated_count"] == 0


def test_mark_overdue_invoices_returns_domain_error(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    def fake_mark_overdue_invoices(**kwargs):
        raise ValidationError(
            "Invalid clinic"
        )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.mark_overdue_invoices",
        fake_mark_overdue_invoices,
    )

    response = client.post(
        "/api/v1/billing/invoices/mark-overdue",
        headers=headers,
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Invalid clinic"
# ---------------------------------------------------------------------------
# Idempotency-Key boundary
# ---------------------------------------------------------------------------


def test_create_invoice_forwards_idempotency_key_and_authenticated_user(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    user = make_user(
        clinic=clinic,
        role=Role.ADMIN,
    )

    headers = auth_headers_for(user)

    invoice = _invoice_response(
        invoice_id=500,
        clinic_id=clinic.id,
        patient_id=20,
    )

    captured = {}

    def fake_create_invoice(**kwargs):
        captured.update(kwargs)
        return invoice

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.create_invoice",
        fake_create_invoice,
    )

    headers = dict(headers)
    headers["Idempotency-Key"] = " billing-invoice-001 "

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": 20,
            "items": [
                {
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        },
    )

    assert response.status_code == 201

    assert captured["idempotency_key"] == (
        "billing-invoice-001"
    )
    assert captured["idempotency_user_id"] == user.id
    assert captured["actor_user_id"] == user.id


def test_create_invoice_rejects_blank_idempotency_key(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    def fail_create_invoice(**kwargs):
        raise AssertionError(
            "create_invoice must not run for blank Idempotency-Key"
        )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.create_invoice",
        fail_create_invoice,
    )

    headers = dict(headers)
    headers["Idempotency-Key"] = "   "

    response = client.post(
        "/api/v1/billing/invoices",
        headers=headers,
        json={
            "currency": "NGN",
            "patient_id": 20,
            "items": [
                {
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        },
    )

    assert response.status_code == 400

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Idempotency-Key cannot be blank"
    )


def test_record_payment_forwards_idempotency_key_and_authenticated_user(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    user = make_user(
        clinic=clinic,
        role=Role.ADMIN,
    )

    headers = auth_headers_for(user)

    payment = _payment_response(
        payment_id=500,
        invoice_id=100,
    )

    captured = {}

    def fake_record_payment(**kwargs):
        captured.update(kwargs)
        return payment

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.record_payment",
        fake_record_payment,
    )

    headers = dict(headers)
    headers["Idempotency-Key"] = " billing-payment-001 "

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": 100,
            "amount": "50.00",
            "method": "cash",
        },
    )

    assert response.status_code == 201

    assert captured["idempotency_key"] == (
        "billing-payment-001"
    )
    assert captured["idempotency_user_id"] == user.id


def test_record_payment_rejects_blank_idempotency_key(
    app,
    client,
    make_user,
    auth_headers_for,
    clinic,
    monkeypatch,
):
    headers = _admin_headers(
        app,
        make_user,
        auth_headers_for,
        clinic,
    )

    def fail_record_payment(**kwargs):
        raise AssertionError(
            "record_payment must not run for blank Idempotency-Key"
        )

    monkeypatch.setattr(
        "app.modules.billing.routes.billing_route.record_payment",
        fail_record_payment,
    )

    headers = dict(headers)
    headers["Idempotency-Key"] = "   "

    response = client.post(
        "/api/v1/billing/payments",
        headers=headers,
        json={
            "invoice_id": 100,
            "amount": "50.00",
            "method": "cash",
        },
    )

    assert response.status_code == 400

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Idempotency-Key cannot be blank"
    )
