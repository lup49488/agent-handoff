import csv
import json
import tempfile
import unittest
from pathlib import Path

from cli import export_rows, main


class CsvDelimiterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "export.csv"

    def test_default_csv_and_existing_call_shape(self):
        export_rows([(1, "Ada", "plain")], self.path)
        with self.path.open(encoding="utf-8", newline="") as stream:
            self.assertEqual(list(csv.reader(stream)), [["id", "name", "note"], ["1", "Ada", "plain"]])

    def test_cli_custom_delimiter_quotes_fields(self):
        rows = [(1, "Ada;Lu", 'said "hello"\nnext')]
        main(["--output", str(self.path), "--delimiter", ";"], rows=rows)
        with self.path.open(encoding="utf-8", newline="") as stream:
            self.assertEqual(list(csv.reader(stream, delimiter=";")), [["id", "name", "note"], ["1", "Ada;Lu", 'said "hello"\nnext']])
        self.assertEqual(rows, [(1, "Ada;Lu", 'said "hello"\nnext')])

    def test_json_is_not_changed_by_csv_delimiter(self):
        rows = [(1, "Ada;Lu", "note")]
        before = json.dumps([{"id": 1, "name": "Ada;Lu", "note": "note"}], ensure_ascii=False) + "\n"
        main(["--format", "json", "--output", str(self.path), "--delimiter", ";"], rows=rows)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_invalid_delimiter_does_not_truncate_existing_output(self):
        self.path.write_text("preserve me\n", encoding="utf-8")
        for value in ("", "::", '"', "\n", "\r", "\x00"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    export_rows([(1, "Ada", "note")], self.path, delimiter=value)
                self.assertEqual(self.path.read_text(encoding="utf-8"), "preserve me\n")


if __name__ == "__main__":
    unittest.main()
