"""Early milestone evaluator; intentionally weaker than full acceptance."""

import tempfile
from pathlib import Path

from cli import export_rows


rows = [(1, "Ada", "plain")]
with tempfile.TemporaryDirectory() as directory:
    output = Path(directory) / "progress.csv"
    export_rows(rows, output, delimiter=";")
    content = output.read_text(encoding="utf-8")
assert content == "id;name;note\n1;Ada;plain\n", repr(content)
assert rows == [(1, "Ada", "plain")]
