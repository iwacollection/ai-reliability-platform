import asyncio

from pathlib import Path
from uuid import UUID

import pytest

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionRecord,
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.execution_service import (
    ActionExecutionService,
)
from services.agent_runtime.app.action.execution_store import (
    ActionExecutionConflictError,
    ActionExecutionStore,
)
from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)


INCIDENT_ID = UUID(
    "11111111-2222-4333-8444-555555555555"
)


def build_service(
    db_path: Path,
) -> ActionExecutionService:
    return ActionExecutionService(
        store=ActionExecutionStore(
            db_path=db_path
        )
    )


def build_action() -> ActionPlan:
    return ActionPlan(
        type=(
            ActionType.INCREASE_MEMORY_LIMIT
        ),
        target="payment-api",
        namespace="payment",
        cluster="production-tw",
        risk=ActionRisk.MEDIUM,
        approved=True,
        metadata={
            "memory_limit": "1Gi",
        },
    )


async def claim_execution(
    service: ActionExecutionService,
    *,
    approval_id: str = "approval-1",
    idempotency_key: str = "execution-1",
):
    return await service.claim(
        approval_id=approval_id,
        incident_id=INCIDENT_ID,
        operator_id="sre-1",
        idempotency_key=idempotency_key,
        action=build_action(),
        metadata={
            "request_id": "request-1",
        },
    )


@pytest.mark.asyncio
async def test_service_claim_is_persistent_and_idempotent(
    tmp_path: Path,
):
    db_path = tmp_path / "service-claim.db"
    first_service = build_service(db_path)
    restarted_service = build_service(db_path)

    first = await claim_execution(
        first_service
    )
    replay = await claim_execution(
        restarted_service
    )

    assert first.created is True
    assert replay.created is False
    assert replay.execution == first.execution
    assert replay.execution.incident_id == INCIDENT_ID
    assert replay.execution.action.namespace == "payment"
    assert replay.execution.action.cluster == "production-tw"
    assert replay.execution.automatic_replay_allowed is False

    restored = await restarted_service.get_by_approval(
        "approval-1"
    )

    assert restored == first.execution


@pytest.mark.asyncio
async def test_concurrent_identical_success_is_idempotent(
    tmp_path: Path,
):
    db_path = tmp_path / "concurrent-success.db"
    first_service = build_service(db_path)
    second_service = build_service(db_path)

    claim = await claim_execution(
        first_service
    )
    execution_id = str(
        claim.execution.id
    )
    result = {
        "success": True,
        "action": "increase_memory_limit",
        "target": "payment-api",
    }

    first, second = await asyncio.gather(
        first_service.complete(
            execution_id,
            result,
        ),
        second_service.complete(
            execution_id,
            result,
        ),
    )

    assert first.id == second.id
    assert first.status == (
        ActionExecutionStatus.SUCCEEDED
    )
    assert second.status == (
        ActionExecutionStatus.SUCCEEDED
    )
    assert first.result == result
    assert second.result == result
    assert first.is_terminal is True


@pytest.mark.asyncio
async def test_failure_result_and_error_are_persisted(
    tmp_path: Path,
):
    db_path = tmp_path / "failed-execution.db"
    first_service = build_service(db_path)
    restarted_service = build_service(db_path)

    claim = await claim_execution(
        first_service
    )
    result = {
        "success": False,
        "status": "executor_failed",
        "error_type": "KubernetesApiError",
        "error_message": "request was denied",
    }

    failed = await first_service.complete(
        str(claim.execution.id),
        result,
    )

    assert failed.status == (
        ActionExecutionStatus.FAILED
    )
    assert failed.result == result
    assert failed.error_type == "KubernetesApiError"
    assert failed.error_message == "request was denied"
    assert failed.is_terminal is True

    replay = await restarted_service.complete(
        str(claim.execution.id),
        result,
    )

    assert replay == failed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {},
        {
            "success": None,
        },
        {
            "success": "true",
        },
        {
            "success": 1,
        },
    ],
)
async def test_ambiguous_success_value_becomes_indeterminate(
    tmp_path: Path,
    result: dict,
):
    service = build_service(
        tmp_path / "ambiguous-result.db"
    )
    claim = await claim_execution(
        service
    )

    execution = await service.complete(
        str(claim.execution.id),
        result,
    )

    assert execution.status == (
        ActionExecutionStatus.INDETERMINATE
    )
    assert execution.requires_reconciliation is True
    assert execution.is_terminal is False
    assert execution.automatic_replay_allowed is False
    assert execution.error_type == (
        "IndeterminateExecution"
    )


@pytest.mark.asyncio
async def test_non_dictionary_result_is_rejected_without_transition(
    tmp_path: Path,
):
    service = build_service(
        tmp_path / "invalid-result.db"
    )
    claim = await claim_execution(
        service
    )

    with pytest.raises(
        TypeError,
        match="must be a dictionary",
    ):
        await service.complete(
            str(claim.execution.id),
            [
                "invalid",
            ],
        )

    persisted = await service.get(
        str(claim.execution.id)
    )

    assert persisted is not None
    assert persisted.status == (
        ActionExecutionStatus.RUNNING
    )


@pytest.mark.asyncio
async def test_success_and_failure_race_has_one_terminal_winner(
    tmp_path: Path,
):
    db_path = tmp_path / "outcome-race.db"
    first_service = build_service(db_path)
    second_service = build_service(db_path)

    claim = await claim_execution(
        first_service
    )
    execution_id = str(
        claim.execution.id
    )

    outcomes = await asyncio.gather(
        first_service.succeed(
            execution_id,
            {
                "success": True,
            },
        ),
        second_service.fail(
            execution_id,
            {
                "success": False,
            },
            error_type="ExecutorError",
        ),
        return_exceptions=True,
    )

    persisted_outcomes = [
        outcome
        for outcome in outcomes
        if isinstance(
            outcome,
            ActionExecutionRecord,
        )
    ]
    conflicts = [
        outcome
        for outcome in outcomes
        if isinstance(
            outcome,
            ActionExecutionConflictError,
        )
    ]

    assert len(persisted_outcomes) == 1
    assert len(conflicts) == 1

    persisted = await first_service.get(
        execution_id
    )

    assert persisted == persisted_outcomes[0]
    assert persisted is not None
    assert persisted.status in {
        ActionExecutionStatus.SUCCEEDED,
        ActionExecutionStatus.FAILED,
    }


@pytest.mark.asyncio
async def test_indeterminate_blocks_normal_terminal_methods(
    tmp_path: Path,
):
    service = build_service(
        tmp_path / "indeterminate-guard.db"
    )
    claim = await claim_execution(
        service
    )
    execution_id = str(
        claim.execution.id
    )

    frozen = await service.mark_indeterminate(
        execution_id,
        "executor outcome is unknown",
    )

    assert frozen.status == (
        ActionExecutionStatus.INDETERMINATE
    )

    with pytest.raises(
        ActionExecutionConflictError,
        match="indeterminate",
    ):
        await service.succeed(
            execution_id,
            {
                "success": True,
            },
        )

    with pytest.raises(
        ActionExecutionConflictError,
        match="indeterminate",
    ):
        await service.fail(
            execution_id,
            {
                "success": False,
            },
        )

    persisted = await service.get(
        execution_id
    )

    assert persisted == frozen


@pytest.mark.asyncio
async def test_terminal_replay_with_different_result_conflicts(
    tmp_path: Path,
):
    service = build_service(
        tmp_path / "terminal-replay-conflict.db"
    )
    claim = await claim_execution(
        service
    )
    execution_id = str(
        claim.execution.id
    )

    await service.succeed(
        execution_id,
        {
            "success": True,
            "revision": "v2",
        },
    )

    with pytest.raises(
        ActionExecutionConflictError,
        match="different result",
    ):
        await service.succeed(
            execution_id,
            {
                "success": True,
                "revision": "v3",
            },
        )


@pytest.mark.asyncio
async def test_missing_execution_fails_without_creating_record(
    tmp_path: Path,
):
    service = build_service(
        tmp_path / "missing-execution.db"
    )

    with pytest.raises(
        ValueError,
        match="not found",
    ):
        await service.succeed(
            "missing-execution",
            {
                "success": True,
            },
        )

    assert await service.list_all() == []
