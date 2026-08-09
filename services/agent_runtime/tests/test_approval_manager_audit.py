import asyncio

import pytest

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)
from services.agent_runtime.app.approval.manager import (
    ApprovalDecisionConflictError,
    ApprovalManager,
)
from services.agent_runtime.app.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
)
from services.agent_runtime.app.approval.store import (
    ApprovalStore,
)


def create_manager(
    db_path,
) -> ApprovalManager:
    return ApprovalManager(
        store=ApprovalStore(
            db_path=db_path
        )
    )


def create_plan() -> ActionPlan:
    return ActionPlan(
        type=ActionType.INCREASE_MEMORY_LIMIT,
        target="payment-api",
        namespace="payment",
        cluster="production-a",
    )


async def create_pending(
    manager: ApprovalManager,
) -> ApprovalRequest:
    return await manager.create_request(
        action=create_plan(),
        reason="Medium risk action requires approval",
    )


def audited_approval() -> dict:
    return {
        "operator_id": "sre-user-1",
        "idempotency_key": "approve-request-1",
        "reason": "Validated the remediation plan",
        "metadata": {
            "source": "api",
            "ticket": "INC-1001",
        },
    }


@pytest.mark.asyncio
async def test_exact_approval_replay_is_idempotent_across_instances(
    tmp_path,
):
    db_path = tmp_path / "approvals.db"
    first_manager = create_manager(
        db_path
    )
    second_manager = create_manager(
        db_path
    )
    request = await create_pending(
        first_manager
    )

    first = await first_manager.approve(
        request.id,
        **audited_approval(),
    )
    replay = await second_manager.approve(
        request.id,
        **audited_approval(),
    )

    assert first.status == (
        ApprovalStatus.APPROVED
    )
    assert first.action.approved is True
    assert first.decision is not None
    assert first.decision.operator_id == (
        "sre-user-1"
    )
    assert first.decision.idempotency_key == (
        "approve-request-1"
    )

    assert replay.decision is not None
    assert replay.decision == first.decision
    assert replay.updated_at == first.updated_at

    restored = await second_manager.get_request(
        request.id
    )

    assert restored is not None
    assert restored.decision == first.decision


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_fields",
    [
        {
            "idempotency_key": (
                "approve-request-2"
            )
        },
        {
            "operator_id": "sre-user-2"
        },
        {
            "reason": "Different reason"
        },
        {
            "metadata": {
                "source": "api",
                "ticket": "INC-2002",
            }
        },
    ],
)
async def test_changed_audited_replay_is_rejected(
    tmp_path,
    changed_fields,
):
    db_path = tmp_path / "approvals.db"
    first_manager = create_manager(
        db_path
    )
    second_manager = create_manager(
        db_path
    )
    request = await create_pending(
        first_manager
    )

    original = audited_approval()
    await first_manager.approve(
        request.id,
        **original,
    )

    changed = audited_approval()
    changed.update(
        changed_fields
    )

    with pytest.raises(
        ApprovalDecisionConflictError
    ):
        await second_manager.approve(
            request.id,
            **changed,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision_arguments",
    [
        {
            "operator_id": "sre-user-1"
        },
        {
            "idempotency_key": (
                "approve-request-1"
            )
        },
        {
            "reason": "Approval reason only"
        },
    ],
)
async def test_incomplete_audit_identity_keeps_request_pending(
    tmp_path,
    decision_arguments,
):
    manager = create_manager(
        tmp_path / "approvals.db"
    )
    request = await create_pending(
        manager
    )

    with pytest.raises(
        ValueError,
        match="operator_id and idempotency_key",
    ):
        await manager.approve(
            request.id,
            **decision_arguments,
        )

    restored = await manager.get_request(
        request.id
    )

    assert restored is not None
    assert restored.status == (
        ApprovalStatus.PENDING
    )
    assert restored.action.approved is False
    assert restored.decision is None


@pytest.mark.asyncio
async def test_concurrent_exact_audited_approval_is_idempotent(
    tmp_path,
):
    db_path = tmp_path / "approvals.db"
    first_manager = create_manager(
        db_path
    )
    second_manager = create_manager(
        db_path
    )
    request = await create_pending(
        first_manager
    )

    first, second = await asyncio.gather(
        first_manager.approve(
            request.id,
            **audited_approval(),
        ),
        second_manager.approve(
            request.id,
            **audited_approval(),
        ),
    )

    assert first.status == (
        ApprovalStatus.APPROVED
    )
    assert second.status == (
        ApprovalStatus.APPROVED
    )
    assert first.decision is not None
    assert second.decision == first.decision


@pytest.mark.asyncio
async def test_concurrent_opposite_audited_decisions_have_one_winner(
    tmp_path,
):
    db_path = tmp_path / "approvals.db"
    approve_manager = create_manager(
        db_path
    )
    reject_manager = create_manager(
        db_path
    )
    request = await create_pending(
        approve_manager
    )

    results = await asyncio.gather(
        approve_manager.approve(
            request.id,
            operator_id="sre-approver",
            idempotency_key="approve-key",
            reason="Approve remediation",
        ),
        reject_manager.reject(
            request.id,
            operator_id="sre-reviewer",
            idempotency_key="reject-key",
            reason="Reject remediation",
        ),
        return_exceptions=True,
    )

    successful = [
        result
        for result in results
        if isinstance(
            result,
            ApprovalRequest,
        )
    ]
    failed = [
        result
        for result in results
        if isinstance(
            result,
            ApprovalDecisionConflictError,
        )
    ]

    assert len(successful) == 1
    assert len(failed) == 1

    restored = await approve_manager.get_request(
        request.id
    )

    assert restored is not None
    assert restored.decision is not None
    assert restored.status == (
        restored.decision.status
    )
    assert restored.action.approved is (
        restored.status
        == ApprovalStatus.APPROVED
    )


@pytest.mark.asyncio
async def test_legacy_terminal_decision_cannot_be_claimed_by_new_key(
    tmp_path,
):
    db_path = tmp_path / "approvals.db"
    legacy_manager = create_manager(
        db_path
    )
    audited_manager = create_manager(
        db_path
    )
    request = await create_pending(
        legacy_manager
    )

    legacy = await legacy_manager.approve(
        request.id
    )

    assert legacy.status == (
        ApprovalStatus.APPROVED
    )
    assert legacy.decision is None

    with pytest.raises(
        ApprovalDecisionConflictError,
        match="no persisted idempotency decision",
    ):
        await audited_manager.approve(
            request.id,
            **audited_approval(),
        )
