"""MCP Federation Router.

Routes investigation requests across multiple environments.
"""

from dataclasses import dataclass
from typing import Any

from .access import MCPAccessController
from .registry import MCPRegistry


@dataclass
class MCPRouteRequest:
    identity: str
    capability: str
    environment: str
    payload: dict[str, Any]


@dataclass
class MCPRouteResult:
    provider_id: str
    cluster_id: str
    capability: str
    accepted: bool
    reason: str


class MCPRouter:
    """Enterprise multi environment MCP router.

    Routing order:
    identity permission
    -> environment discovery
    -> capability provider selection
    -> target cluster binding
    """

    def __init__(
        self,
        access_controller: MCPAccessController,
        registry: MCPRegistry,
    ):
        self.access_controller = access_controller
        self.registry = registry

    def route(self, request: MCPRouteRequest) -> MCPRouteResult:
        provider = self.access_controller.resolve(
            identity=request.identity,
            capability=request.capability,
            environment=request.environment,
        )

        if provider is None:
            return MCPRouteResult(
                provider_id="",
                cluster_id="",
                capability=request.capability,
                accepted=False,
                reason="no permitted provider found",
            )

        clusters = self.registry.discover_clusters(
            request.environment
        )

        if not clusters:
            return MCPRouteResult(
                provider_id=provider.provider_id,
                cluster_id="",
                capability=request.capability,
                accepted=False,
                reason="no cluster available",
            )

        return MCPRouteResult(
            provider_id=provider.provider_id,
            cluster_id=clusters[0].cluster_id,
            capability=request.capability,
            accepted=True,
            reason="route selected",
        )
