"""Progress gate: projection and delimiter work is present, full acceptance is not implied."""

import csv
import tempfile
from pathlib import Path

from cli import export_rows


rows = [(1, "Ada;Lu", "first"), (2, "李", "second")]
original = list(rows)
with tempfile.TemporaryDirectory() as directory:
    output = Path(directory) / "progress.csv"
    export_rows(rows, output, delimiter=";", columns="note,id")
    with output.open(encoding="utf-8", newline="") as stream:
        parsed = list(csv.reader(stream, delimiter=";"))
assert parsed == [["note", "id"], ["first", "1"], ["second", "2"]], parsed
assert rows == original
