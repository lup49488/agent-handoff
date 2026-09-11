import re
import unicodedata


def slugify(title):
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.replace("&", " and ")
    return "-".join(re.findall(r"[a-z0-9]+", normalized.lower()))
