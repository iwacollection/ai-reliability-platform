"""Real GitHub REST connector for investigation evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from services.connectors.common import AuthenticationError, AuthorizationError, Evidence, NotFoundError, RateLimitedError, UpstreamError


class GitHubConnector:
    """Read-only GitHub connector; mutations remain outside this boundary."""

    def __init__(self, token: str, *, timeout: float = 10.0, client: httpx.AsyncClient | None = None):
        if not token.strip():
            raise ValueError("GitHub token cannot be empty")
        self._token = token
        self._timeout = timeout
        self._client = client

    async def get_commit(self, repository: str, sha: str) -> Evidence:
        data = await self._get(f"/repos/{repository}/commits/{sha}")
        return self._evidence("commit", sha, data)

    async def get_pull_request(self, repository: str, number: int) -> Evidence:
        if number < 1:
            raise ValueError("pull request number must be positive")
        data = await self._get(f"/repos/{repository}/pulls/{number}")
        return self._evidence("pull_request", str(number), data)

    async def get_workflow_run(self, repository: str, run_id: int) -> Evidence:
        if run_id < 1:
            raise ValueError("workflow run id must be positive")
        data = await self._get(f"/repos/{repository}/actions/runs/{run_id}")
        return self._evidence("workflow_run", str(run_id), data)

    async def _get(self, path: str) -> dict[str, Any]:
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        close = self._client is None
        try:
            response = await client.get(
                f"https://api.github.com{path}",
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json"},
            )
        finally:
            if close:
                await client.aclose()
        self._raise_for_status(response)
        body = response.json()
        if not isinstance(body, dict):
            raise UpstreamError("GitHub returned a non-object response")
        return body

    @staticmethod
    def _evidence(kind: str, resource_id: str, payload: dict[str, Any]) -> Evidence:
        timestamp = payload.get("committer", {}).get("date") if kind == "commit" else payload.get("merged_at") or payload.get("created_at")
        observed = None
        if isinstance(timestamp, str):
            try:
                observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                pass
        return Evidence(
            source="github",
            resource_type=kind,
            resource_id=resource_id,
            payload=payload,
            observed_at=observed,
            retrieved_at=datetime.now(UTC),
            provenance={"resource": resource_id, "kind": kind, "api": "github-rest"},
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 401:
            raise AuthenticationError("GitHub authentication failed")
        if response.status_code == 403:
            if response.headers.get("x-ratelimit-remaining") == "0":
                raise RateLimitedError("GitHub API rate limit exhausted")
            raise AuthorizationError("GitHub permission denied")
        if response.status_code == 404:
            raise NotFoundError("GitHub resource was not found")
        if response.status_code == 429:
            raise RateLimitedError("GitHub API rate limited the request")
        if response.status_code >= 500:
            raise UpstreamError(f"GitHub upstream error: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise UpstreamError(f"GitHub request failed: HTTP {response.status_code}")
