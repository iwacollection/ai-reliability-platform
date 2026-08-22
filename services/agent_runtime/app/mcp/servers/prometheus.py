from dataclasses import dataclass
from typing import Any


@dataclass
class PrometheusMCPServer:
    """Prometheus metrics query MCP adapter."""

    name: str = "prometheus"

    def query(self, expression: str) -> dict[str, Any]:
        return {
            "tool": "prometheus_query",
            "expression": expression,
            "status": "adapter_ready",
        }
