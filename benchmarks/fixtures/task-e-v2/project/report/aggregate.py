"""Daily totals per store: sales by business day, less refunds by booking day."""

from __future__ import annotations

from datetime import date
from typing import Dict, Iterable

from report.business_day import business_day
from report.config import StoreSettings
from report.ledger import Transaction
from report.money import total
from report.refunds import check_refunds, refunds_by_day
from report.timezones import to_local


def sale_day(sale: Transaction, store: StoreSettings) -> date:
    """The business day a sale belongs to, in the store's own local time."""
    return business_day(to_local(sale.utc, store.utc_offset), store.day_cutoff_hour)


def daily_totals(transactions: Iterable[Transaction], stores: Dict[str, StoreSettings]) -> Dict[str, Dict[date, int]]:
    """``{store: {day: cents}}`` for every store and day with any activity."""
    transactions = list(transactions)
    unknown = sorted({txn.store for txn in transactions} - set(stores))
    if unknown:
        raise ValueError("transactions for stores with no settings: %s" % ", ".join(unknown))
    check_refunds(transactions)

    sales: Dict[str, Dict[date, list]] = {}
    for txn in transactions:
        if txn.kind == "sale":
            day = sale_day(txn, stores[txn.store])
            sales.setdefault(txn.store, {}).setdefault(day, []).append(txn.amount_cents)

    refunds = refunds_by_day(transactions, stores)
    totals: Dict[str, Dict[date, int]] = {}
    for store in sorted(set(sales) | set(refunds)):
        days = set(sales.get(store, {})) | set(refunds.get(store, {}))
        totals[store] = {}
        for day in sorted(days):
            sold = total(sales.get(store, {}).get(day, []))
            refunded = total(r.amount_cents for r in refunds.get(store, {}).get(day, []))
            totals[store][day] = sold - refunded
    return totals
