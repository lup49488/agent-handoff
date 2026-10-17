"""Refunds: checked against the sales they reverse, and booked to a day.

A refund names the sale it reverses in its ``ref`` column. It must be for the
same store and must not exceed what is left of that sale after earlier
refunds.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List

from report.config import StoreSettings
from report.ledger import Transaction


class RefundError(ValueError):
    pass


def check_refunds(transactions: Iterable[Transaction]) -> None:
    """Raise if any refund does not match a sale it can reverse."""
    remaining: Dict[str, int] = {}
    owner: Dict[str, str] = {}
    for txn in sorted(transactions, key=lambda t: t.utc):
        if txn.kind == "sale":
            remaining[txn.id] = txn.amount_cents
            owner[txn.id] = txn.store
            continue
        if txn.ref is None or txn.ref not in remaining:
            raise RefundError("refund %s references unknown sale %r" % (txn.id, txn.ref))
        if owner[txn.ref] != txn.store:
            raise RefundError("refund %s is for store %s but sale %s was at %s"
                              % (txn.id, txn.store, txn.ref, owner[txn.ref]))
        if txn.amount_cents > remaining[txn.ref]:
            raise RefundError("refund %s exceeds what is left of sale %s" % (txn.id, txn.ref))
        remaining[txn.ref] -= txn.amount_cents


def refund_day(refund: Transaction, store: StoreSettings) -> date:
    """The day a refund is booked to.

    Finance books a refund on the day it is issued, not on the day of the
    sale it reverses (NOTES.md). The ledger records when it was issued.
    """
    # TODO(finance): confirm whether refunds should follow the original sale's
    # day instead; issuing day is what the 2025 audit asked for.
    return refund.utc.date()


def refunds_by_day(transactions: Iterable[Transaction], stores: Dict[str, StoreSettings]) -> Dict[str, Dict[date, List[Transaction]]]:
    """Refunds grouped by store, then by the day each is booked to."""
    grouped: Dict[str, Dict[date, List[Transaction]]] = {}
    for txn in transactions:
        if txn.kind != "refund":
            continue
        day = refund_day(txn, stores[txn.store])
        grouped.setdefault(txn.store, {}).setdefault(day, []).append(txn)
    return grouped
