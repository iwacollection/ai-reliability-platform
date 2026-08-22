from dataclasses import dataclass
from typing import Dict, List


@dataclass
class CapabilityRequest:
    capability: str
    required_permission: str = "readonly"


@dataclass
class CapabilityResponse:
    server: str
    capability: str
    accepted: bool
    permissions: List[str]


class CapabilityNegotiator:
    def negotiate(self, request: CapabilityRequest, servers: Dict):
        responses = []
        for name, server in servers.items():
            capabilities = getattr(server, "capabilities", [])
            for item in capabilities:
                if item.name == request.capability:
                    responses.append(
                        CapabilityResponse(
                            server=name,
                            capability=request.capability,
                            accepted=True,
                            permissions=item.permissions,
                        )
                    )
        return responses
