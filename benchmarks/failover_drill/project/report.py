"""A one-line summary of a list of measurements."""

from stats import median, mode, percentile


def summary(values, unit=""):
    """'n=4 median=2.50 mode=1 p90=3.70 ms' -- or 'n=0' for no values."""
    if not values:
        return "n=0" + unit
    parts = [
        "n=%d" % len(values),
        "median=%.1f" % median(values),
        "mode=%s" % mode(values),
        "p90=%.2f" % percentile(values, 90),
    ]
    return " ".join(parts) + unit
