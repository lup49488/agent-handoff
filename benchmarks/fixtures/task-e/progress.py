"""Fixture-owned progress evaluator for task E. Run with the target project as cwd.

Kept outside the project so the target cannot edit it, and computed from its
own copy of the ledger and finance's figures, so editing `data/`, `expected/`
or `check_report.py` cannot make it pass.
"""

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from report.aggregate import daily_totals  # noqa: E402
from report.config import StoreSettings  # noqa: E402
from report.ledger import load_transactions  # noqa: E402

STORES = {
    "A": StoreSettings("A", "+01:00", 4, "EUR"),
    "B": StoreSettings("B", "-03:30", 4, "CAD"),
    "C": StoreSettings("C", "+05:45", 4, "NPR"),
    # Not in the project's stores.ini: a second store at a negative offset
    # with minutes, so a fix has to be general rather than about store B.
    "D": StoreSettings("D", "-09:30", 4, "USD"),
}


def totals(rows):
    with tempfile.TemporaryDirectory() as temp:
        ledger = Path(temp) / "ledger.csv"
        with open(ledger, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(["id", "store", "kind", "utc", "amount", "ref"])
            writer.writerows(sorted(rows, key=lambda row: row[3]))
        result = daily_totals(load_transactions(ledger), STORES)
    return {store: {day.isoformat(): cents for day, cents in days.items()} for store, days in result.items()}


# Progress: every *sale* lands on its business day, at every store, including
# one the project does not know about. Refunds are not part of it.
SALES = [['A1', 'A', 'sale', '2026-09-12T10:05:00Z', '64.20', ''],
 ['A2', 'A', 'sale', '2026-09-12T18:40:00Z', '128.00', ''],
 ['A3', 'A', 'sale', '2026-09-13T01:30:00Z', '41.75', ''],
 ['A4', 'A', 'sale', '2026-09-13T13:10:00Z', '89.99', ''],
 ['A6', 'A', 'sale', '2026-09-14T11:00:00Z', '55.00', ''],
 ['A7', 'A', 'sale', '2026-09-15T00:15:00Z', '33.33', ''],
 ['A8', 'A', 'sale', '2026-09-15T09:45:00Z', '72.40', ''],
 ['B1', 'B', 'sale', '2026-09-12T15:30:00Z', '150.00', ''],
 ['B2', 'B', 'sale', '2026-09-13T01:00:00Z', '82.50', ''],
 ['B3', 'B', 'sale', '2026-09-13T06:50:00Z', '47.25', ''],
 ['B4', 'B', 'sale', '2026-09-13T16:45:00Z', '99.00', ''],
 ['B5', 'B', 'sale', '2026-09-14T07:15:00Z', '64.00', ''],
 ['B7', 'B', 'sale', '2026-09-14T21:30:00Z', '120.00', ''],
 ['B10', 'B', 'sale', '2026-09-15T06:40:00Z', '21.00', ''],
 ['B9', 'B', 'sale', '2026-09-15T14:30:00Z', '88.80', ''],
 ['C1', 'C', 'sale', '2026-09-12T04:15:00Z', '300.00', ''],
 ['C2', 'C', 'sale', '2026-09-12T21:25:00Z', '145.50', ''],
 ['C3', 'C', 'sale', '2026-09-13T09:45:00Z', '210.00', ''],
 ['C5', 'C', 'sale', '2026-09-14T18:05:00Z', '99.99', ''],
 ['C6', 'C', 'sale', '2026-09-15T06:15:00Z', '180.00', ''],
 ['D1', 'D', 'sale', '2026-09-13T13:10:00Z', '70.00', ''],
 ['D2', 'D', 'sale', '2026-09-13T21:30:00Z', '40.00', ''],
 ['A9', 'A', 'sale', '2026-09-14T08:00:00Z', '60.00', '']]

EXPECTED = {'A': {'2026-09-12': 23395, '2026-09-13': 8999, '2026-09-14': 14833, '2026-09-15': 7240},
 'B': {'2026-09-12': 27975, '2026-09-13': 16300, '2026-09-14': 14100, '2026-09-15': 8880},
 'C': {'2026-09-12': 44550, '2026-09-13': 21000, '2026-09-14': 9999, '2026-09-15': 18000},
 'D': {'2026-09-12': 7000, '2026-09-13': 4000}}

got = totals(SALES)
if got != EXPECTED:
    raise SystemExit("sales are not all on their business days")
