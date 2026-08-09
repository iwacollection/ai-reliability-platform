from pathlib import Path
from uuid import uuid4

import pytest

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.approval.manager import (
    ApprovalManager,
)
from services.agent_runtime.app.approval.models import (
    ApprovalStatus,
)
from services.agent_runtime.app.approval.service import (
    ApprovalService,
)
from services.agent_runtime.app.approval.store import (
    ApprovalStore,
)
from services.agent_runtime.app.runtime.action_runtime import (
    ActionRuntime,
)


def _create_service(
    db_path: Path,
) -> ApprovalService:
    return ApprovalService(
        manager=ApprovalManager(
            store=ApprovalStore(
                db_path=db_path
            )
        )
    )


def _healing_result(
    *,
    untrusted_namespace: str | None = None,
    untrusted_cluster: str | None = None,
) -> dict:
    data = {
        "action": "increase_memory_limit",
        "target": "payment-api",
        "risk": "medium",
        "reason": "Pod memory limit exceeded",
    }

    if untrusted_namespace is not None:
        data["namespace"] = (
            untrusted_namespace
        )

    if untrusted_cluster is not None:
        data["cluster"] = (
            untrusted_cluster
        )

    return {
        "agent": "healing",
        "success": True,
        "score": 1.0,
        "message": "Increase memory limit",
        "data": data,
    }


class _CapturingExecutor:
    def __init__(self) -> None:
        self.plan: ActionPlan | None = None

    async def execute(
        self,
        plan: ActionPlan,
    ) -> dict:
        self.plan = plan

        return {
            "success": True,
            "action": plan.type.value,
            "target": plan.target,
        }


@pytest.mark.asyncio
async def test_action_scope_survives_approval_restart(
    tmp_path: Path,
) -> None:
    """
    ApprovalStore persists the complete ActionPlan, including resource scope.

    Every service below owns a separate Store instance to simulate process
    restarts around approval creation, human approval, and final recovery.
    """

    db_path = tmp_path / "approvals.db"
    incident_id = uuid4()

    creator = _create_service(
        db_path
    )

    request = await creator.create_approval(
        action=ActionPlan(
            type=(
                ActionType.INCREASE_MEMORY_LIMIT
            ),
            target="payment-api",
            namespace="payment",
            cluster="production-a",
            risk=ActionRisk.MEDIUM,
        ),
        reason=(
            "Memory remediation requires approval"
        ),
        incident_id=incident_id,
    )

    restarted_reader = _create_service(
        db_path
    )

    restored_pending = (
        await restarted_reader.get(
            request.id
        )
    )

    assert restored_pending is not None
    assert restored_pending.status == (
        ApprovalStatus.PENDING
    )
    assert restored_pending.incident_id == (
        incident_id
    )
    assert restored_pending.action.namespace == (
        "payment"
    )
    assert restored_pending.action.cluster == (
        "production-a"
    )
    assert restored_pending.action.approved is False

    approved = await restarted_reader.approve(
        request.id
    )

    assert approved.status == (
        ApprovalStatus.APPROVED
    )
    assert approved.action.approved is True

    final_reader = _create_service(
        db_path
    )

    restored_approved = (
        await final_reader.get(
            request.id
        )
    )

    assert restored_approved is not None
    assert restored_approved.status == (
        ApprovalStatus.APPROVED
    )
    assert restored_approved.action.approved is True
    assert restored_approved.action.namespace == (
        "payment"
    )
    assert restored_approved.action.cluster == (
        "production-a"
    )


def test_legacy_action_plan_without_scope_is_compatible() -> None:
    """Old serialized ActionPlan data remains readable during migration."""

    legacy_plan = ActionPlan.model_validate(
        {
            "type": "restart_pod",
            "target": "legacy-pod",
            "risk": "low",
            "approved": False,
            "metadata": {},
        }
    )

    assert legacy_plan.namespace is None
    assert legacy_plan.cluster is None
    assert legacy_plan.type == (
        ActionType.RESTART_POD
    )


@pytest.mark.asyncio
async def test_action_runtime_scope_survives_approval_resume(
    tmp_path: Path,
) -> None:
    """
    Explicit caller scope is persisted before approval and reaches the
    executor after independent ApprovalStore instances simulate restarts.
    """

    db_path = tmp_path / "runtime-approvals.db"

    creator_service = _create_service(
        db_path
    )
    creator_runtime = ActionRuntime(
        approval_service=creator_service
    )

    plan, pending = await creator_runtime.execute(
        _healing_result(),
        namespace="  payment  ",
        cluster="  production-a  ",
    )

    assert pending["status"] == (
        "pending_approval"
    )
    assert plan.namespace == "payment"
    assert plan.cluster == "production-a"

    approver_service = _create_service(
        db_path
    )
    approval = await approver_service.get(
        pending["approval_id"]
    )

    assert approval is not None
    assert approval.action.namespace == (
        "payment"
    )
    assert approval.action.cluster == (
        "production-a"
    )

    await approver_service.approve(
        pending["approval_id"]
    )

    resume_service = _create_service(
        db_path
    )
    resumed_runtime = ActionRuntime(
        approval_service=resume_service
    )
    executor = _CapturingExecutor()
    resumed_runtime.executor = executor

    execution = await resumed_runtime.resume(
        pending["approval_id"]
    )

    assert execution["success"] is True
    assert executor.plan is not None
    assert executor.plan.namespace == (
        "payment"
    )
    assert executor.plan.cluster == (
        "production-a"
    )


@pytest.mark.asyncio
async def test_action_runtime_does_not_trust_healing_scope(
    tmp_path: Path,
) -> None:
    """HealingAgent output cannot silently choose production scope."""

    db_path = tmp_path / "untrusted-scope.db"
    service = _create_service(
        db_path
    )
    runtime = ActionRuntime(
        approval_service=service
    )

    plan, pending = await runtime.execute(
        _healing_result(
            untrusted_namespace=(
                "llm-controlled-namespace"
            ),
            untrusted_cluster=(
                "llm-controlled-cluster"
            ),
        )
    )

    assert plan.namespace is None
    assert plan.cluster is None

    approval = await service.get(
        pending["approval_id"]
    )

    assert approval is not None
    assert approval.action.namespace is None
    assert approval.action.cluster is None
