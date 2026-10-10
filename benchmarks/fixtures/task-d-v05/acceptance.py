"""Fixture-owned completion evaluator. Run with the target project as cwd."""

import csv
import json
import tempfile
from pathlib import Path

import schema
from cli import export_rows, main


assert schema.FIELDS == ("id", "name", "note")
with tempfile.TemporaryDirectory() as directory:
    output = Path(directory) / "out.csv"
    rows = [(1, "Ada;Lu", 'said "hello"\nnext'), (2, None, "李")]
    original = list(rows)
    main(["--output", str(output), "--delimiter", ";", "--columns", "note, id, name", "--null-text", "(null)"], rows=rows)
    with output.open(encoding="utf-8", newline="") as stream:
        parsed = list(csv.reader(stream, delimiter=";"))
    assert parsed == [
        ["note", "id", "name"], ['said "hello"\nnext', "1", "Ada;Lu"], ["李", "2", "(null)"],
    ], parsed
    assert rows == original, rows

    output.write_text("do not truncate\n", encoding="utf-8")
    for invalid in ("", "::", '"', "\r", "\n", "\x00", "\x01"):
        try:
            export_rows(rows, output, delimiter=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid delimiter accepted: %r" % invalid)
        assert output.read_text(encoding="utf-8") == "do not truncate\n"

    for invalid_columns in ("", ",id", "id,", "id,,name", "id,id", "bogus"):
        output.write_text("do not truncate\n", encoding="utf-8")
        try:
            export_rows(rows, output, columns=invalid_columns)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid columns accepted: %r" % invalid_columns)
        assert output.read_text(encoding="utf-8") == "do not truncate\n"

    json_output = Path(directory) / "out.json"
    main(["--format", "json", "--output", str(json_output), "--delimiter", "::", "--columns", "unknown", "--null-text", "(null)"], rows=rows)
    expected = json.dumps(
        [
            {"id": 1, "name": "Ada;Lu", "note": 'said "hello"\nnext'},
            {"id": 2, "name": None, "note": "李"},
        ],
        ensure_ascii=False,
    ) + "\n"
    assert json_output.read_text(encoding="utf-8") == expected
