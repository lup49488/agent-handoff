from service import list_tasks


def handle_get_tasks(query):
    raw_limit = query.get("limit")
    if raw_limit is not None and (not raw_limit.isdigit() or int(raw_limit) < 1):
        return 400, {"error": "invalid limit"}
    tasks = list_tasks(query.get("owner"))
    if raw_limit is not None:
        tasks = tasks[:int(raw_limit)]
    return 200, {"tasks": tasks}
