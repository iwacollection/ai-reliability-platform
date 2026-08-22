"""Prometheus connector foundation for investigation tools."""

from dataclasses import dataclass


@dataclass
class PrometheusConfig:
    endpoint: str


class PrometheusConnector:
    def __init__(self, config: PrometheusConfig):
        self.config = config

    def query(self, promql: str) -> dict:
        # Production implementation will call Prometheus HTTP API.
        return {
            "query": promql,
            "source": "prometheus",
            "result": [],
        }

    def range_query(self, promql: str, start: int, end: int):
        return self.query(promql)
