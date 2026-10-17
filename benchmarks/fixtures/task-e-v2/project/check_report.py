"""Compare the daily report with finance's figures in expected/daily_totals.csv.

    python check_report.py

Exits 0 when every store and day matches, 1 otherwise, listing each mismatch.
"""

import csv
import sys
from pathlib import Path

from report.aggregate import daily_totals
from report.config import load_stores
from report.ledger import load_transactions
from report.money import format_cents, parse_amount


def expected_totals(path: Path) -> dict:
    with open(path, encoding="utf-8", newline="") as handle:
        return {(row["store"], row["day"]): parse_amount(row["total"]) for row in csv.DictReader(handle)}


def main() -> int:
    stores = load_stores(Path("stores.ini"))
    actual = {
        (store, day.isoformat()): cents
        for store, days in daily_totals(load_transactions(Path("data/transactions.csv")), stores).items()
        for day, cents in days.items()
    }
    expected = expected_totals(Path("expected/daily_totals.csv"))
    mismatches = []
    for key in sorted(set(actual) | set(expected)):
        if actual.get(key) != expected.get(key):
            store, day = key
            got = actual.get(key)
            want = expected.get(key)
            mismatches.append("store %s %s: report %s, finance %s" % (
                store, day,
                "missing" if got is None else format_cents(got),
                "missing" if want is None else format_cents(want),
            ))
    if mismatches:
        print("%d mismatch(es):" % len(mismatches))
        for line in mismatches:
            print("  " + line)
        return 1
    print("all %d store-days match finance" % len(expected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
