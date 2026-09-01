"""Real Azure Resource Graph connector.

Authentication is injected by the caller; the connector never stores secrets.
Mutations are intentionally not exposed here. They must go through the
platform Action/Policy/Approval/Executor path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from services.connectors.common import AuthenticationError, AuthorizationError, Evidence, NotFoundError, RateLimitedError, UpstreamError


class AzureConnector:
    """Read-only Azure Resource Graph connector."""

    endpoint = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources"
    api_version = "2022-10-01"

    def __init__(self, token: str, *, timeout: float = 10.0, client: httpx.AsyncClient | None = None):
        if not token.strip():
            raise ValueError("Azure bearer token cannot be empty")
        self._token = token
        self._timeout = timeout
        self._client = client

    async def query_resources(self, subscription_id: str, query: str, *, top: int = 100) -> list[Evidence]:
        if not subscription_id.strip() or not query.strip():
            raise ValueError("subscription_id and query are required")
        if not 1 <= top <= 1000:
            raise ValueError("top must be between 1 and 1000")

        payload = {"subscriptions": [subscription_id], "query": query, "options": {"$top": top}}
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        close = self._client is None
        try:
            response = await client.post(f"{self.endpoint}?api-version={self.api_version}", json=payload, headers=headers)
        finally:
            if close:
                await client.aclose()
        self._raise_for_status(response)
        body = response.json()
        now = datetime.now(UTC)
        return [
            Evidence(
                source="azure",
                resource_type=str(item.get("type", "resource")),
                resource_id=str(item.get("id", item.get("name", "unknown"))),
                payload=item,
                observed_at=self._parse_time(item.get("properties", {}).get("timeCreated")),
                retrieved_at=now,
                provenance={"subscription_id": subscription_id, "api_version": self.api_version, "query": query},
            )
            for item in body.get("data", [])
            if isinstance(item, dict)
        ]

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in (401,):
            raise AuthenticationError("Azure authentication failed")
        if response.status_code == 403:
            raise AuthorizationError("Azure permission denied")
        if response.status_code == 404:
            raise NotFoundError("Azure resource was not found")
        if response.status_code == 429:
            raise RateLimitedError("Azure Resource Graph rate limited the request")
        if response.status_code >= 500:
            raise UpstreamError(f"Azure upstream error: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise UpstreamError(f"Azure request failed: HTTP {response.status_code}")
