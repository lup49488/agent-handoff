Extend the export command with a robust CSV projection and null-rendering API.

The existing command writes a fixed CSV header (`id,name,note`) and has a
separate JSON path. Implement the feature across the CLI, shared schema, and
CSV writer; do not edit `test_public.py`.

Requirements:

- Keep `export_rows(rows, destination, fmt="csv")` and default CSV/JSON output
  byte-for-byte compatible with existing calls.
- For CSV, add `--delimiter` (default comma) and `--columns` (default all
  fields). `--columns` is a comma-separated, ordered subset of `id`, `name`,
  and `note`; trim surrounding whitespace, reject empty, duplicate, or unknown
  names, and emit both header and row cells in the requested order.
- Add `--null-text` (default empty string) and render only Python `None` cells
  using that text. Do not mutate input rows.
- Validate CSV delimiter and column options before creating or truncating the
  destination. A delimiter must be exactly one printable character other than
  a quote, CR, LF, or NUL.
- Use standard CSV quoting so embedded delimiters, quotes, and newlines
  round-trip. Preserve UTF-8 text.
- CSV-only options must not change JSON output, even if they are supplied.
- Keep schema metadata in one shared module used by both CSV and JSON writers;
  do not duplicate the field-to-cell mapping.

Run `python -m unittest -v` to check the public tests. Do not edit the tests.
