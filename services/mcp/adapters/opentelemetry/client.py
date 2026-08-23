from typing import Any, Dict

import requests


class OpenTelemetryClient:
    """Trace backend client for OpenTelemetry compatible APIs."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint.rstrip("/")

    def query_trace(self, trace_id: str = None, service_name: str = None, start: str = None, end: str = None) -> Dict[str, Any]:
        params = {}
        if trace_id:
            params["trace_id"] = trace_id
        if service_name:
            params["service.name"] = service_name
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        response = requests.get(
            f"{self.endpoint}/api/traces",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
