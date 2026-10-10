Add a `--delimiter` option to the CSV export command.

Requirements:

- The default remains a comma, so existing CSV output and calls keep working.
- A supplied delimiter must be exactly one printable character other than a
  quote, CR, LF, or NUL. Invalid values must raise `ValueError` with a useful
  message before creating or truncating the output file.
- Apply the delimiter only to CSV output. JSON output must remain unchanged
  regardless of the supplied delimiter.
- Use standard CSV quoting rules so delimiters, quotes, and newlines inside
  fields round-trip correctly. Do not mutate the input rows.
- Keep the public `export_rows(rows, destination, fmt="csv")` call compatible.

Run `python -m unittest -v` to check the public tests. Do not edit the tests.
