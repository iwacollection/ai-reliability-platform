import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.fingerprint import action_fingerprint
from services.agent_runtime.app.action.models import ActionPlan, ActionRisk, ActionType
from services.agent_runtime.app.approval.models import ApprovalRequest


def build_action():
    return ActionPlan(
        type=ActionType.RESTART_POD,
        target="payment-api",
        namespace="payment",
        cluster="prod-a",
        risk=ActionRisk.MEDIUM,
    )


def test_fingerprint_is_stable():
    action = build_action()
    assert action_fingerprint(action) == action_fingerprint(action.model_copy(deep=True))


def test_approval_binds_exact_action():
    action = build_action()
    request = ApprovalRequest(id="approval-1", action=action)
    assert request.action_fingerprint == action_fingerprint(action)


def test_tampered_fingerprint_is_rejected():
    with pytest.raises(ValidationError):
        ApprovalRequest(id="approval-1", action=build_action(), action_fingerprint="tampered")
