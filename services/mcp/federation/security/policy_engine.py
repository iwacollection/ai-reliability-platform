"""Unified MCP Federation authorization policy engine."""

from dataclasses import dataclass

from .abac import ABACEngine, AccessAttributes
from .rbac import RBACEngine


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


class MCPPolicyEngine:
    def __init__(self):
        self.rbac = RBACEngine()
        self.abac = ABACEngine()

    def evaluate(
        self,
        roles: list[str],
        permission: str,
        attributes: AccessAttributes,
        environment: str | None = None,
    ) -> PolicyDecision:
        if not self.rbac.allowed(roles, permission):
            return PolicyDecision(False, "rbac denied")

        if not self.abac.evaluate(attributes, environment):
            return PolicyDecision(False, "abac denied")

        return PolicyDecision(True, "policy allowed")
