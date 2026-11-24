"""Single policy for existing clinic opening_time / closing_time columns.

No second hours store, scheduler, login gate, or new database column.
Explicit 24/7 is a reserved midnight/midnight pair. Missing hours remain
UNSPECIFIED and never silently become 24/7. Overnight hours are scheduled.

Extended writes require a separately approved database constraint migration.
"""
from __future__ import annotations

from datetime import time

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
