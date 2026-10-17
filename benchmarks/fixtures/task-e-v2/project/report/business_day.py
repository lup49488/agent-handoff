"""Business days.

The stores trade past midnight, so a calendar day is the wrong unit. A
business day starts at the store's ``day_cutoff_hour`` (local time) and runs
until the same hour the next morning: with a cutoff of 4, a sale at 01:30 on
the 14th belongs to the business day of the 13th.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def business_day(local_moment: datetime, cutoff_hour: int) -> date:
    """The business day a store-local moment belongs to."""
    if not 0 <= cutoff_hour <= 23:
        raise ValueError("day_cutoff_hour must be 0-23, got %r" % cutoff_hour)
    day = local_moment.date()
    if local_moment.hour < cutoff_hour:
        day -= timedelta(days=1)
    return day


def days_between(first: date, last: date) -> list:
    """Every business day from ``first`` to ``last`` inclusive."""
    if last < first:
        raise ValueError("last day %s is before first day %s" % (last, first))
    span = (last - first).days
    return [first + timedelta(days=offset) for offset in range(span + 1)]
