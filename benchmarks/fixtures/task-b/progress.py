from api import handle_get_tasks

status, payload = handle_get_tasks({"owner": "ada"})
assert status == 200, (status, payload)
assert {task["id"] for task in payload["tasks"]} == {1, 4}
