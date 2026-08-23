from typing import Any, Dict

import requests


class LokiClient:
    """Thin Loki HTTP API client used by the MCP adapter."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint.rstrip("/")

    def query_range(self, query: str, start: str = None, end: str = None, limit: int = 100) -> Dict[str, Any]:
        params = {
            "query": query,
            "limit": limit,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        response = requests.get(
            f"{self.endpoint}/loki/api/v1/query_range",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
