import json

from schema import FIELDS, row_values


def write_rows(rows, stream):
    json.dump(
        [dict(zip(FIELDS, row_values(row))) for row in rows],
        stream,
        ensure_ascii=False,
    )
    stream.write("\n")
