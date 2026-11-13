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
    'radiology_draft': frozenset(('DOCTOR',)),
}
_WATERMARK = {
    'invoice': 'BILLING DOCUMENT COPY - NOT A PAYMENT RECEIPT',
    'appointment': 'APPOINTMENT INFORMATION - NOT PROOF OF ATTENDANCE',
    'lab_order': 'SPECIMEN / LAB ORDER ONLY - NO VERIFIED RESULT',
    'radiology_draft': 'UNSIGNED WORKING NOTE - NOT FOR DIAGNOSIS OR CHART RELEASE',
}
_FIELDS = {
    'invoice': ('invoice_number', 'patient_id', 'status', 'currency', 'total_amount', 'amount_paid'),
    'appointment': ('patient_id', 'assigned_staff_id', 'status', 'scheduled_start', 'scheduled_end'),
    'lab_order': ('patient_id', 'status', 'created_at', 'sample_collected_at'),
    'radiology_draft': ('imaging_order_id', 'accession_number', 'report_revision', 'report_text'),
}
_MAX_TEXT = {'invoice': 300, 'appointment': 300, 'lab_order': 300, 'radiology_draft': 12000}

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


def build_document(*, kind: str, clinic_id: int, record_id: int,
                   source_version: int, source: Mapping[str, object]) -> PrintDocument:
    # No user identity, role or clinic from HTTP is used here; route calls
    # authorize() before retrieving a canonical source snapshot.
    if kind not in _ALLOWED or not isinstance(source, Mapping):
        raise PrintDenied('Unsupported print source')
    _positive(clinic_id); _positive(record_id)
    if type(source_version) is not int or source_version < 0 or (kind=='radiology_draft' and source_version==0):
        raise PrintDenied('Unknown source version')
    if set(source) != set(_FIELDS[kind]):
        raise PrintDenied('Unexpected or missing source fields')
    fields = tuple((key.replace('_', ' ').title(),
                    _safe_text(source[key], _MAX_TEXT[kind] if key == 'report_text' else 300))
                   for key in _FIELDS[kind])
    canonical = '|'.join(f'{len(k)}:{k}:{len(v)}:{v}' for k,v in fields)
    digest = sha256((f'{kind}|{clinic_id}|{record_id}|{source_version}|'+canonical).encode('utf8')).hexdigest()
    return PrintDocument(kind, clinic_id, record_id, source_version,
        kind.replace('_',' ').title(), _WATERMARK[kind], fields, digest)


def supported_kinds() -> tuple[str, ...]:
    return tuple(sorted(_ALLOWED))
