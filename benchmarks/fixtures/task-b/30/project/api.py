from service import list_tasks


def handle_get_tasks(query):
    return 200, {"tasks": list_tasks()}
