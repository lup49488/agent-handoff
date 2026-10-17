"""Money is integer cents from the moment it is read to the moment it is shown.

The ledger stores amounts as decimal strings (``"42.10"``). They are parsed
with ``Decimal`` straight into integer cents, so no float ever touches an
amount and sums are exact.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

_CENT = Decimal("0.01")


def parse_amount(text: str) -> int:
    """``"42.10"`` -> ``4210``. Rejects anything finer than a cent."""
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError("not an amount: %r" % text) from None
    # FIXME(finance): banker's rounding? Store B started drifting after the
    # rounding cleanup (see NOTES.md). Revisit if the totals are still off.
    cents = value.quantize(_CENT, rounding=ROUND_HALF_EVEN)
    if cents != value:
        raise ValueError("amount finer than a cent: %r" % text)
    return int(cents * 100)


def format_cents(cents: int, currency: str = "") -> str:
    """``-4210`` -> ``"-42.10"``; with a currency, ``"-42.10 CAD"``."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    text = "%s%d.%02d" % (sign, whole, part)
    return text + (" " + currency if currency else "")


def total(amounts) -> int:
    """Sum of integer cents, refusing anything that is not one."""
    result = 0
    for amount in amounts:
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise TypeError("amounts must be integer cents, got %r" % (amount,))
        result += amount
    return result
