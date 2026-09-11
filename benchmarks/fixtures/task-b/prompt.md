Implement `handle_get_tasks(query)` in `api.py`. It must return only open
tasks, optionally filter by `owner`, sort by `created_at` ascending, and accept
an optional positive integer string `limit`. Return `(400, {"error": "invalid limit"})`
for invalid limits; otherwise return `(200, {"tasks": [...]})`. Keep task
records immutable and make `python acceptance.py` pass.
