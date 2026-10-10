import csv
import json
import tempfile
import unittest
from pathlib import Path

import schema
from cli import export_rows, main


class CsvProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "export.csv"

    def read_csv(self, delimiter=","):
        with self.path.open(encoding="utf-8", newline="") as stream:
            return list(csv.reader(stream, delimiter=delimiter))

    def test_default_call_shape_and_bytes_remain_compatible(self):
        export_rows([(1, "Ada", "plain")], self.path)
        self.assertEqual(self.path.read_bytes(), b"id,name,note\n1,Ada,plain\n")
        self.assertEqual(schema.FIELDS, ("id", "name", "note"))

    def test_cli_projects_columns_and_quotes_round_trip(self):
        rows = [(1, "Ada;Lu", 'said "hello"\nnext')]
        original = list(rows)
        main(["--output", str(self.path), "--delimiter", ";", "--columns", " note, id ,name "], rows=rows)
        self.assertEqual(self.read_csv(";"), [["note", "id", "name"], ['said "hello"\nnext', "1", "Ada;Lu"]])
        self.assertEqual(rows, original)

    def test_null_text_only_replaces_none(self):
        export_rows([(1, None, "None")], self.path, null_text="∅")
        self.assertEqual(self.read_csv(), [["id", "name", "note"], ["1", "∅", "None"]])

    def test_json_is_not_changed_by_csv_only_options(self):
        rows = [(1, "Ada;Lu", "note")]
        before = json.dumps([{"id": 1, "name": "Ada;Lu", "note": "note"}], ensure_ascii=False) + "\n"
        main(["--format", "json", "--output", str(self.path), "--delimiter", "::", "--columns", "unknown", "--null-text", "∅"], rows=rows)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_invalid_delimiter_does_not_truncate_existing_output(self):
        self.path.write_text("preserve me\n", encoding="utf-8")
        for delimiter in ("", "::", '"', "\n", "\r", "\x00", "\x01"):
            with self.subTest(delimiter=delimiter):
                with self.assertRaises(ValueError):
                    export_rows([(1, "Ada", "note")], self.path, delimiter=delimiter)
                self.assertEqual(self.path.read_text(encoding="utf-8"), "preserve me\n")

    def test_invalid_columns_do_not_truncate_existing_output(self):
        self.path.write_text("preserve me\n", encoding="utf-8")
        for columns in ("", ",id", "id,", "id,,name", "id,id", "bogus"):
            with self.subTest(columns=columns):
                with self.assertRaises(ValueError):
                    export_rows([(1, "Ada", "note")], self.path, columns=columns)
                self.assertEqual(self.path.read_text(encoding="utf-8"), "preserve me\n")


if __name__ == "__main__":
    unittest.main()
