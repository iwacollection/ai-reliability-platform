"""MCP Federation Gateway.

Binds federation routing decisions to real MCP tool execution.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from .router import MCPRouteRequest, MCPRouter


class MCPToolExecutor(Protocol):
    def execute(self, provider_id: str, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class MCPExecutionResult:
    accepted: bool
    provider_id: str
    cluster_id: str
    capability: str
    result: dict[str, Any]
    reason: str


class MCPFederationGateway:
    """Enterprise gateway between Agent and MCP providers.

    Flow:
    Agent -> Router -> MCP Provider -> Tool Execution
    """

    def __init__(self, router: MCPRouter, executor: MCPToolExecutor):
        self.router = router
        self.executor = executor

    def invoke(self, request: MCPRouteRequest) -> MCPExecutionResult:
        route = self.router.route(request)

        if not route.accepted:
            return MCPExecutionResult(
                accepted=False,
                provider_id=route.provider_id,
                cluster_id=route.cluster_id,
                capability=route.capability,
                result={},
                reason=route.reason,
            )

        result = self.executor.execute(
            provider_id=route.provider_id,
            capability=route.capability,
            payload=request.payload,
        )

        return MCPExecutionResult(
            accepted=True,
            provider_id=route.provider_id,
            cluster_id=route.cluster_id,
            capability=route.capability,
            result=result,
            reason="execution completed",
        )
