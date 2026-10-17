"""Store-local time.

Stores report in their own local time, but the ledger is kept in UTC. Offsets
come from ``stores.ini`` as ``+HH:MM`` or ``-HH:MM``.

The standard library's fixed-offset ``timezone`` is used rather than
``zoneinfo``: none of the stores observes daylight saving, and ``zoneinfo``
needs a tz database that a stock Windows Python does not ship.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache

_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")

#: Real-world offsets run from -12:00 to +14:00.
_MAX_HOURS = 14


def parse_offset(text: str) -> timedelta:
    """``"+05:45"`` -> ``timedelta(hours=5, minutes=45)``."""
    match = _OFFSET.match(text.strip())
    if match is None:
        raise ValueError("bad UTC offset: %r (expected +HH:MM or -HH:MM)" % text)
    sign = -1 if match.group(1) == "-" else 1
    hours, minutes = int(match.group(2)), int(match.group(3))
    if hours > _MAX_HOURS or minutes >= 60:
        raise ValueError("UTC offset out of range: %r" % text)
    return sign * timedelta(hours=hours, minutes=minutes)


@lru_cache(maxsize=None)
def zone_for(offset_text: str) -> timezone:
    """A fixed-offset zone for one store; cached, as every transaction asks."""
    return timezone(parse_offset(offset_text))


def parse_utc(text: str) -> datetime:
    """Read a ledger timestamp such as ``2026-09-14T07:15:00Z``.

    Timestamps without a zone are rejected rather than assumed to be UTC: a
    naive timestamp in the ledger has always meant an import bug upstream.
    """
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("ledger timestamp has no zone: %r" % text)
    return moment.astimezone(timezone.utc)


def to_local(moment: datetime, offset_text: str) -> datetime:
    """The same instant, as a clock in the store would show it."""
    if moment.tzinfo is None:
        raise ValueError("to_local needs an aware datetime")
    return moment.astimezone(zone_for(offset_text))


def describe(offset_text: str) -> str:
    """``"-03:30"`` -> ``"UTC-03:30"``, for reports and debugging output."""
    delta = parse_offset(offset_text)
    total = int(delta.total_seconds() // 60)
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return "UTC%s%02d:%02d" % (sign, total // 60, total % 60)
