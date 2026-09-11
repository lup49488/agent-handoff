## Source progress

- Located the route boundary in `api.py` and the records in `service.py`.
- Acceptance reveals that insertion order cannot define the API order.

## Next step

Keep filtering and sorting in the service layer. Do not mutate `TASKS` while
serving a request.
