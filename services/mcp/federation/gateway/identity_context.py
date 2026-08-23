"""
Identity context passed through MCP Federation Gateway.

Used after Azure AD / Workload Identity authentication.
"""

from dataclasses import dataclass, field


@dataclass
class MCPIdentityContext:
    principal: str
    tenant_id: str
    roles: list[str] = field(default_factory=list)
    provider: str = "azure-ad"
    workload_identity: str | None = None

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def can_execute(self, capability: str) -> bool:
        if "mcp-admin" in self.roles:
            return True
        return capability.startswith("read") and "mcp-reader" in self.roles
