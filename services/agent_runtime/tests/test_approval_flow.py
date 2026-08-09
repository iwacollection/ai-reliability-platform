import asyncio

import pytest

from services.agent_runtime.app.approval.models import (
    ApprovalStatus,
)
from services.agent_runtime.app.approval.service import (
    ApprovalService,
)
from services.agent_runtime.app.approval.store import (
    ApprovalConflictError,
)
from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.runtime.action_runtime import (
    ActionRuntime,
)


def build_legacy_healing_result() -> dict:
    """
    Build the old flat HealingAgent response.
    """

    return {
        "agent": "healing",
        "success": True,
        "score": 1.0,
        "message": "increase memory limit",
        "data": {
            "action": "increase_memory_limit",
            "target": "payment-api",
            "risk": "medium",
            "reason": "Pod memory limit exceeded",
        },
    }


def build_nested_healing_result() -> dict:
    """
    Build the current nested HealingAgent response.
    """

    return {
        "agent": "healing",
        "success": True,
        "score": 1.0,
        "message": "increase memory limit",
        "data": {
            "action": {
                "type": "increase_memory_limit",
                "target": "payment-api",
            },
            "risk": "medium",
            "reason": "Pod memory limit exceeded",
            "rollback": (
                "Restore the previous memory limit"
            ),
            "verification": (
                "Verify OOMKilled events disappear"
            ),
            "approval_required": True,
        },
    }


def build_runtime() -> tuple[
    ApprovalService,
    ActionRuntime,
]:
    approval_service = ApprovalService()

    action_runtime = ActionRuntime(
        approval_service=approval_service
    )

    return approval_service, action_runtime


@pytest.mark.asyncio
async def test_approval_resume_flow():
    """
    Legacy approvals without incident_id remain executable.
    """

    approval_service, action_runtime = (
        build_runtime()
    )

    plan, result = await action_runtime.execute(
        build_legacy_healing_result()
    )

    assert result["status"] == (
        "pending_approval"
    )
    assert result["incident_id"] is None

    approval_id = result["approval_id"]

    approval = await approval_service.approve(
        approval_id
    )

    assert approval.status == (
        ApprovalStatus.APPROVED
    )
    assert approval.action.approved is True

    execution = await action_runtime.resume(
        approval_id
    )

    assert execution["success"] is True
    assert execution["action"] == (
        "increase_memory_limit"
    )
    assert plan.target == "payment-api"


@pytest.mark.asyncio
async def test_approval_stores_incident_id():
    """
    A new approval must retain its originating Incident ID.
    """

    approval_service, action_runtime = (
        build_runtime()
    )
    incident = IncidentState()

    _, result = await action_runtime.execute(
        build_nested_healing_result(),
        incident=incident,
    )

    assert result["status"] == (
        "pending_approval"
    )
    assert result["incident_id"] == str(
        incident.id
    )

    approval = await approval_service.get(
        result["approval_id"]
    )

    assert approval is not None
    assert approval.incident_id == (
        incident.id
    )
    assert incident.status == (
        IncidentStatus.CONFIRMED
    )
    assert incident.reason is not None
    assert "approval" in (
        incident.reason.lower()
    )


@pytest.mark.asyncio
async def test_linked_approval_requires_incident_context():
    """
    A linked approval cannot resume without loading its Incident.
    """

    approval_service, action_runtime = (
        build_runtime()
    )
    incident = IncidentState()

    _, result = await action_runtime.execute(
        build_nested_healing_result(),
        incident=incident,
    )

    approval_id = result["approval_id"]

    await approval_service.approve(
        approval_id
    )

    execution = await action_runtime.resume(
        approval_id
    )

    assert execution["success"] is False
    assert execution["status"] == (
        "incident_context_required"
    )
    assert execution["incident_id"] == str(
        incident.id
    )
    assert incident.status == (
        IncidentStatus.CONFIRMED
    )


@pytest.mark.asyncio
async def test_approval_rejects_incident_mismatch():
    """
    An approval issued for one Incident cannot execute against another.
    """

    approval_service, action_runtime = (
        build_runtime()
    )

    original_incident = IncidentState()
    wrong_incident = IncidentState()

    _, result = await action_runtime.execute(
        build_nested_healing_result(),
        incident=original_incident,
    )

    approval_id = result["approval_id"]

    await approval_service.approve(
        approval_id
    )

    execution = await action_runtime.resume(
        approval_id,
        incident=wrong_incident,
    )

    assert execution["success"] is False
    assert execution["status"] == (
        "incident_mismatch"
    )
    assert execution[
        "approval_incident_id"
    ] == str(original_incident.id)
    assert execution[
        "provided_incident_id"
    ] == str(wrong_incident.id)

    assert original_incident.status == (
        IncidentStatus.CONFIRMED
    )
    assert wrong_incident.status == (
        IncidentStatus.NEW
    )


@pytest.mark.asyncio
async def test_approved_action_enters_healing():
    """
    A correctly matched approval enters HEALING.

    Execution success alone cannot mark the Incident RESOLVED.
    """

    approval_service, action_runtime = (
        build_runtime()
    )
    incident = IncidentState()

    _, result = await action_runtime.execute(
        build_nested_healing_result(),
        incident=incident,
    )

    approval_id = result["approval_id"]

    await approval_service.approve(
        approval_id
    )

    execution = await action_runtime.resume(
        approval_id,
        incident=incident,
    )

    assert execution["success"] is True
    assert execution["action"] == (
        "increase_memory_limit"
    )
    assert execution["target"] == (
        "payment-api"
    )
    assert execution["incident_id"] == str(
        incident.id
    )
    assert execution["incident_status"] == (
        IncidentStatus.HEALING.value
    )
    assert incident.status == (
        IncidentStatus.HEALING
    )
    assert incident.reason is not None
    assert "awaiting verification" in (
        incident.reason.lower()
    )


@pytest.mark.asyncio
async def test_concurrent_approve_is_idempotent():
    """
    Concurrent identical decisions must both observe APPROVED.
    """

    approval_service, action_runtime = (
        build_runtime()
    )
    incident = IncidentState()

    _, result = await action_runtime.execute(
        build_nested_healing_result(),
        incident=incident,
    )

    approval_id = result["approval_id"]

    approvals = await asyncio.gather(
        approval_service.approve(
            approval_id
        ),
        approval_service.approve(
            approval_id
        ),
    )

    assert all(
        approval.status
        == ApprovalStatus.APPROVED
        for approval in approvals
    )

    assert all(
        approval.action.approved is True
        for approval in approvals
    )

    stored = await approval_service.get(
        approval_id
    )

    assert stored is not None
    assert stored.status == (
        ApprovalStatus.APPROVED
    )
    assert stored.action.approved is True


@pytest.mark.asyncio
async def test_concurrent_conflicting_decisions():
    """
    Concurrent APPROVED and REJECTED decisions cannot both succeed.
    """

    approval_service, action_runtime = (
        build_runtime()
    )
    incident = IncidentState()

    _, result = await action_runtime.execute(
        build_nested_healing_result(),
        incident=incident,
    )

    approval_id = result["approval_id"]

    decisions = await asyncio.gather(
        approval_service.approve(
            approval_id
        ),
        approval_service.reject(
            approval_id
        ),
        return_exceptions=True,
    )

    successful = [
        decision
        for decision in decisions
        if not isinstance(
            decision,
            BaseException,
        )
    ]

    failed = [
        decision
        for decision in decisions
        if isinstance(
            decision,
            BaseException,
        )
    ]

    assert len(successful) == 1
    assert len(failed) == 1

    assert isinstance(
        failed[0],
        (
            ApprovalConflictError,
            ValueError,
        ),
    )

    stored = await approval_service.get(
        approval_id
    )

    assert stored is not None

    assert stored.status in {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
    }

    if stored.status == ApprovalStatus.APPROVED:
        assert stored.action.approved is True

        repeated = await approval_service.approve(
            approval_id
        )

    else:
        assert stored.action.approved is False

        repeated = await approval_service.reject(
            approval_id
        )

    assert repeated.status == stored.status
    assert (
        repeated.action.approved
        == stored.action.approved
    )