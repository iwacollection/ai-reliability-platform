from dataclasses import dataclass
from typing import Any


@dataclass
class LokiMCPServer:
    """Loki log investigation MCP adapter."""

    name: str = "loki"

    def query_logs(self, query: str) -> dict[str, Any]:
        return {
            "tool": "loki_query",
            "query": query,
            "status": "adapter_ready",
        }
