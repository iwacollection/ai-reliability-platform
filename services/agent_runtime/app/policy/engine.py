from services.agent_runtime.app.action.models import ActionPlan
from services.agent_runtime.app.policy.models import PolicyDecision
from services.agent_runtime.app.policy.rules import DefaultHealingPolicy


class PolicyEngine:
    """Fail-closed policy evaluation for executable remediation actions."""

    def __init__(self, policies=None):
        self.policies = list(policies or [DefaultHealingPolicy()])

    def evaluate(self, action: ActionPlan) -> PolicyDecision:
        """Evaluate every policy and return the most restrictive decision.

        A policy exception is a security failure, not an implicit allow.
        DENY always wins, followed by REQUIRE_HUMAN, followed by ALLOW.
        """
        if not self.policies:
            return self._fail_closed("No policy is configured.")

        decisions: list[PolicyDecision] = []
        for policy in self.policies:
            try:
                decision = policy.evaluate(action)
            except Exception as exc:
                return self._fail_closed(
                    f"Policy evaluation failed for {getattr(policy, 'name', type(policy).__name__)}: {type(exc).__name__}"
                )
            if not isinstance(decision, PolicyDecision):
                return self._fail_closed(
                    f"Policy {getattr(policy, 'name', type(policy).__name__)} returned an invalid decision."
                )
            decisions.append(decision)

        denied = next((d for d in decisions if not d.allowed), None)
        if denied is not None:
            return denied.model_copy(update={"approved": False})

        human = next((d for d in decisions if d.require_human or not d.approved), None)
        if human is not None:
            return PolicyDecision(
                allowed=True,
                approved=False,
                require_human=True,
                reason=human.reason,
                policy=human.policy,
            )

        first = decisions[0]
        return PolicyDecision(
            allowed=True,
            approved=True,
            require_human=False,
            reason="All configured policies allowed the action.",
            policy=",".join(d.policy for d in decisions),
        )

    @staticmethod
    def _fail_closed(reason: str) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            approved=False,
            require_human=True,
            reason=reason,
            policy="fail_closed",
        )
