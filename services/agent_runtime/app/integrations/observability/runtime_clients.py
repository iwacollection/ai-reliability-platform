"""Production observability query clients foundation."""

from dataclasses import dataclass


@dataclass
class QueryResult:
    source: str
    query: str
    data: list


class PrometheusRuntimeClient:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def query(self, promql: str) -> QueryResult:
        # Production implementation:
        # GET /api/v1/query
        return QueryResult(
            source="prometheus",
            query=promql,
            data=[],
        )


class LokiRuntimeClient:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def query(self, logql: str) -> QueryResult:
        # Production implementation:
        # GET /loki/api/v1/query_range
        return QueryResult(
            source="loki",
            query=logql,
            data=[],
        )
