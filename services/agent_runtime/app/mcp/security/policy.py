from dataclasses import dataclass
from enum import Enum


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


@dataclass
class ToolPolicy:
    tool: str
    action: str
    decision: PermissionDecision


class MCPPermissionPolicy:
    def __init__(self, policies=None):
        self.policies = policies or []

    def check(self, tool: str, action: str) -> PermissionDecision:
        for policy in self.policies:
            if policy.tool == tool and policy.action == action:
                return policy.decision
        return PermissionDecision.DENY
