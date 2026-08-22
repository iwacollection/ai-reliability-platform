"""Loki MCP investigation tool foundation.

Provides read-only log investigation capabilities.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class LokiQueryResult:
    query: str
    data: Any
    source: str = "loki"


class LokiMCPTool:
    name = "loki"

    def query_log(self, query: str) -> LokiQueryResult:
        return LokiQueryResult(query=query, data=None)

    def query_range(self, query: str, start: int, end: int) -> LokiQueryResult:
        return LokiQueryResult(query=query, data=None)
