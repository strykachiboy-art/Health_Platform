"""Pure, deny-first shared print contract. Not a signature, release or retention engine.

The caller must establish authorization through canonical source records BEFORE
calling build_document(). All documents are bounded, explicitly marked, and
created from a trusted server-side snapshot, never client-supplied arbitrary text.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping

_ALLOWED = {
    'invoice': frozenset(('ADMIN', 'ACCOUNTANT')),
    'appointment': frozenset(('ADMIN', 'RECEPTIONIST', 'DOCTOR')),
    'lab_order': frozenset(('ADMIN', 'LAB_TECHNICIAN')),
    'released_lab_result': frozenset(('PATIENT',)),
    'staged_radiology_copy': frozenset(('PATIENT',)),
    'radiology_draft': frozenset(('DOCTOR',)),
}
_WATERMARK = {
    'invoice': 'BILLING DOCUMENT COPY - NOT A PAYMENT RECEIPT',
    'appointment': 'APPOINTMENT INFORMATION - NOT PROOF OF ATTENDANCE',
    'lab_order': 'SPECIMEN / LAB ORDER ONLY - NO VERIFIED RESULT',
    'released_lab_result': 'PATIENT COPY OF RELEASED LAB REPORT - NOT A SIGNED ORIGINAL',
    'radiology_draft': 'UNSIGNED WORKING NOTE - NOT FOR DIAGNOSIS OR CHART RELEASE',
    'staged_radiology_copy': 'SYNTHETIC TEST COPY - NOT FOR DIAGNOSIS OR CHART RELEASE',
}
_FIELDS = {
    'invoice': ('invoice_number', 'patient_id', 'status', 'currency', 'total_amount', 'amount_paid'),
    'appointment': ('patient_id', 'assigned_staff_id', 'status', 'scheduled_start', 'scheduled_end'),
    'lab_order': ('patient_id', 'status', 'created_at', 'sample_collected_at'),
    'released_lab_result': ('patient_id','verified_at','completed_at','released_at','report_text'),
    'staged_radiology_copy': ('patient_id','released_at','report_text'),
    'radiology_draft': ('imaging_order_id', 'accession_number', 'report_revision', 'report_text'),
}
_MAX_TEXT = {'invoice': 300, 'appointment': 300, 'lab_order': 300, 'radiology_draft': 12000, 'released_lab_result': 12000, 'staged_radiology_copy': 12000}

class PrintDenied(ValueError):
    """Deliberately generic authorization/validation denial, safe for 404 response."""


def _positive(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise PrintDenied('Untrusted print request')
    return value


def _safe_text(value: object, limit: int) -> str:
    if value is None:
        return '-'
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PrintDenied('Untrusted print snapshot')
    raw = str(value)
    if not raw or len(raw) > limit or any(ord(c)<32 and c not in '\n\t' for c in raw):
        raise PrintDenied('Untrusted print snapshot')
    if '\u202e' in raw or '\u202d' in raw or '\u202c' in raw:
        raise PrintDenied('Untrusted print snapshot')
    return raw


@dataclass(frozen=True)
class PrintDocument:
    kind: str
    clinic_id: int
    record_id: int
    source_version: int
    title: str
    watermark: str
    fields: tuple[tuple[str, str], ...]
    source_digest: str
    is_signed: bool = False
    released_to_chart: bool = False


def authorize(*, kind: str, actor_clinic_id: int, record_clinic_id: int,
              actor_role: str, actor_staff_id: int | None = None,
              owner_staff_id: int | None = None) -> None:
    if kind not in _ALLOWED or not isinstance(actor_role, str):
        raise PrintDenied('Print unavailable')
    if _positive(actor_clinic_id) != _positive(record_clinic_id):
        raise PrintDenied('Print unavailable')
    if actor_role not in _ALLOWED[kind]:
        raise PrintDenied('Print unavailable')
    if kind in ('radiology_draft',) or (kind == 'appointment' and actor_role == 'DOCTOR'):
        if (_positive(actor_staff_id) != _positive(owner_staff_id)):
            raise PrintDenied('Print unavailable')


def _validate_invoice_extension(source):
    import json, re
    from decimal import Decimal
    raw=source['invoice_items']
    if not isinstance(raw,str) or len(raw.encode('utf8'))>12000:
        raise PrintDenied('Invalid invoice items')
    try:
        rows=json.loads(raw)
    except (TypeError,ValueError) as exc:
        raise PrintDenied('Invalid invoice items') from exc
    if not isinstance(rows,list) or len(rows)>100 or json.dumps(rows,ensure_ascii=False,separators=(',',':'))!=raw:
        raise PrintDenied('Invalid invoice items')
    def amount(value):
        if not isinstance(value,str) or re.fullmatch(r'[0-9]{1,8}\.[0-9]{2}',value) is None:
            raise PrintDenied('Invalid invoice amount')
        return Decimal(value)
    subtotal=Decimal('0')
    for row in rows:
        if not isinstance(row,list) or len(row)!=4 or any(not isinstance(v,str) for v in row):
            raise PrintDenied('Invalid invoice item')
        description,quantity,price,total=row
        if not description.strip() or len(description)>255 or any(ord(c)<32 or ord(c)==127 for c in description) or re.fullmatch(r'[1-9][0-9]{0,9}',quantity) is None or int(quantity)>2147483647:
            raise PrintDenied('Invalid invoice item')
        if amount(price)*int(quantity)!=amount(total):
            raise PrintDenied('Invalid invoice subtotal')
        subtotal+=amount(total)
    total,paid,balance=map(amount,(source['total_amount'],source['amount_paid'],source['outstanding_balance']))
    if paid>total or balance!=total-paid or (rows and subtotal!=total):
        raise PrintDenied('Invalid invoice totals')


def build_document(*, kind: str, clinic_id: int, record_id: int,
                   source_version: int, source: Mapping[str, object]) -> PrintDocument:
    # No user identity, role or clinic from HTTP is used here; route calls
    # authorize() before retrieving a canonical source snapshot.
    if kind not in _ALLOWED or not isinstance(source, Mapping):
        raise PrintDenied('Unsupported print source')
    _positive(clinic_id); _positive(record_id)
    if type(source_version) is not int or source_version < 0 or (kind in ('radiology_draft','released_lab_result','staged_radiology_copy') and source_version==0):
        raise PrintDenied('Unknown source version')
    names = _FIELDS[kind]
    if kind == 'invoice' and set(source) == set(names) | {'invoice_items', 'outstanding_balance'}:
        names = (*names, 'invoice_items', 'outstanding_balance')
    if set(source) != set(names):
        raise PrintDenied('Unexpected or missing source fields')
    if 'invoice_items' in names:
        _validate_invoice_extension(source)
    fields = tuple((key.replace('_', ' ').title(),
                    _safe_text(source[key], _MAX_TEXT[kind] if key == 'report_text' else 12000 if key == 'invoice_items' else 300))
                   for key in names)
    canonical = '|'.join(f'{len(k)}:{k}:{len(v)}:{v}' for k,v in fields)
    digest = sha256((f'{kind}|{clinic_id}|{record_id}|{source_version}|'+canonical).encode('utf8')).hexdigest()
    return PrintDocument(kind, clinic_id, record_id, source_version,
        kind.replace('_',' ').title(), _WATERMARK[kind], fields, digest)


def supported_kinds() -> tuple[str, ...]:
    # Patient-only print contract is not exposed via the generic staff endpoint.
    return tuple(sorted(k for k in _ALLOWED if k not in ('released_lab_result','staged_radiology_copy')))
