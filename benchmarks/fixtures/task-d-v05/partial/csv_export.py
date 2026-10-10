import csv

from schema import FIELDS, normalize_columns, row_values


def write_rows(rows, stream, delimiter=",", columns=None):
    selected = normalize_columns(columns)
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
    indexes = [FIELDS.index(name) for name in selected]
    writer.writerow(selected)
    for row in rows:
        values = row_values(row)
        writer.writerow([values[index] for index in indexes])
