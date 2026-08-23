"""
MCP Federation Gateway Request Guard

Protects tool execution boundary with identity and capability checks.
"""

from dataclasses import dataclass


@dataclass
class GuardDecision:
    allowed: bool
    reason: str


class MCPRequestGuard:
    def validate(self, identity_context, capability: str) -> GuardDecision:
        if identity_context is None:
            return GuardDecision(False, "missing identity context")

        if not identity_context.can_execute(capability):
            return GuardDecision(False, "capability denied by identity policy")

        return GuardDecision(True, "identity authorized")
