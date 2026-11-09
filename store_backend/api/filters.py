from ninja import FilterSchema


class QueryFilterSchema(FilterSchema):
    """Base for list filters: endpoint-specific explicit params plus free-text `q`.

    `q` is not applied as a filter; each endpoint passes it to its search service.
    """

    q: str | None = None
