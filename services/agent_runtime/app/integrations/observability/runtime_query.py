"""Unified observability query boundary for Prometheus and Loki."""

from dataclasses import dataclass


@dataclass
class QueryContext:
    endpoint: str
    tenant: str | None = None


class ObservabilityRuntime:
    def __init__(self, context: QueryContext):
        self.context = context

    def prometheus_query(self, expression: str) -> dict:
        return {
            "type": "prometheus",
            "query": expression,
            "status": "adapter_ready",
        }

    def loki_query(self, logql: str) -> dict:
        return {
            "type": "loki",
            "query": logql,
            "status": "adapter_ready",
        }
