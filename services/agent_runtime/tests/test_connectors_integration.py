import httpx
import pytest

from services.connectors.azure.connector import AzureConnector
from services.connectors.common import AuthorizationError, RateLimitedError
from services.connectors.github.connector import GitHubConnector


@pytest.mark.asyncio
async def test_github_commit_is_normalized_with_provenance():
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"sha": "abc123", "commit": {"committer": {"date": "2026-09-01T00:00:00Z"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        evidence = await GitHubConnector("test-token", client=client).get_commit("org/repo", "abc123")

    assert evidence.source == "github"
    assert evidence.resource_type == "commit"
    assert evidence.resource_id == "abc123"
    assert evidence.provenance["api"] == "github-rest"
    assert evidence.observed_at is not None


@pytest.mark.asyncio
async def test_github_rate_limit_is_structured():
    async def handler(request):
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "rate limit"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RateLimitedError):
            await GitHubConnector("test-token", client=client).get_commit("org/repo", "abc123")


@pytest.mark.asyncio
async def test_azure_resource_graph_is_normalized():
    async def handler(request):
        body = request.read()
        assert b"subscriptions" in body
        return httpx.Response(200, json={"data": [{"id": "/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Web/sites/api", "type": "microsoft.web/sites", "name": "api"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        evidence = await AzureConnector("azure-token", client=client).query_resources("s1", "Resources | take 1")

    assert len(evidence) == 1
    assert evidence[0].source == "azure"
    assert evidence[0].resource_id.endswith("/api")
    assert evidence[0].provenance["subscription_id"] == "s1"


@pytest.mark.asyncio
async def test_azure_permission_denied_is_not_retried_as_success():
    async def handler(request):
        return httpx.Response(403, json={"error": "forbidden"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthorizationError):
            await AzureConnector("azure-token", client=client).query_resources("s1", "Resources | take 1")
