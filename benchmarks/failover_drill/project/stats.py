"""Small statistics helpers."""


def median(values):
    """The middle value; for an even count, the mean of the two middle values."""
    if not values:
        raise ValueError("median of an empty sequence")
    ordered = list(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def mode(values):
    """The most common value; ties go to the value seen first."""
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=lambda v: (counts[v], v))


def percentile(values, p):
    """The p-th percentile (0-100) by linear interpolation between closest ranks."""
    ordered = sorted(values)
    rank = p / 100 * len(ordered)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)
