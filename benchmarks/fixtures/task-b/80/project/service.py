TASKS = [
    {"id": 1, "owner": "ada", "status": "open", "created_at": 30},
    {"id": 2, "owner": "bob", "status": "open", "created_at": 10},
    {"id": 3, "owner": "ada", "status": "closed", "created_at": 20},
    {"id": 4, "owner": "ada", "status": "open", "created_at": 15},
]


def list_tasks(owner=None):
    return [
        task for task in TASKS
        if task["status"] == "open" and (owner is None or task["owner"] == owner)
    ]
