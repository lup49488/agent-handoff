import json


def write_rows(rows, stream):
    json.dump(
        [{"id": row[0], "name": row[1], "note": row[2]} for row in rows],
        stream,
        ensure_ascii=False,
    )
    stream.write("\n")
