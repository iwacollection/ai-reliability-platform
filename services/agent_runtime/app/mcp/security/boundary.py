from .policy import MCPPermissionPolicy, PermissionDecision
from ..audit import MCPAuditLogger, MCPAuditRecord


class MCPSecurityBoundary:
    def __init__(self, policy: MCPPermissionPolicy, audit: MCPAuditLogger):
        self.policy = policy
        self.audit = audit

    def authorize(self, tool: str, action: str, request: dict):
        decision = self.policy.check(tool, action)
        self.audit.record(
            MCPAuditRecord(
                tool=tool,
                action=action,
                request=request,
                response=None,
                decision=decision.value,
            )
        )
        return decision
