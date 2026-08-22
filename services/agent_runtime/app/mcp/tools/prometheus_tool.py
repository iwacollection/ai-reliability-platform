"""Prometheus MCP investigation tool foundation.

Provides read-only metric query capabilities for investigation workflows.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class PrometheusQueryResult:
    query: str
    data: Any
    source: str = "prometheus"


class PrometheusMCPTool:
    name = "prometheus"

    def query_metric(self, query: str) -> PrometheusQueryResult:
        # Runtime client wiring is injected by production connector layer.
        return PrometheusQueryResult(query=query, data=None)

    def query_range(self, query: str, start: int, end: int) -> PrometheusQueryResult:
        return PrometheusQueryResult(query=query, data=None)
