"""Single policy for existing clinic opening_time / closing_time columns.

No second hours store, scheduler, login gate, or new database column.
Explicit 24/7 is a reserved midnight/midnight pair. Missing hours remain
UNSPECIFIED and never silently become 24/7. Overnight hours are scheduled.

Extended writes require a separately approved database constraint migration.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIDNIGHT = time(0, 0)
ALLOWED_MODES = frozenset({"unspecified", "always_open", "scheduled"})


def describe_operating_mode(opening_time: time | None, closing_time: time | None) -> str:
    if opening_time is None or closing_time is None:
        return "unspecified"
    if opening_time == MIDNIGHT and closing_time == MIDNIGHT:
        return "always_open"
    if opening_time != closing_time:
        return "scheduled"
    # Fail conservative for invalid legacy data; never claim 24/7.
    return "unspecified"


def normalize_explicit_operating_mode(
    opening_time: time | None,
    closing_time: time | None,
    operating_mode: str,
) -> tuple[time | None, time | None]:
    if operating_mode not in ALLOWED_MODES:
        raise ValueError("Invalid clinic operating mode")
    if (opening_time is not None and not isinstance(opening_time, time)) or (
        closing_time is not None and not isinstance(closing_time, time)
    ):
        raise ValueError("Opening and closing times must be valid times")
    if any(clock is not None and clock.tzinfo is not None
           for clock in (opening_time, closing_time)):
        raise ValueError("Clinic hours must be local wall-clock times without offsets")
    if operating_mode == "unspecified":
        if opening_time is not None or closing_time is not None:
            raise ValueError("Unspecified hours cannot include opening or closing times")
        return None, None
    if operating_mode == "always_open":
        if opening_time is not None or closing_time is not None:
            raise ValueError("24/7 mode does not accept opening or closing times")
        return MIDNIGHT, MIDNIGHT
    # Explicit scheduled mode requires both times, including overnight shifts.
    if opening_time is None or closing_time is None or opening_time == closing_time:
        raise ValueError("Scheduled hours require distinct opening and closing times")
    return opening_time, closing_time


def _utc(value: datetime) -> datetime:
    """Existing appointment storage uses naive UTC; offset inputs retain their instant."""
    if not isinstance(value, datetime):
        raise ValueError("Appointment time must be a datetime")
    if value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _boundary(day, clock, zone, *, opening):
    # Reject nonexistent wall-clock boundaries. For repeated DST wall times,
    # use the later opening and earlier closing (conservative window).
    wall = datetime.combine(day, clock)
    candidates = set()
    for fold in (0, 1):
        instant = wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None) == wall:
            candidates.add(instant)
    if not candidates:
        return None
    return max(candidates) if opening else min(candidates)


def _windows(start, opening, closing, zone, days=3):
    local_day = start.astimezone(zone).date()
    for offset in range(-1, days + 1):
        day = local_day + timedelta(days=offset)
        end_day = day + timedelta(days=int(closing < opening))
        first = _boundary(day, opening, zone, opening=True)
        last = _boundary(end_day, closing, zone, opening=False)
        if first is not None and last is not None and first < last:
            yield first, last


def _zone(name):
    if not isinstance(name, str) or not name:
        raise ValueError("Clinic timezone is not configured")
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Clinic timezone is invalid") from exc


def appointment_within_hours(opening, closing, timezone_name, start, end):
    """None = unspecified; true = allowed by hours only; false = outside window.

    Start inclusive; end may equal closing. A booking must fit one continuous
    operating window. Does not authorize a user or replace staff conflict checks.
    """
    first, last = _utc(start), _utc(end)
    if last <= first:
        raise ValueError("Appointment end must be later than start")
    mode = describe_operating_mode(opening, closing)
    if mode == "unspecified":
        return None
    if mode == "always_open":
        return True
    zone = _zone(timezone_name)
    return any(a <= first and last <= b for a, b in _windows(first, opening, closing, zone, days=0))


def operating_status(opening, closing, timezone_name, *, now=None):
    """Derived display metadata only. Unknown schedule/timezone remains unknown."""
    mode = describe_operating_mode(opening, closing)
    result = {"operating_mode": mode, "is_open_now": None, "next_opening_at": None}
    if mode == "unspecified":
        return result
    if mode == "always_open":
        result["is_open_now"] = True
        return result
    try:
        zone = _zone(timezone_name)
        instant = _utc(now if now is not None else datetime.now(timezone.utc))
        windows = list(_windows(instant, opening, closing, zone))
    except (ValueError, OverflowError):
        return result
    result["is_open_now"] = any(a <= instant < b for a, b in windows)
    if not result["is_open_now"]:
        upcoming = [a for a, _ in windows if a > instant]
        if upcoming:
            result["next_opening_at"] = min(upcoming).isoformat()
    return result


def normalize_booking_interval(start, end):
    """Convert explicit offsets to the existing naive-UTC storage convention.

    Used only by the staged booking enforcement path. No historical rewrite.
    """
    first, last = _utc(start), _utc(end)
    if last <= first:
        raise ValueError("Appointment end must be later than start")
    return first.replace(tzinfo=None), last.replace(tzinfo=None)
