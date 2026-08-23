from .client import OpenTelemetryClient
from .models import TraceQueryRequest, TraceResponse


class OpenTelemetryMCPTools:
    """MCP exposed distributed tracing investigation tools."""

    def __init__(self, client: OpenTelemetryClient):
        self.client = client

    def query_trace(self, request: TraceQueryRequest) -> TraceResponse:
        try:
            data = self.client.query_trace(
                trace_id=request.trace_id,
                service_name=request.service_name,
                start=request.start,
                end=request.end,
            )
            return TraceResponse(success=True, data=data)
        except Exception as exc:
            return TraceResponse(success=False, data={}, error=str(exc))
