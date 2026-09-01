import pytest

from services.agent_runtime.app.action.models import ActionPlan, ActionRisk, ActionType
from services.agent_runtime.app.policy.engine import PolicyEngine
from services.agent_runtime.app.policy.models import PolicyDecision


class ExplodingPolicy:
    name = "exploding_policy"

    def evaluate(self, action):
        raise RuntimeError("policy backend unavailable")


class DenyPolicy:
    name = "deny_policy"

    def evaluate(self, action):
        return PolicyDecision(
            allowed=False,
            approved=False,
            require_human=True,
            reason="explicit deny",
            policy=self.name,
        )


class AllowPolicy:
    name = "allow_policy"

    def evaluate(self, action):
        return PolicyDecision(
            allowed=True,
            approved=True,
            require_human=False,
            reason="allow",
            policy=self.name,
        )


def action():
    return ActionPlan(
        type=ActionType.RESTART_POD,
        target="payment-api",
        namespace="payment",
        risk=ActionRisk.LOW,
    )


def test_policy_exception_fails_closed():
    decision = PolicyEngine([ExplodingPolicy()]).evaluate(action())
    assert decision.allowed is False
    assert decision.approved is False
    assert decision.require_human is True
    assert decision.policy == "fail_closed"


def test_all_policies_must_allow():
    decision = PolicyEngine([AllowPolicy(), DenyPolicy()]).evaluate(action())
    assert decision.allowed is False
    assert decision.approved is False
    assert decision.policy == "deny_policy"


def test_empty_policy_set_fails_closed():
    decision = PolicyEngine([]).evaluate(action())
    assert decision.allowed is False
    assert decision.require_human is True
