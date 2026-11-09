"""Business rules live here, not in API handlers.

Ninja handlers parse input, call a service and map the result to a schema. Services
own the rules (placing orders, reserving stock, pricing, ...), wrap Oscar's models and
strategies, and run one transaction per use case. They raise `store_backend.api.errors`
exceptions (`ConflictError`, `NotFoundError`, ...) for expected failures.
"""
