"""Generate task E v2's ledger, finance's figures and evaluators from the reference.

    python gen_task_e_v2.py <fixture dir>
"""
import csv
import hashlib
import io
import pprint
import random
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FIX = Path(sys.argv[1]).resolve()
rng = random.Random(20261016)

OFFSETS = {"A": "+01:00", "B": "-03:30", "C": "+05:45", "D": "-09:30"}
CURRENCY = {"A": "EUR", "B": "CAD", "C": "NPR", "D": "USD"}
TILLS = {"A": ["A1"], "B": ["B1", "B2"], "C": ["C1", "C2"]}
FIRST_DAY = date(2026, 9, 1)
DAYS = 28
COLUMNS = ["id", "store", "terminal", "receipt", "kind", "utc", "amount", "ref"]


def offset_delta(store):
    text = OFFSETS[store]
    sign = -1 if text[0] == "-" else 1
    return sign * timedelta(hours=int(text[1:3]), minutes=int(text[4:6]))


def utc_text(store, business_day, minutes_after_midnight):
    """A local moment `minutes_after_midnight` into `business_day` (may run past 24h)."""
    local = datetime.combine(business_day, datetime.min.time()) + timedelta(minutes=minutes_after_midnight)
    moment = local.replace(tzinfo=timezone(offset_delta(store))).astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:00Z")


def local_minutes():
    """When in its business day a sale happens: mostly daytime, a few late."""
    roll = rng.random()
    if roll < 0.035:
        return 24 * 60 + rng.randrange(0, 4 * 60)  # 00:00-03:59 the next morning
    return rng.randrange(9 * 60, 24 * 60)


def amount():
    return "%d.%02d" % (rng.randrange(4, 260), rng.randrange(0, 100))


# -- the public ledger, as the imports built it --------------------------------
next_id = [1]
receipt_no = {till: 1000 for tills in TILLS.values() for till in tills}
sales_by_store = {store: [] for store in TILLS}
remaining = {}
rows = []
exports = {}  # (day, till) -> its rows, for a re-send


def new_id():
    value = "T%05d" % next_id[0]
    next_id[0] += 1
    return value


def import_rows(batch):
    for row in batch:
        row["id"] = new_id()
        rows.append(row)


def till_export(day, store, till):
    batch = []
    for minutes in sorted(local_minutes() for _ in range(rng.randint(8, 14))):
        receipt_no[till] += 1
        batch.append({"store": store, "terminal": till, "receipt": receipt_no[till], "kind": "sale",
                      "utc": utc_text(store, day, minutes), "amount": amount(), "ref": ""})
    minutes = rng.randrange(10 * 60, 24 * 60) if rng.random() < 0.8 else 24 * 60 + rng.randrange(0, 4 * 60)
    candidates = [s for s in sales_by_store[store][-60:]
                  if s["utc"] < utc_text(store, day, minutes) and remaining[s["id"]] > 500]
    if rng.random() < 0.45 and candidates:
        original = rng.choice(candidates)
        cents = remaining[original["id"]]
        if True:
            refund_cents = cents if rng.random() < 0.5 else rng.randrange(100, cents)
            receipt_no[till] += 1
            batch.append({"store": store, "terminal": till, "receipt": receipt_no[till], "kind": "refund",
                          "utc": utc_text(store, day, minutes), "amount": "%d.%02d" % divmod(refund_cents, 100),
                          "ref": original["id"]})
            remaining[original["id"]] -= refund_cents
    batch.sort(key=lambda r: r["utc"])
    return batch


RESENDS = {date(2026, 9, 18): [(date(2026, 9, 17), "B2", 6)],
           date(2026, 9, 24): [(date(2026, 9, 23), "B1", 3)]}

for offset in range(DAYS):
    day = FIRST_DAY + timedelta(days=offset)
    for store, tills in TILLS.items():
        for till in tills:
            batch = till_export(day, store, till)
            exports[(day, till)] = batch
            import_rows(batch)
            for row in batch:
                if row["kind"] == "sale":
                    sales_by_store[store].append(row)
                    cents = int(row["amount"].replace(".", ""))
                    remaining[row["id"]] = cents
    for resent_day, till, count in RESENDS.get(day, []):
        tail = [dict(row) for row in exports[(resent_day, till)] if row["kind"] == "sale"][-count:]
        import_rows(tail)

rows.sort(key=lambda r: (r["utc"], r["id"]))
DUPLICATE_IDS = sorted(r["id"] for r in rows if (r["store"], r["terminal"], r["receipt"]) in
                       {k for k, n in __import__("collections").Counter(
                           (x["store"], x["terminal"], x["receipt"]) for x in rows).items() if n > 1}
                       and r["id"] != min(x["id"] for x in rows if (x["store"], x["terminal"], x["receipt"])
                                          == (r["store"], r["terminal"], r["receipt"])))


def ledger_text(ledger_rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in ledger_rows:
        writer.writerow([row[c] for c in COLUMNS])
    return out.getvalue()


# -- what the evaluators add ---------------------------------------------------
def hidden_rows():
    extra = []
    hid = iter("H%03d" % n for n in range(1, 100))
    d_receipt = 5000
    # Store D, at -09:30: sales either side of its cutoff, and a refund.
    for offset, minutes, amt in [(3, 24 * 60 + 3 * 60 + 20, "70.00"), (3, 13 * 60, "40.00"),
                                 (8, 24 * 60 + 3 * 60 + 50, "55.55"), (8, 20 * 60, "12.00")]:
        d_receipt += 1
        extra.append({"id": next(hid), "store": "D", "terminal": "D1", "receipt": d_receipt, "kind": "sale",
                      "utc": utc_text("D", FIRST_DAY + timedelta(days=offset), minutes), "amount": amt, "ref": ""})
    d_receipt += 1
    extra.append({"id": next(hid), "store": "D", "terminal": "D1", "receipt": d_receipt, "kind": "refund",
                  "utc": utc_text("D", FIRST_DAY + timedelta(days=9), 24 * 60 + 2 * 60), "amount": "15.00",
                  "ref": extra[1]["id"]})
    # Store C, till C2: two sales re-imported, so the fix is not about store B.
    c2 = [r for r in rows if r["terminal"] == "C2" and r["kind"] == "sale"
          and r["utc"].startswith("2026-09-1")][5:7]
    for row in c2:
        copy = dict(row)
        copy["id"] = next(hid)
        extra.append(copy)
    # Store A, till A1: two genuine sales in the same minute for the same
    # amount, with different receipts -- both count.
    last_a1 = max(r["receipt"] for r in rows if r["terminal"] == "A1")
    for n in (1, 2):
        extra.append({"id": next(hid), "store": "A", "terminal": "A1", "receipt": last_a1 + n, "kind": "sale",
                      "utc": utc_text("A", FIRST_DAY + timedelta(days=DAYS), 14 * 60 + 7), "amount": "19.99",
                      "ref": ""})
    return extra


HIDDEN = hidden_rows()


def totals_with(variant, ledger_rows):
    work = Path(tempfile.mkdtemp())
    shutil.copytree(FIX / "project", work / "p")
    if variant:
        shutil.copytree(FIX / variant, work / "p", dirs_exist_ok=True)
    sys.path.insert(0, str(work / "p"))
    for name in [m for m in sys.modules if m == "report" or m.startswith("report.")]:
        del sys.modules[name]
    from report.aggregate import daily_totals
    from report.config import StoreSettings
    from report.ledger import load_transactions
    ordered = sorted(ledger_rows, key=lambda r: (r["utc"], r["id"]))
    (work / "ledger.csv").write_text(ledger_text(ordered), encoding="utf-8")
    stores = {s: StoreSettings(s, OFFSETS[s], 4, CURRENCY[s]) for s in OFFSETS}
    try:
        result = daily_totals(load_transactions(work / "ledger.csv"), stores)
    finally:
        sys.path.remove(str(work / "p"))
        shutil.rmtree(work, ignore_errors=True)
    return {s: {d.isoformat(): c for d, c in days.items()} for s, days in result.items()}


public_text = ledger_text(rows)
(FIX / "project" / "data").mkdir(exist_ok=True)
(FIX / "project" / "data" / "transactions.csv").write_bytes(public_text.encode("utf-8"))
public_sha = hashlib.sha256(public_text.encode("utf-8")).hexdigest()

public_expected = totals_with("reference", rows)
extended_expected = totals_with("reference", rows + HIDDEN)
progress_rows = [r for r in rows + HIDDEN if r["kind"] == "sale"]
dup_ids = set(DUPLICATE_IDS) | {r["id"] for r in HIDDEN if r["store"] == "C"}
progress_rows = [r for r in progress_rows if r["id"] not in dup_ids]
progress_expected = totals_with("reference", progress_rows)

(FIX / "project" / "expected").mkdir(exist_ok=True)
lines = ["store,day,total"]
for store in sorted(public_expected):
    for day, cents in sorted(public_expected[store].items()):
        sign = "-" if cents < 0 else ""
        lines.append("%s,%s,%s%d.%02d" % (store, day, sign, abs(cents) // 100, abs(cents) % 100))
(FIX / "project" / "expected" / "daily_totals.csv").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))

COMMON = '''import csv
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
COLUMNS = %r

#: The public ledger as the fixture ships it; editing it cannot make this pass.
PUBLIC_LEDGER_SHA256 = %r


def public_rows():
    data = Path("data/transactions.csv").read_bytes()
    if hashlib.sha256(data).hexdigest() != PUBLIC_LEDGER_SHA256:
        raise SystemExit("data/transactions.csv is not the ledger the fixture ships")
    return list(csv.DictReader(data.decode("utf-8").splitlines()))


def totals(rows):
    with tempfile.TemporaryDirectory() as temp:
        ledger = Path(temp) / "ledger.csv"
        with open(ledger, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\\n")
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda row: (row["utc"], row["id"])))
        result = daily_totals(load_transactions(ledger), STORES)
    return {store: {day.isoformat(): cents for day, cents in days.items()} for store, days in result.items()}
''' % (tuple(COLUMNS), public_sha)


def literal(value):
    return pprint.pformat(value, width=110, sort_dicts=True)


def as_strings(row_list):
    return [{c: str(r[c]) for c in COLUMNS} for r in row_list]


progress = '''"""Fixture-owned progress evaluator for task E v2. Run with the target project as cwd.

Progress is the sales of every store placed on the right business day: the
public ledger's sales without its re-imported rows, plus store D's. Offsets
must be right; duplicates and refunds do not enter into it.
"""

''' + COMMON + '''

#: Rows a re-import added to the public ledger; left out here.
REIMPORTED = %s

HIDDEN_SALES = %s

EXPECTED = %s

sales = [row for row in public_rows() if row["kind"] == "sale" and row["id"] not in REIMPORTED]
if totals(sales + HIDDEN_SALES) != EXPECTED:
    raise SystemExit("sales are not placed on the right business days")
''' % (literal(sorted(DUPLICATE_IDS)),
       literal(as_strings([r for r in HIDDEN if r["kind"] == "sale" and r["id"] not in dup_ids])),
       literal(progress_expected))

acceptance = '''"""Fixture-owned completion evaluator for task E v2. Run with the target project as cwd.

Kept outside the project so the target cannot edit it. Completion is
finance's figures for the public ledger, and for the public ledger extended
with store D's trading, a re-import at store C, and two genuine sales at
store A that share a minute and an amount but not a receipt.
"""

''' + COMMON + '''

HIDDEN = %s

PUBLIC_EXPECTED = %s

EXTENDED_EXPECTED = %s

public = public_rows()
if totals(public) != PUBLIC_EXPECTED:
    raise SystemExit("public ledger does not match finance's figures")
if totals(public + HIDDEN) != EXTENDED_EXPECTED:
    raise SystemExit("extended ledger does not match finance's figures")
''' % (literal(as_strings(HIDDEN)), literal(public_expected), literal(extended_expected))

(FIX / "progress.py").write_text(progress, encoding="utf-8", newline="\n")
(FIX / "acceptance.py").write_text(acceptance, encoding="utf-8", newline="\n")

print("rows", len(rows), "duplicates", DUPLICATE_IDS)
for variant in ("", "partial", "reference"):
    got = totals_with(variant, rows)
    bad = sorted((s, d) for s in public_expected for d in set(public_expected[s]) | set(got.get(s, {}))
                 if public_expected[s].get(d) != got.get(s, {}).get(d))
    print("%-10s public store-days wrong: %d %s" % (variant or "initial", len(bad), bad))
