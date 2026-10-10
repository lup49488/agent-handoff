import csv

from schema import FIELDS, normalize_columns, row_values


def validate_columns(columns):
    selected = normalize_columns(columns)
    if (
        not selected
        or any(not name or name not in FIELDS for name in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("columns must be a unique non-empty subset of id,name,note")
    return selected


def write_rows(rows, stream, delimiter=",", columns=None, null_text=""):
    selected = validate_columns(columns)
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
    indexes = [FIELDS.index(name) for name in selected]
    writer.writerow(selected)
    for row in rows:
        values = row_values(row)
        writer.writerow([null_text if values[index] is None else values[index] for index in indexes])
