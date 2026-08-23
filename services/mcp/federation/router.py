"""MCP Federation Router.

Routes approved tool requests to selected MCP providers.
"""

from dataclasses import dataclass
from typing import Any

from .access import MCPAccessController


@dataclass
class MCPRouteRequest:
    identity: str
    capability: str
    environment: str
    payload: dict[str, Any]


@dataclass
class MCPRouteResult:
    provider_id: str
    capability: str
    accepted: bool
    reason: str


class MCPRouter:
    def __init__(self, access_controller: MCPAccessController):
        self.access_controller = access_controller

    def route(self, request: MCPRouteRequest) -> MCPRouteResult:
        provider = self.access_controller.resolve(
            identity=request.identity,
            capability=request.capability,
            environment=request.environment,
        )

        if provider is None:
            return MCPRouteResult(
                provider_id="",
                capability=request.capability,
                accepted=False,
                reason="no permitted healthy provider found",
            )

        return MCPRouteResult(
            provider_id=provider.provider_id,
            capability=request.capability,
            accepted=True,
            reason="route selected",
        )
