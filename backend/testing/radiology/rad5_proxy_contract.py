"""RAD5 synthetic-only OHIF/DICOMweb read-scope policy.

NOT a production authorization service. Never accept real clinical identities.
The only session identities are caller-independent, generated fixtures A and B.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

UID = re.compile(r'(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))+\Z')
SAFE_STUDY_QUERY = {'StudyInstanceUID', 'includefield', 'limit', 'offset'}


class ScopeDenied(ValueError):
    pass


def is_uid(value: str) -> bool:
    return isinstance(value, str) and len(value) <= 64 and UID.fullmatch(value) is not None


def _valid_fragment(value: str) -> bool:
    return is_uid(value)


def allowed_dicomweb_target(raw_target: str, scoped_study_uid: str) -> str:
    """Return a forwarding-safe path, never a generic upstream URL.

    Only GET operations with an explicit authorized StudyInstanceUID are allowed.
    WADO never allows an instance or series outside the selected study path.
    """
    if not is_uid(scoped_study_uid) or not isinstance(raw_target, str):
        raise ScopeDenied('Invalid scope')
    if len(raw_target) > 1024 or any(ord(c) < 32 for c in raw_target):
        raise ScopeDenied('Invalid target length or characters')
    if '%' in raw_target or '\\' in raw_target or '#' in raw_target or '//' in raw_target:
        raise ScopeDenied('Encoded/ambiguous target prohibited')
    u = urlsplit(raw_target)
    if u.scheme or u.netloc or u.fragment or not u.path.startswith('/dicom-web/'):
        raise ScopeDenied('Non-local DICOMweb route')
    p = u.path[len('/dicom-web/'):].split('/')
    if p == ['studies']:
        try:
            qs = parse_qsl(u.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise ScopeDenied('Query invalid') from exc
        names = [x[0] for x in qs]
        if names.count('StudyInstanceUID') != 1 or any(n not in SAFE_STUDY_QUERY for n in names):
            raise ScopeDenied('QIDO requires exact, unique StudyInstanceUID')
        if any(n != 'includefield' and names.count(n) > 1 for n in names):
            raise ScopeDenied('Duplicate parameter')
        qmap = dict(qs)
        if qmap.get('StudyInstanceUID') != scoped_study_uid:
            raise ScopeDenied('Cross-study enumeration prohibited')
        if 'limit' in qmap and (not qmap['limit'].isdigit() or int(qmap['limit']) > 100):
            raise ScopeDenied('Unbounded QIDO')
        if 'offset' in qmap and (not qmap['offset'].isdigit() or int(qmap['offset']) != 0):
            raise ScopeDenied('QIDO offset prohibited')
        if any(n == 'includefield' and not (v == 'all' or re.fullmatch(r'[0-9A-Fa-f]{8}', v))
               for n, v in qs):
            raise ScopeDenied('Unsafe include field')
        return raw_target
    if len(p) < 2 or p[0] != 'studies' or p[1] != scoped_study_uid:
        raise ScopeDenied('Study not authorized')
    if u.query:
        # No arbitrary wildcard, value selectors, URL redirects or public searches.
        raise ScopeDenied('WADO query not permitted in synthetic gate')
    tail = p[2:]
    if not tail:
        return raw_target
    if tail == ['metadata']:
        return raw_target
    if tail[0] != 'series':
        raise ScopeDenied('Non-study child path')
    tail = tail[1:]
    if not tail:
        return raw_target   # QIDO series list within permitted study
    if not _valid_fragment(tail[0]):
        raise ScopeDenied('Invalid series UID')
    tail = tail[1:]
    if not tail or tail == ['metadata']:
        return raw_target
    if tail[0] != 'instances':
        raise ScopeDenied('Unsupported series route')
    tail = tail[1:]
    if not tail:
        return raw_target
    if not _valid_fragment(tail[0]):
        raise ScopeDenied('Invalid instance UID')
    tail = tail[1:]
    if not tail or tail in (['metadata'], ['rendered']):
        return raw_target
    if len(tail) == 2 and tail[0] == 'frames' and tail[1].isdigit() and 0 < int(tail[1]) <= 4096:
        return raw_target
    raise ScopeDenied('Non-allowlisted DICOMweb resource')


def map_peer_access(subject: str, scopes: dict[str, str]) -> str:
    if subject not in ('A', 'B') or subject not in scopes:
        raise ScopeDenied('Only synthetic fixture identities permitted')
    study_uid = scopes[subject]
    if not is_uid(study_uid):
        raise ScopeDenied('Bad synthetic scope mapping')
    return study_uid
