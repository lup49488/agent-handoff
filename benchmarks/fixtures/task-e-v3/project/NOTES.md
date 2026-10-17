# Report notes

- Amounts are integer cents from parsing to display (`report/money.py`); no
  float touches an amount.
- **Business day.** A transaction belongs to the business day in which it
  happens in the store's local time, where a business day starts at the
  store's `day_cutoff_hour`. The stores trade late: with a cutoff of 4, a sale
  at 01:30 local on the 14th counts toward the 13th.
- **Refunds** are booked on the business day they are issued, not on the day
  of the sale they reverse.
- The ledger (`data/transactions.csv`) is in UTC and is built by importing
  each till's daily export; `id` is assigned by the import. Each till numbers
  its own receipts. Store offsets and cutoffs are in `stores.ini`.
- Finance's figures (`expected/daily_totals.csv`) come from their own system,
  fed from the same till exports.
- 2026-09: finance reported store B's daily totals drifting after the rounding
  cleanup in `money.py`; store A and C were unaffected. Not yet investigated.
