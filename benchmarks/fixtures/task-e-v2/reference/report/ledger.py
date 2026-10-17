"""The transaction ledger: one row per sale or refund, timestamps in UTC.

The ledger is built by importing each till's daily export. ``id`` is assigned
by the import; ``terminal`` and ``receipt`` come from the till, and each till
numbers its own receipts.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from report.money import parse_amount
from report.timezones import parse_utc

KINDS = ("sale", "refund")
COLUMNS = ("id", "store", "terminal", "receipt", "kind", "utc", "amount", "ref")


@dataclass(frozen=True)
class Transaction:
    id: str
    store: str
    terminal: str
    receipt: int
    kind: str
    utc: datetime
    amount_cents: int
    ref: Optional[str] = None

    @property
    def signed_cents(self) -> int:
        """Sales add to a day's total; refunds take away from it."""
        return self.amount_cents if self.kind == "sale" else -self.amount_cents


def load_transactions(path: Path) -> List[Transaction]:
    """Every row of the ledger, validated, in file order.

    A till's export imported twice gives the same receipts new ids. A receipt
    is one transaction however often it was imported, so only its first row
    is kept.
    """
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in COLUMNS if column not in (reader.fieldnames or ())]
        if missing:
            raise ValueError("ledger %s lacks columns: %s" % (path, ", ".join(missing)))
        rows = list(reader)
    transactions = []
    seen = set()
    receipts = set()
    for number, row in enumerate(rows, start=2):
        if row["id"] in seen:
            raise ValueError("line %d: duplicate transaction id %s" % (number, row["id"]))
        seen.add(row["id"])
        if row["kind"] not in KINDS:
            raise ValueError("line %d: unknown kind %r" % (number, row["kind"]))
        amount = parse_amount(row["amount"])
        if amount <= 0:
            raise ValueError("line %d: amounts are positive; the kind sets the sign" % number)
        receipt = (row["store"], row["terminal"], int(row["receipt"]))
        if receipt in receipts:
            continue
        receipts.add(receipt)
        transactions.append(Transaction(
            id=row["id"],
            store=row["store"],
            terminal=row["terminal"],
            receipt=int(row["receipt"]),
            kind=row["kind"],
            utc=parse_utc(row["utc"]),
            amount_cents=amount,
            ref=row["ref"] or None,
        ))
    return transactions
