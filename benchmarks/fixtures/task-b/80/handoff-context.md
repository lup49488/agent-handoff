## Source progress

- Open-state and owner filtering are correct.
- Invalid `limit` values return the specified 400 payload.

## Next step

Sort the service result by `created_at` before the API applies `limit`; slicing
in insertion order currently returns the wrong task.
