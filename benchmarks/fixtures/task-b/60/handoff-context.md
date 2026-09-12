## Source progress

- Owner filtering is implemented without mutating the shared records.
- The remaining failures are closed-task exclusion, chronological ordering, and
  a validated API-level `limit`.

## Decision

Treat `limit` as an API concern. Reject non-positive or non-numeric strings;
do not silently coerce them.

## Next step

Exclude closed tasks and sort by `created_at` in the service layer, then apply
the validated `limit` in the API after ordering.
