import csv


def write_rows(rows, stream):
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("id", "name", "note"))
    writer.writerows(rows)
