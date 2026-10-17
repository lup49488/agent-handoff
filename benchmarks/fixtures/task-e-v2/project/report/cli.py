"""``python -m report.cli`` — print or export the daily report."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional

from report.aggregate import daily_totals
from report.config import load_stores
from report.ledger import load_transactions
from report.money import format_cents


def report_rows(ledger: Path, stores_path: Path, store: Optional[str] = None) -> List[dict]:
    """One row per store and business day, sorted."""
    stores = load_stores(stores_path)
    totals = daily_totals(load_transactions(ledger), stores)
    rows = []
    for name in sorted(totals):
        if store is not None and name != store:
            continue
        for day, cents in sorted(totals[name].items()):
            rows.append({"store": name, "day": day.isoformat(), "total": format_cents(cents),
                         "currency": stores[name].currency})
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=Path("data/transactions.csv"))
    parser.add_argument("--stores", type=Path, default=Path("stores.ini"))
    parser.add_argument("--store", help="only this store")
    parser.add_argument("--csv", type=Path, help="write the report here instead of printing it")
    args = parser.parse_args(argv)
    rows = report_rows(args.ledger, args.stores, args.store)
    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("store", "day", "total", "currency"))
            writer.writeheader()
            writer.writerows(rows)
        print("wrote %d rows to %s" % (len(rows), args.csv))
        return 0
    for row in rows:
        print("%-3s %s %12s %s" % (row["store"], row["day"], row["total"], row["currency"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
