"""Strict search requests and private, session-bound continuation tokens."""
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import unicodedata

from itsdangerous import BadData, URLSafeTimedSerializer


PATIENT_ROLES = frozenset({'admin', 'doctor', 'nurse', 'receptionist', 'pharmacist', 'lab_technician', 'paramedic', 'emt'})
APPOINTMENT_ROLES = frozenset({'admin', 'doctor', 'nurse', 'receptionist'})
LAB_ROLES = APPOINTMENT_ROLES | {'lab_technician'}
CLINICAL_ROLES = frozenset({'admin', 'doctor', 'nurse'})
PRESCRIPTION_ROLES = frozenset({'doctor', 'pharmacist'})
EMAR_ROLES = frozenset({'doctor', 'nurse'})
ORDER_STATUSES = frozenset({'pending', 'active', 'in_progress', 'on_hold', 'completed', 'cancelled'})

# Ordered intentionally: patient matches precede operational records.
# Statuses are original source lifecycle values, not inferred task urgency.
RESOURCES = {
    'patient': (PATIENT_ROLES, frozenset({'active', 'inactive'})),
    'appointment': (APPOINTMENT_ROLES, frozenset({'scheduled', 'confirmed', 'cancelled', 'completed', 'no_show'})),
    'lab_order': (LAB_ROLES, frozenset({'ordered', 'sample_collected', 'in_progress', 'completed', 'cancelled'})),
    'prescription': (PRESCRIPTION_ROLES, frozenset({'active', 'completed', 'cancelled', 'expired'})),
    'imaging_order': (CLINICAL_ROLES, ORDER_STATUSES),
    'procedure_order': (CLINICAL_ROLES, ORDER_STATUSES),
    'blood_product_order': (CLINICAL_ROLES | {'lab_technician'}, ORDER_STATUSES),
    'diet_order': (CLINICAL_ROLES, ORDER_STATUSES),
    'nursing_order': (CLINICAL_ROLES, ORDER_STATUSES),
    'nursing_task': (CLINICAL_ROLES, frozenset({'pending', 'in_progress', 'completed', 'cancelled'})),
    'medication_administration': (EMAR_ROLES, frozenset({'scheduled', 'due', 'administered', 'held', 'refused', 'missed', 'cancelled'})),
}
RANKS = {kind: i for i, kind in enumerate(RESOURCES)}
MAX_ID = 2147483647
MAX_LIMIT = 50


def available_resources(role):
    return {kind: sorted(statuses) for kind, (roles, statuses) in RESOURCES.items() if role in roles}


@dataclass(frozen=True)
class SearchFilters:
    query: str
    exact_id: int | None
    resource_types: tuple[str, ...] | None
    status: str | None
    limit: int
    cursor: str | None

    @classmethod
    def parse(cls, payload):
        if not isinstance(payload, dict) or set(payload) - {'q', 'resource_types', 'status', 'limit', 'cursor'}:
            raise ValueError('Invalid search request')
        raw = payload.get('q')
        if not isinstance(raw, str) or len(raw) > 120 or any(unicodedata.category(c).startswith('C') for c in raw):
            raise ValueError('Invalid search text')
        query = raw.strip()
        if len(query) < 2:
            raise ValueError('Search requires at least two characters')
        exact_id = None
        if query.startswith('#'):
            if not re.fullmatch(r'#[1-9][0-9]{0,9}', query) or int(query[1:]) > MAX_ID:
                raise ValueError('Invalid exact record ID')
            exact_id = int(query[1:])
        kinds = payload.get('resource_types')
        if kinds is not None:
            if (not isinstance(kinds, list) or not 1 <= len(kinds) <= len(RESOURCES)
                    or any(not isinstance(k, str) or k not in RESOURCES for k in kinds)
                    or len(set(kinds)) != len(kinds)):
                raise ValueError('Invalid resource types')
            kinds = tuple(sorted(kinds, key=RANKS.__getitem__))
        status = payload.get('status')
        if status is not None and (not isinstance(status, str) or not any(status in s for _, s in RESOURCES.values())):
            raise ValueError('Invalid status')
        limit = payload.get('limit', 25)
        if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
            raise ValueError('Invalid limit')
        cursor = payload.get('cursor')
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048):
            raise ValueError('Invalid continuation')
        return cls(query, exact_id, kinds, status, limit, cursor)

    def binding(self, secret, *, user_id, tenant_id, clinic_id, role, token_version, jti, kinds):
        # The cursor is signed, not encrypted. Never put query text or PHI in it.
        body = json.dumps([user_id, tenant_id, clinic_id, role, token_version, jti,
                           self.query, list(kinds), self.status, self.limit], ensure_ascii=False, separators=(',', ':'))
        key = secret.encode('utf-8') if isinstance(secret, str) else secret
        return hmac.new(key, ('unified-search-binding-v1:' + body).encode('utf-8'), hashlib.sha256).hexdigest()


def _serializer(secret):
    return URLSafeTimedSerializer(secret, salt='unified-clinical-search-v1')


def encode_cursor(secret, binding, rank, record_id):
    return _serializer(secret).dumps([binding, rank, record_id])


def decode_cursor(secret, binding, cursor):
    if cursor is None:
        return None
    try:
        value = _serializer(secret).loads(cursor, max_age=900)
        if (not isinstance(value, list) or len(value) != 3 or not isinstance(value[0], str)
                or not hmac.compare_digest(value[0], binding)
                or type(value[1]) is not int or value[1] not in RANKS.values()
                or type(value[2]) is not int or not 1 <= value[2] <= MAX_ID):
            raise ValueError('Invalid continuation')
        return value[1], value[2]
    except (BadData, ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Invalid or expired continuation') from exc
