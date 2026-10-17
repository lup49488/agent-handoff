"""Fixture-owned progress evaluator for task E v2. Run with the target project as cwd.

Progress is the sales of every store placed on the right business day: the
public ledger's sales without its re-imported rows, plus store D's. Offsets
must be right; duplicates and refunds do not enter into it.
"""

import csv
import hashlib
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
COLUMNS = ('id', 'store', 'terminal', 'receipt', 'kind', 'utc', 'amount', 'ref')

#: The public ledger as the fixture ships it; editing it cannot make this pass.
PUBLIC_LEDGER_SHA256 = '710f54ec7367ced0a24ea4d01c46b0424e3357fc53c2e95fb2621507584aec3b'


def public_rows():
    data = Path("data/transactions.csv").read_bytes()
    if hashlib.sha256(data).hexdigest() != PUBLIC_LEDGER_SHA256:
        raise SystemExit("data/transactions.csv is not the ledger the fixture ships")
    return list(csv.DictReader(data.decode("utf-8").splitlines()))


def totals(rows):
    with tempfile.TemporaryDirectory() as temp:
        ledger = Path(temp) / "ledger.csv"
        with open(ledger, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda row: (row["utc"], row["id"])))
        result = daily_totals(load_transactions(ledger), STORES)
    return {store: {day.isoformat(): cents for day, cents in days.items()} for store, days in result.items()}


#: Rows a re-import added to the public ledger; left out here.
REIMPORTED = ['T01016', 'T01017', 'T01018', 'T01019', 'T01020', 'T01021', 'T01352', 'T01353', 'T01354']

HIDDEN_SALES = [{'amount': '70.00',
  'id': 'H001',
  'kind': 'sale',
  'receipt': '5001',
  'ref': '',
  'store': 'D',
  'terminal': 'D1',
  'utc': '2026-09-05T12:50:00Z'},
 {'amount': '40.00',
  'id': 'H002',
  'kind': 'sale',
  'receipt': '5002',
  'ref': '',
  'store': 'D',
  'terminal': 'D1',
  'utc': '2026-09-04T22:30:00Z'},
 {'amount': '55.55',
  'id': 'H003',
  'kind': 'sale',
  'receipt': '5003',
  'ref': '',
  'store': 'D',
  'terminal': 'D1',
  'utc': '2026-09-10T13:20:00Z'},
 {'amount': '12.00',
  'id': 'H004',
  'kind': 'sale',
  'receipt': '5004',
  'ref': '',
  'store': 'D',
  'terminal': 'D1',
  'utc': '2026-09-10T05:30:00Z'},
 {'amount': '19.99',
  'id': 'H008',
  'kind': 'sale',
  'receipt': '1327',
  'ref': '',
  'store': 'A',
  'terminal': 'A1',
  'utc': '2026-09-29T13:07:00Z'},
 {'amount': '19.99',
  'id': 'H009',
  'kind': 'sale',
  'receipt': '1328',
  'ref': '',
  'store': 'A',
  'terminal': 'A1',
  'utc': '2026-09-29T13:07:00Z'}]

EXPECTED = {'A': {'2026-09-01': 121509,
       '2026-09-02': 157594,
       '2026-09-03': 126935,
       '2026-09-04': 165165,
       '2026-09-05': 151042,
       '2026-09-06': 178123,
       '2026-09-07': 186435,
       '2026-09-08': 128259,
       '2026-09-09': 213646,
       '2026-09-10': 102437,
       '2026-09-11': 147020,
       '2026-09-12': 179080,
       '2026-09-13': 107158,
       '2026-09-14': 187856,
       '2026-09-15': 165855,
       '2026-09-16': 201997,
       '2026-09-17': 88216,
       '2026-09-18': 190236,
       '2026-09-19': 222545,
       '2026-09-20': 128850,
       '2026-09-21': 115368,
       '2026-09-22': 135812,
       '2026-09-23': 94532,
       '2026-09-24': 166117,
       '2026-09-25': 179979,
       '2026-09-26': 163637,
       '2026-09-27': 118990,
       '2026-09-28': 122975,
       '2026-09-29': 3998},
 'B': {'2026-09-01': 268411,
       '2026-09-02': 284286,
       '2026-09-03': 283552,
       '2026-09-04': 224999,
       '2026-09-05': 219958,
       '2026-09-06': 234536,
       '2026-09-07': 324823,
       '2026-09-08': 314503,
       '2026-09-09': 346762,
       '2026-09-10': 343550,
       '2026-09-11': 305118,
       '2026-09-12': 322781,
       '2026-09-13': 314078,
       '2026-09-14': 366410,
       '2026-09-15': 304501,
       '2026-09-16': 344751,
       '2026-09-17': 291898,
       '2026-09-18': 245974,
       '2026-09-19': 196882,
       '2026-09-20': 244318,
       '2026-09-21': 315921,
       '2026-09-22': 264506,
       '2026-09-23': 301880,
       '2026-09-24': 299010,
       '2026-09-25': 269324,
       '2026-09-26': 326792,
       '2026-09-27': 280182,
       '2026-09-28': 370580},
 'C': {'2026-09-01': 280095,
       '2026-09-02': 197839,
       '2026-09-03': 294133,
       '2026-09-04': 257954,
       '2026-09-05': 277494,
       '2026-09-06': 218881,
       '2026-09-07': 286917,
       '2026-09-08': 258783,
       '2026-09-09': 285098,
       '2026-09-10': 256424,
       '2026-09-11': 318169,
       '2026-09-12': 353637,
       '2026-09-13': 373652,
       '2026-09-14': 277994,
       '2026-09-15': 268396,
       '2026-09-16': 236231,
       '2026-09-17': 213395,
       '2026-09-18': 333774,
       '2026-09-19': 313819,
       '2026-09-20': 214056,
       '2026-09-21': 214026,
       '2026-09-22': 218189,
       '2026-09-23': 367674,
       '2026-09-24': 238354,
       '2026-09-25': 295195,
       '2026-09-26': 282051,
       '2026-09-27': 204821,
       '2026-09-28': 226850},
 'D': {'2026-09-04': 11000, '2026-09-09': 6755}}

sales = [row for row in public_rows() if row["kind"] == "sale" and row["id"] not in REIMPORTED]
if totals(sales + HIDDEN_SALES) != EXPECTED:
    raise SystemExit("sales are not placed on the right business days")
