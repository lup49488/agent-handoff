FIELDS = ("id", "name", "note")


def normalize_columns(columns):
    if columns is None:
        return FIELDS
    if isinstance(columns, str):
        return tuple(part.strip() for part in columns.split(","))
    return tuple(columns)


def row_values(row):
    return tuple(row)
