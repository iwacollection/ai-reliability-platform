from .client import LokiClient
from .models import LokiQueryRequest, LokiQueryResponse


class LokiMCPTools:
    """MCP exposed Loki investigation tools."""

    def __init__(self, client: LokiClient):
        self.client = client

    def query_logs(self, request: LokiQueryRequest) -> LokiQueryResponse:
        try:
            data = self.client.query_range(
                request.query,
                request.start,
                request.end,
                request.limit,
            )
            return LokiQueryResponse(success=True, data=data)
        except Exception as exc:
            return LokiQueryResponse(success=False, data={}, error=str(exc))
