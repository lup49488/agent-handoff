import csv


def write_rows(rows, stream, delimiter=","):
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
    writer.writerow(("id", "name", "note"))
    writer.writerows(rows)
