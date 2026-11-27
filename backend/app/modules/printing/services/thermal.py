"""Explicit ESC/POS payment-record copy, not an invoice or automatic spool job."""
from decimal import Decimal
import re
import textwrap
from sqlalchemy import select
from flask import current_app
from app.extensions import db
from app.core.enums.billing_enums import PaymentStatus
from .print_policy import PrintDenied
from .hospital_grade import stamp


def render_payment(*, payment_id, clinic_id, paper):
    if not current_app.testing or current_app.config.get('PRINT_THERMAL_STAGED_ENABLED') is not True or type(paper) is not int or paper not in (58,80):
        raise PrintDenied('Thermal copy unavailable')
    from app.modules.billing.models.billing_model import Payment, Invoice
    pair = db.session.execute(select(Payment,Invoice).join(Invoice,Invoice.id==Payment.invoice_id).where(Payment.id==payment_id,Invoice.clinic_id==clinic_id).with_for_update()).one_or_none()
    if pair is None:
        raise PrintDenied('Thermal copy unavailable')
    payment, invoice = pair
    if payment.status is not PaymentStatus.SUCCESSFUL or payment.paid_at is None or payment.currency != invoice.currency or not isinstance(payment.amount,Decimal) or not payment.amount.is_finite() or payment.amount<=0 or payment.amount>invoice.total_amount or payment.amount>invoice.amount_paid or not re.fullmatch('[A-Z]{3}',payment.currency or ''):
        raise PrintDenied('Thermal copy unavailable')
    lines=['STRYKA-MAX', 'PAYMENT RECORD COPY', 'NOT A BANK SETTLEMENT CERTIFICATE', f'Clinic reference: {clinic_id}', f'Invoice: {invoice.invoice_number}', f'Payment reference: {payment.id}', 'Payment status: successful', f'Paid amount: {payment.currency} {payment.amount:.2f}', 'Recorded paid at: '+stamp(payment.paid_at), 'Powered by Stryka-Max']
    width = 32 if paper==58 else 48
    result=[]
    for line in lines:
        if not isinstance(line,str) or len(line)>300 or any(not 32<=ord(c)<=126 for c in line):
            raise PrintDenied('Unsupported thermal text')
        result.extend(textwrap.wrap(line,width=width,replace_whitespace=False,drop_whitespace=True))
    # Only fixed ESC/POS initialization and plain printable ASCII; no cutter,
    # drawer pulse, user-controlled command, network destination or spool call.
    return b'\x1b@'+('\n'.join(result)+'\n\n\n').encode('ascii')
