"""Staged issuance ledger. A historical byte match is NOT a clinical release."""
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import re
import secrets
from urllib.parse import urlsplit
from flask import current_app
from app.extensions import db
from .print_policy import PrintDenied

NOTICE = 'Historical issuance only; does not establish current clinical validity, signature, delivery or payment.'


class PrintIssuance(db.Model):
    __tablename__ = 'print_issuances'
    token_sha256 = db.Column(db.String(64), primary_key=True)
    clinic_id = db.Column(db.Integer, db.ForeignKey('clinics.id', ondelete='RESTRICT'), nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    record_id = db.Column(db.Integer, nullable=False)
    source_version = db.Column(db.Integer, nullable=False)
    source_sha256 = db.Column(db.String(64), nullable=False)
    pdf_sha256 = db.Column(db.String(64), nullable=False)
    issued_at = db.Column(db.DateTime(timezone=True), nullable=False)
    __table_args__ = (db.CheckConstraint('record_id > 0 AND source_version >= 0', name='ck_print_issuance_record'),)


def enabled():
    if current_app.config.get('PRINT_VERIFICATION_STAGED_ENABLED') is not True:
        return False
    if not current_app.testing or current_app.config.get('PRINT_BRANDING_STAGED_ENABLED') is not True:
        raise PrintDenied('Document verification unavailable')
    return True


def reserve():
    if not enabled():
        raise PrintDenied('Document verification unavailable')
    origin = current_app.config.get('PRINT_VERIFICATION_ORIGIN')
    if not isinstance(origin, str) or len(origin)>100 or re.fullmatch(r'https://[a-zA-Z0-9.-]+(?::[0-9]{1,5})?', origin) is None:
        raise PrintDenied('Verification origin unavailable')
    try:
        uri = urlsplit(origin)
        if not uri.hostname or '.' not in uri.hostname or uri.port == 0:
            raise ValueError('origin')
    except ValueError as exc:
        raise PrintDenied('Verification origin unavailable') from exc
    token = secrets.token_hex(16)
    return token, origin + '/api/v1/printing/verify/' + token, datetime.now(timezone.utc)


def store(doc, token, data, issued_at):
    if not enabled() or re.fullmatch('[0-9a-f]{32}',token) is None or not isinstance(data,bytes) or not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF') or len(data)>3*1024*1024:
        raise PrintDenied('Invalid document issuance')
    row = PrintIssuance(token_sha256=sha256(token.encode('ascii')).hexdigest(), clinic_id=doc.clinic_id, kind=doc.kind, record_id=doc.record_id,
                        source_version=doc.source_version, source_sha256=doc.source_digest, pdf_sha256=sha256(data).hexdigest(), issued_at=issued_at)
    db.session.add(row)
    db.session.flush()  # The original route commits only after its audit succeeds.
    return row.token_sha256


def lookup(token, candidate=None):
    if not enabled() or not isinstance(token,str) or re.fullmatch('[0-9a-f]{32}',token) is None:
        raise PrintDenied('Document verification unavailable')
    if candidate is not None and (not isinstance(candidate,str) or re.fullmatch('[0-9a-f]{64}',candidate) is None):
        raise PrintDenied('Document verification unavailable')
    row = db.session.get(PrintIssuance, sha256(token.encode('ascii')).hexdigest())
    if row is None:
        raise PrintDenied('Document verification unavailable')
    return {'issuance_record_found':True, 'file_hash_match':None if candidate is None else hmac.compare_digest(row.pdf_sha256,candidate),
            'status':'historical_issuance_only','notice':NOTICE}
