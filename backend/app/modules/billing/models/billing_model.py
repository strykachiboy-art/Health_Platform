from datetime import datetime, timezone

from app.extensions import db
from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentMethod,
    PaymentStatus,
)


def _utcnow():
    return datetime.now(timezone.utc)


class Invoice(db.Model):
    __tablename__ = "invoices"

    __table_args__ = (
        db.Index(
            "ix_invoices_clinic_status_due",
            "clinic_id",
            "status",
            "due_date",
            "id",
        ),
        db.Index(
            "ix_invoices_clinic_patient_created",
            "clinic_id",
            "patient_id",
            "created_at",
            "id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )

    patient_id = db.Column(
        db.Integer,
        db.ForeignKey("patients.id"),
        nullable=False,
        index=True,
    )

    appointment_id = db.Column(
        db.Integer,
        db.ForeignKey("appointments.id"),
        nullable=True,
        index=True,
    )

    invoice_number = db.Column(
        db.String(50),
        unique=True,
        nullable=False,
        index=True,
    )

    # Nullable only for legacy invoices whose currency is unknown.
    currency = db.Column(
        db.String(3),
        nullable=True,
    )

    total_amount = db.Column(
        db.Numeric(10, 2),
        nullable=False,
        default=0,
    )

    amount_paid = db.Column(
        db.Numeric(10, 2),
        nullable=False,
        default=0,
    )

    status = db.Column(
        db.Enum(InvoiceStatus),
        nullable=False,
        default=InvoiceStatus.ISSUED,
        index=True,
    )

    due_date = db.Column(
        db.Date,
        nullable=True,
        index=True,
    )

    is_insurance_claim = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )

    insurance_provider = db.Column(
        db.String(120),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        index=True,
    )

    clinic = db.relationship(
        "Clinic",
        back_populates="invoices",
    )

    patient = db.relationship(
        "Patient",
        back_populates="invoices",
    )

    items = db.relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )

    payments = db.relationship(
        "Payment",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )

    ambulance_trip = db.relationship(
        "AmbulanceTrip",
        back_populates="invoice",
        uselist=False,
    )

    __table_args__ = (
        db.CheckConstraint(
            "total_amount >= 0",
            name="ck_invoices_total_amount_nonnegative",
        ),
        db.CheckConstraint(
            "amount_paid >= 0",
            name="ck_invoices_amount_paid_nonnegative",
        ),
        db.CheckConstraint(
            "amount_paid <= total_amount",
            name="ck_invoices_amount_paid_lte_total",
        ),
        db.Index(
            "ix_invoices_clinic_status_due",
            "clinic_id",
            "status",
            "due_date",
            "id",
        ),
        db.Index(
            "ix_invoices_clinic_patient_created",
            "clinic_id",
            "patient_id",
            "created_at",
            "id",
        ),
    )

    def __repr__(self):
        return (
            f"<Invoice "
            f"{self.invoice_number} - "
            f"{self.status.value}>"
        )


class InvoiceItem(db.Model):
    __tablename__ = "invoice_items"

    __table_args__ = (
        db.CheckConstraint(
            "quantity > 0",
            name="ck_invoice_items_quantity_positive",
        ),
        db.CheckConstraint(
            "unit_price >= 0",
            name="ck_invoice_items_unit_price_nonnegative",
        ),
        db.CheckConstraint(
            "subtotal >= 0",
            name="ck_invoice_items_subtotal_nonnegative",
        ),
        db.Index(
            "ix_invoice_items_invoice_id_id",
            "invoice_id",
            "id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("invoices.id"),
        nullable=False,
        index=True,
    )

    description = db.Column(
        db.String(255),
        nullable=False,
    )

    quantity = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    unit_price = db.Column(
        db.Numeric(10, 2),
        nullable=False,
    )

    subtotal = db.Column(
        db.Numeric(10, 2),
        nullable=False,
    )

    invoice = db.relationship(
        "Invoice",
        back_populates="items",
    )

    def __repr__(self):
        return (
            f"<InvoiceItem "
            f"{self.description} "
            f"x{self.quantity}>"
        )


class Payment(db.Model):
    __tablename__ = "payments"

    __table_args__ = (
        db.CheckConstraint(
            "amount > 0",
            name="ck_payments_amount_positive",
        ),
        db.Index(
            "ix_payments_invoice_status_created",
            "invoice_id",
            "status",
            "created_at",
            "id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("invoices.id"),
        nullable=False,
        index=True,
    )

    amount = db.Column(
        db.Numeric(10, 2),
        nullable=False,
    )

    # Nullable for legacy/manual payment rows whose original currency is unknown.
    # New gateway payment intents always persist their expected currency.
    currency = db.Column(
        db.String(3),
        nullable=True,
    )

    method = db.Column(
        db.Enum(PaymentMethod),
        nullable=False,
        index=True,
    )

    status = db.Column(
        db.Enum(PaymentStatus),
        nullable=False,
        default=PaymentStatus.PENDING,
        index=True,
    )

    gateway = db.Column(
        db.Enum(PaymentGateway),
        nullable=True,
        index=True,
    )

    reference = db.Column(
        db.String(120),
        nullable=True,
        index=True,
    )

    gateway_transaction_id = db.Column(
        db.String(255),
        nullable=True,
        unique=True,
        index=True,
    )

    failure_reason = db.Column(
        db.String(255),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        index=True,
    )

    paid_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    invoice = db.relationship(
        "Invoice",
        back_populates="payments",
    )

    def __repr__(self):
        return (
            f"<Payment "
            f"{self.amount} - "
            f"{self.status.value}>"
        )
class PaymentWebhookEvent(db.Model):
    __tablename__ = "payment_webhook_events"

    __table_args__ = (
        db.UniqueConstraint(
            "clinic_id",
            "gateway",
            "event_id",
            name="uq_payment_webhook_events_scope",
        ),
        db.Index(
            "ix_payment_webhook_events_clinic_created",
            "clinic_id",
            "created_at",
            "id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    clinic_id = db.Column(
        db.Integer,
        db.ForeignKey("clinics.id"),
        nullable=False,
        index=True,
    )

    payment_id = db.Column(
        db.Integer,
        db.ForeignKey("payments.id"),
        nullable=True,
        index=True,
    )

    gateway = db.Column(
        db.String(32),
        nullable=False,
        index=True,
    )

    event_id = db.Column(
        db.String(255),
        nullable=False,
    )

    event_type = db.Column(
        db.String(120),
        nullable=False,
    )

    reference = db.Column(
        db.String(120),
        nullable=True,
    )

    transaction_id = db.Column(
        db.String(255),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        index=True,
    )

    clinic = db.relationship(
        "Clinic",
    )

    payment = db.relationship(
        "Payment",
    )

    def __repr__(self):
        return (
            f"<PaymentWebhookEvent "
            f"{self.gateway}:{self.event_id}>"
        )
