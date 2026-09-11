from api import handle_get_tasks


def ids(query):
    status, payload = handle_get_tasks(query)
    assert status == 200, (status, payload)
    return [task["id"] for task in payload["tasks"]]


assert ids({}) == [2, 4, 1]
assert ids({"owner": "ada"}) == [4, 1]
assert ids({"limit": "1"}) == [2]
for value in ("0", "-1", "many"):
    assert handle_get_tasks({"limit": value}) == (400, {"error": "invalid limit"})
