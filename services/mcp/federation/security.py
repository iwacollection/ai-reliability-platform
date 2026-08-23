"""
MCP Federation Enterprise Security Layer

Provides identity, authorization and audit primitives for MCP tool execution.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class SecurityContext:
    """Identity context propagated through federation gateway."""

    principal: str
    tenant_id: str
    roles: List[str] = field(default_factory=list)
    workload_identity: str | None = None


@dataclass
class MCPPermissionDecision:
    allowed: bool
    reason: str
    policy: str


class MCPPermissionPolicyEngine:
    """RBAC/ABAC style permission evaluation for MCP tools."""

    def evaluate(
        self,
        context: SecurityContext,
        tool_name: str,
        action: str,
    ) -> MCPPermissionDecision:
        if "mcp-admin" in context.roles:
            return MCPPermissionDecision(
                allowed=True,
                reason="administrator role granted",
                policy="admin-bypass",
            )

        if action.startswith("read") and "mcp-reader" in context.roles:
            return MCPPermissionDecision(
                allowed=True,
                reason="read permission granted",
                policy="reader-policy",
            )

        return MCPPermissionDecision(
            allowed=False,
            reason=f"permission denied for {tool_name}",
            policy="default-deny",
        )


@dataclass
class MCPAuditRecord:
    principal: str
    tool_name: str
    action: str
    decision: str


class MCPSecurityAudit:
    def __init__(self):
        self.records: list[MCPAuditRecord] = []

    def record(self, record: MCPAuditRecord):
        self.records.append(record)
