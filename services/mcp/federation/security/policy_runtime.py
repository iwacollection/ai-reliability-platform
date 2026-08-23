"""Dynamic authorization runtime for MCP federation."""

from .policy_models import (
    PolicyDecision,
    PolicyDecisionType,
    PolicyRequest,
)


class DynamicPolicyRuntime:
    """
    Evaluates MCP authorization requests.

    Evaluation order:
    1. identity/role checks
    2. capability checks
    3. environment/risk checks
    """

    def __init__(self, policies=None):
        self.policies = policies or {}

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        principal = request.subject.principal
        policy = self.policies.get(principal)

        if not policy:
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                reason="identity policy not found",
            )

        if request.capability not in policy.get("capabilities", []):
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                reason="capability denied",
                policy_name=principal,
            )

        if request.context.environment not in policy.get("environments", []):
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                reason="environment denied",
                policy_name=principal,
            )

        if request.context.risk_level == "high":
            return PolicyDecision(
                decision=PolicyDecisionType.REQUIRE_APPROVAL,
                reason="high risk action requires approval",
                policy_name=principal,
            )

        return PolicyDecision(
            decision=PolicyDecisionType.ALLOW,
            reason="policy matched",
            policy_name=principal,
        )
