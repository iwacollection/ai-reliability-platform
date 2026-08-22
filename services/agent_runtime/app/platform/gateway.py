from dataclasses import dataclass
from typing import Any


@dataclass
class AgentRequest:
    tenant_id: str
    user_id: str
    incident_id: str
    payload: dict[str, Any]


class AgentGateway:
    """Unified entry point for agent requests.

    Future integrations:
    - authentication
    - authorization
    - request tracing
    - API management
    """

    def handle(self, request: AgentRequest) -> dict[str, Any]:
        return {
            "tenant_id": request.tenant_id,
            "incident_id": request.incident_id,
            "accepted": True,
        }
