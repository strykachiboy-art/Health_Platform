"""Strict, bounded timeline filters and scope-bound continuation tokens."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from itsdangerous import URLSafeTimedSerializer, BadData

CATEGORIES = frozenset({'appointments', 'consultations', 'vitals', 'problems', 'laboratory', 'prescriptions', 'medication_administration', 'imaging', 'admissions', 'nursing', 'procedures', 'surgery'})
MAX_LIMIT = 100


def utc(value):
    if not isinstance(value, datetime):
        raise ValueError('Invalid timestamp')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_time(value):
    if not isinstance(value, str) or len(value) > 40 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('Expected ISO-8601 timestamp with offset')
    try:
        return utc(datetime.fromisoformat(value.replace('Z', '+00:00')))
    except (ValueError, OverflowError) as exc:
        raise ValueError('Invalid timestamp') from exc


@dataclass(frozen=True)
class Filters:
    categories: tuple[str, ...]
    start: datetime | None
    end: datetime | None
    limit: int
    cursor: str | None

    @classmethod
    def parse(cls, args):
        if set(args) - {'categories', 'start', 'end', 'limit', 'cursor'} or any(len(args.getlist(k)) != 1 for k in args):
            raise ValueError('Unknown or repeated timeline filter')
        raw = args.get('categories')
        if raw is not None:
            parts = raw.split(',')
            if len(raw) > 256 or len(set(parts)) != len(parts) or not set(parts) <= CATEGORIES:
                raise ValueError('Invalid timeline category')
            categories = tuple(sorted(parts))
        else:
            categories = tuple(sorted(CATEGORIES))
        start = parse_time(args['start']) if 'start' in args else None
        end = parse_time(args['end']) if 'end' in args else None
        if start is not None and end is not None and start >= end:
            raise ValueError('Invalid date range')
        raw_limit = args.get('limit', '50')
        if not re.fullmatch(r'[0-9]{1,3}', raw_limit) or not 1 <= int(raw_limit) <= MAX_LIMIT:
            raise ValueError('Invalid page limit')
        cursor = args.get('cursor')
        if cursor is not None and (not cursor or len(cursor) > 2048):
            raise ValueError('Invalid cursor')
        return cls(categories, start, end, int(raw_limit), cursor)

    def binding(self, *, user_id, organization_id, clinic_id, patient_id, role, token_version):
        return [user_id, str(organization_id), clinic_id, patient_id, role, token_version, list(self.categories), self.start.isoformat() if self.start else None, self.end.isoformat() if self.end else None, self.limit]


def _serializer(secret):
    return URLSafeTimedSerializer(secret, salt='patient-clinical-timeline-v1')


def encode_cursor(secret, binding, timestamp, event_type, event_id):
    return _serializer(secret).dumps([binding, utc(timestamp).isoformat(), event_type, event_id])


def decode_cursor(secret, binding, cursor):
    if cursor is None:
        return None
    try:
        data = _serializer(secret).loads(cursor, max_age=900)
        if (not isinstance(data, list) or len(data) != 4 or data[0] != binding or
                not isinstance(data[2], str) or not re.fullmatch(r'[a-z_]{1,80}', data[2]) or
                type(data[3]) is not int or data[3] <= 0):
            raise ValueError('Invalid timeline continuation')
        return parse_time(data[1]), data[2], data[3]
    except (BadData, ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Invalid or expired timeline continuation') from exc
