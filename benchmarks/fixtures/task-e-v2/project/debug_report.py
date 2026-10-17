"""Show how each transaction is placed, to investigate a store's totals.

    python debug_report.py --store B
    python debug_report.py --store B --day 2026-09-13

For each transaction: its till, its UTC time, the store-local time the report
computes, the day it is booked to, and its signed amount.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

from report.aggregate import sale_day
from report.config import load_stores
from report.ledger import load_transactions
from report.money import format_cents
from report.refunds import refund_day
from report.timezones import to_local


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    parser.add_argument("--day", help="only transactions booked to this day (YYYY-MM-DD)")
    args = parser.parse_args()

    stores = load_stores(Path("stores.ini"))
    if args.store not in stores:
        print("unknown store %s; known: %s" % (args.store, ", ".join(sorted(stores))))
        return 2
    store = stores[args.store]
    only = date.fromisoformat(args.day) if args.day else None
    print("store %s: offset %s, business day starts %02d:00 local, %s" % (
        store.name, store.utc_offset, store.day_cutoff_hour, store.currency))
    print("%-7s %-4s %-6s %-17s %-17s %-10s %10s" % ("id", "till", "kind", "utc", "local", "booked to", "amount"))
    for txn in load_transactions(Path("data/transactions.csv")):
        if txn.store != store.name:
            continue
        day = sale_day(txn, store) if txn.kind == "sale" else refund_day(txn, store)
        if only is not None and day != only:
            continue
        local = to_local(txn.utc, store.utc_offset)
        print("%-7s %-4s %-6s %-17s %-17s %-10s %10s" % (
            txn.id, txn.terminal, txn.kind, txn.utc.strftime("%Y-%m-%d %H:%M"), local.strftime("%Y-%m-%d %H:%M"),
            day.isoformat(), format_cents(txn.signed_cents)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
