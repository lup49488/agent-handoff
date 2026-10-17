"""Write task E v3's ledger and finance's figures into a project.

    python prepare.py <project>

The ledger is nine months of five tills' trading -- about a million rows --
so that every run of the report, and anything else that reads the ledger,
takes long enough that what an investigation establishes is costly to
establish again. It is too large to keep in the repository, so it is
generated, deterministically, from the seed below; the first run caches it in
`.generated/` beside this script and later runs copy the cache.

Finance's figures are computed here from what the generator knows -- each
transaction's true business day and which rows a re-import duplicated --
independently of the report code.
"""

import hashlib
import os
import random
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SEED = 20261017
FIRST_DAY = date(2026, 1, 1)
LAST_DAY = date(2026, 9, 30)
SALES_PER_TILL_DAY = (1150, 1500)
OFFSETS = {"A": (1, 0), "B": (-3, -30), "C": (5, 45)}
TILLS = {"A": ["A1"], "B": ["B1", "B2"], "C": ["C1", "C2"]}
CUTOFF_HOUR = 4
#: (day the copies were imported, day of the export re-sent, till, how many sales at its end)
RESENDS = [(date(2026, 9, 18), date(2026, 9, 17), "B2", 6),
           (date(2026, 9, 24), date(2026, 9, 23), "B1", 3)]
HEADER = "id,store,terminal,receipt,kind,utc,amount,ref\n"
EPOCH = datetime(1970, 1, 1)

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".generated"
OUTPUTS = ("data/transactions.csv", "expected/daily_totals.csv")


def _source_digest() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def generate(destination: Path) -> None:
    rng = random.Random(SEED)
    offset_minutes = {s: h * 60 + m for s, (h, m) in OFFSETS.items()}
    utc_text_cache = {}

    def utc_text(minute_epoch):
        text = utc_text_cache.get(minute_epoch)
        if text is None:
            text = (EPOCH + timedelta(minutes=minute_epoch)).strftime("%Y-%m-%dT%H:%M:00Z")
            utc_text_cache[minute_epoch] = text
        return text

    rows = []          # (utc minute, id number, line without id)
    totals = {}        # (store, day) -> cents, for finance
    receipt_no = {till: 1000 for tills in TILLS.values() for till in tills}
    recent_sales = {store: [] for store in TILLS}   # (utc minute, id, remaining cents)
    exports = {}
    next_id = 1
    day = FIRST_DAY
    while day <= LAST_DAY:
        day_minute = int((datetime.combine(day, datetime.min.time()) - EPOCH).total_seconds() // 60)
        for store, tills in TILLS.items():
            shift = offset_minutes[store]
            for till in tills:
                batch = []
                count = rng.randint(*SALES_PER_TILL_DAY)
                minutes = sorted(
                    24 * 60 + rng.randrange(0, CUTOFF_HOUR * 60) if rng.random() < 0.03
                    else rng.randrange(9 * 60, 24 * 60)
                    for _ in range(count)
                )
                for local in minutes:
                    receipt_no[till] += 1
                    cents = rng.randrange(400, 26000)
                    batch.append(["sale", day_minute + local - shift, receipt_no[till], cents, None])
                for _ in range(max(1, count // 120)):
                    local = rng.randrange(10 * 60, 24 * 60) if rng.random() < 0.85 \
                        else 24 * 60 + rng.randrange(0, CUTOFF_HOUR * 60)
                    at = day_minute + local - shift
                    pool = recent_sales[store]
                    pick = None
                    for _ in range(8):
                        if not pool:
                            break
                        candidate = pool[rng.randrange(max(0, len(pool) - 4000), len(pool))]
                        if candidate[0] < at and candidate[2] > 500:
                            pick = candidate
                            break
                    if pick is None:
                        continue
                    amount = pick[2] if rng.random() < 0.5 else rng.randrange(100, pick[2])
                    pick[2] -= amount
                    receipt_no[till] += 1
                    batch.append(["refund", at, receipt_no[till], amount, pick[1]])
                batch.sort(key=lambda item: item[1])
                exported = []
                for kind, at, receipt, cents, ref in batch:
                    ident = next_id
                    next_id += 1
                    line = "%s,%s,%d,%s,%s,%d.%02d,%s\n" % (
                        store, till, receipt, kind, utc_text(at), cents // 100, cents % 100,
                        "" if ref is None else "T%07d" % ref)
                    rows.append((at, ident, line))
                    signed = cents if kind == "sale" else -cents
                    totals[(store, day)] = totals.get((store, day), 0) + signed
                    if kind == "sale":
                        recent_sales[store].append([at, ident, cents])
                        exported.append((at, line))
                exports[(day, till)] = exported
        for imported, resent, till, count in RESENDS:
            if imported == day:
                for at, line in exports[(resent, till)][-count:]:
                    rows.append((at, next_id, line))
                    next_id += 1
        for store in recent_sales:
            del recent_sales[store][:-6000]
        day += timedelta(days=1)

    rows.sort(key=lambda item: (item[0], item[1]))
    (destination / "data").mkdir(parents=True, exist_ok=True)
    (destination / "expected").mkdir(parents=True, exist_ok=True)
    with open(destination / "data" / "transactions.csv", "w", encoding="utf-8", newline="\n") as out:
        out.write(HEADER)
        out.writelines("T%07d,%s" % (ident, line) for _, ident, line in rows)
    with open(destination / "expected" / "daily_totals.csv", "w", encoding="utf-8", newline="\n") as out:
        out.write("store,day,total\n")
        for (store, when), cents in sorted(totals.items()):
            sign = "-" if cents < 0 else ""
            out.write("%s,%s,%s%d.%02d\n" % (store, when.isoformat(), sign, abs(cents) // 100, abs(cents) % 100))


def main(project: Path) -> None:
    cache = CACHE / _source_digest()
    if not all((cache / name).is_file() for name in OUTPUTS):
        started = time.monotonic()
        staging = CACHE / ("tmp-%d" % os.getpid())
        shutil.rmtree(staging, ignore_errors=True)
        generate(staging)
        shutil.rmtree(cache, ignore_errors=True)
        staging.rename(cache)
        print("generated %s in %.1f s" % (cache, time.monotonic() - started), file=sys.stderr)
    for name in OUTPUTS:
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache / name, target)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
