"""Prometheus investigation adapter foundation."""

from dataclasses import dataclass


@dataclass
class MetricEvidence:
    query: str
    value: str


class PrometheusToolAdapter:
    name = "prometheus"
    permission = "readonly"

    def query(self, expression: str) -> MetricEvidence:
        return MetricEvidence(
            query=expression,
            value="mock",
        )
