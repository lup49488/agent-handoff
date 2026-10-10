import argparse
import unicodedata
from pathlib import Path

import csv_export
import json_export
import schema


SAMPLE_ROWS = [(1, "Ada", "first export"), (2, "Lin", "second export")]


def _validate_delimiter(delimiter):
    if (
        not isinstance(delimiter, str)
        or len(delimiter) != 1
        or delimiter in ('"', "\r", "\n", "\x00")
        or not delimiter.isprintable()
        or unicodedata.category(delimiter).startswith("C")
    ):
        raise ValueError("delimiter must be one printable character other than quote or newline")
    return delimiter


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--output", required=True)
    parser.add_argument("--delimiter", default=",")
    parser.add_argument("--columns", default=None)
    parser.add_argument("--null-text", default="")
    return parser


def export_rows(rows, destination, fmt="csv", *, delimiter=",", columns=None, null_text=""):
    if fmt == "csv":
        delimiter = _validate_delimiter(delimiter)
        selected = csv_export.validate_columns(columns)
    elif fmt != "json":
        raise ValueError("format must be csv or json")
    path = Path(destination)
    writer = csv_export.write_rows if fmt == "csv" else json_export.write_rows
    with path.open("w", encoding="utf-8", newline="") as stream:
        if fmt == "csv":
            writer(rows, stream, delimiter=delimiter, columns=selected, null_text=null_text)
        else:
            writer(rows, stream)


def main(argv=None, rows=None):
    args = build_parser().parse_args(argv)
    export_rows(
        SAMPLE_ROWS if rows is None else rows,
        args.output,
        args.format,
        delimiter=args.delimiter,
        columns=args.columns,
        null_text=args.null_text,
    )


if __name__ == "__main__":
    main()
