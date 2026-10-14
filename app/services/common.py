def isoformat(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def item_count(value):
    return len(value) if value is not None else 0