"""Fixture-owned completion evaluator. Run with the target project as cwd."""

import csv
import json
import tempfile
from pathlib import Path

from cli import export_rows, main


with tempfile.TemporaryDirectory() as directory:
    output = Path(directory) / "out.csv"
    rows = [(1, "Ada;Lu", 'said "hello"\nnext')]
    original = list(rows)
    main(["--output", str(output), "--delimiter", ";"], rows=rows)
    with output.open(encoding="utf-8", newline="") as stream:
        parsed = list(csv.reader(stream, delimiter=";"))
    assert parsed == [["id", "name", "note"], ["1", "Ada;Lu", 'said "hello"\nnext']], parsed
    assert rows == original, rows

    output.write_text("do not truncate\n", encoding="utf-8")
    for invalid in ("", "::", '"', "\r", "\n", "\x00"):
        try:
            export_rows(rows, output, delimiter=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid delimiter accepted: %r" % invalid)
        assert output.read_text(encoding="utf-8") == "do not truncate\n"

    json_output = Path(directory) / "out.json"
    main(["--format", "json", "--output", str(json_output), "--delimiter", ";"], rows=rows)
    expected = json.dumps(
        [{"id": 1, "name": "Ada;Lu", "note": 'said "hello"\nnext'}],
        ensure_ascii=False,
    ) + "\n"
    assert json_output.read_text(encoding="utf-8") == expected
