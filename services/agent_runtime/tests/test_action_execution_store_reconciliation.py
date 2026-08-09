import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionReconciliationDecision,
    ActionExecutionReconciliationOutcome,
    ActionExecutionRecord,
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.execution_store import (
    ActionExecutionConflictError,
    ActionExecutionReconciliationResult,
    ActionExecutionStore,
)
from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)


INCIDENT_ID = UUID(
    "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
)


def build_store(
    db_path: Path,
) -> ActionExecutionStore:
    return ActionExecutionStore(
        db_path=db_path
    )


def build_execution(
    *,
    approval_id: str | None = None,
    idempotency_key: str | None = None,
) -> ActionExecutionRecord:
    unique_id = str(
        uuid4()
    )

    return ActionExecutionRecord(
        approval_id=(
            approval_id
            or f"approval-{unique_id}"
        ),
        incident_id=INCIDENT_ID,
        operator_id="executor-operator",
        idempotency_key=(
            idempotency_key
            or f"execution-{unique_id}"
        ),
        action=ActionPlan(
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
        ),
    )


def build_decision(
    *,
    outcome=(
        ActionExecutionReconciliationOutcome.SUCCEEDED
    ),
    idempotency_key: str = "reconcile-1",
    reason: str = "External outcome was confirmed",
    result=None,
    **overrides,
) -> ActionExecutionReconciliationDecision:
    values = {
        "outcome": outcome,
        "operator_id": "reconciliation-operator",
        "idempotency_key": idempotency_key,
        "reason": reason,
        "result": (
            {
                "success": True,
                "evidence": "kubernetes_audit",
            }
            if result is None
            else result
        ),
        "metadata": {
            "ticket": "INC-2026-0001",
        },
    }
    values.update(
        overrides
    )

    return ActionExecutionReconciliationDecision(
        **values
    )


async def create_indeterminate(
    store: ActionExecutionStore,
    *,
    approval_id: str | None = None,
    idempotency_key: str | None = None,
) -> ActionExecutionRecord:
    claim = await store.claim(
        build_execution(
            approval_id=approval_id,
            idempotency_key=idempotency_key,
        )
    )

    assert claim.created is True

    return await store.mark_indeterminate(
        str(
            claim.execution.id
        ),
        "Runtime lost the executor response",
    )


@pytest.mark.asyncio
async def test_success_reconciliation_persists_across_restart(
    tmp_path: Path,
):
    db_path = tmp_path / "reconcile-success.db"
    first_store = build_store(
        db_path
    )
    restarted_store = build_store(
        db_path
    )
    indeterminate = await create_indeterminate(
        first_store
    )
    decision = build_decision()

    result = await restarted_store.reconcile(
        indeterminate.id,
        decision,
    )

    assert result.applied is True
    assert result.is_replay is False
    assert result.execution.status == (
        ActionExecutionStatus.SUCCEEDED
    )
    assert result.execution.reconciliation == (
        decision
    )
    assert result.execution.was_reconciled is True
    assert result.execution.requires_reconciliation is False
    assert result.execution.automatic_replay_allowed is False

    persisted = await first_store.get(
        str(
            indeterminate.id
        )
    )

    assert persisted == result.execution
    assert persisted is not None
    assert persisted.reconciliation == decision


@pytest.mark.asyncio
async def test_exact_replay_ignores_new_decision_timestamp(
    tmp_path: Path,
):
    db_path = tmp_path / "reconcile-replay.db"
    first_store = build_store(
        db_path
    )
    restarted_store = build_store(
        db_path
    )
    indeterminate = await create_indeterminate(
        first_store
    )
    first_decision = build_decision()
    replay_decision = build_decision(
        reconciled_at=(
            first_decision.reconciled_at
            + timedelta(
                minutes=5
            )
        )
    )

    first = await first_store.reconcile(
        indeterminate.id,
        first_decision,
    )
    replay = await restarted_store.reconcile(
        indeterminate.id,
        replay_decision,
    )

    assert first.applied is True
    assert first.is_replay is False
    assert replay.applied is False
    assert replay.is_replay is True
    assert replay.execution == first.execution
    assert replay.execution.reconciliation is not None
    assert (
        replay.execution.reconciliation.reconciled_at
        == first_decision.reconciled_at
    )
    assert (
        replay.execution.reconciliation.reconciled_at
        != replay_decision.reconciled_at
    )
    assert len(
        await restarted_store.list_all()
    ) == 1


@pytest.mark.asyncio
async def test_same_key_with_changed_decision_fails_closed(
    tmp_path: Path,
):
    store = build_store(
        tmp_path
        / "reconcile-key-conflict.db"
    )
    indeterminate = await create_indeterminate(
        store
    )
    first = await store.reconcile(
        indeterminate.id,
        build_decision(),
    )

    with pytest.raises(
        ActionExecutionConflictError,
        match="key was reused",
    ):
        await store.reconcile(
            indeterminate.id,
            build_decision(
                reason=(
                    "The same key now carries different evidence"
                )
            ),
        )

    persisted = await store.get(
        str(
            indeterminate.id
        )
    )

    assert persisted == first.execution


@pytest.mark.asyncio
async def test_second_reconciliation_key_fails_closed(
    tmp_path: Path,
):
    store = build_store(
        tmp_path
        / "reconcile-second-key.db"
    )
    indeterminate = await create_indeterminate(
        store
    )
    first = await store.reconcile(
        indeterminate.id,
        build_decision(),
    )

    with pytest.raises(
        ActionExecutionConflictError,
        match="already reconciled",
    ):
        await store.reconcile(
            indeterminate.id,
            build_decision(
                idempotency_key=(
                    "another-reconciliation-key"
                )
            ),
        )

    assert await store.get(
        str(
            indeterminate.id
        )
    ) == first.execution


@pytest.mark.asyncio
async def test_failure_reconciliation_persists_error_audit(
    tmp_path: Path,
):
    db_path = tmp_path / "reconcile-failure.db"
    first_store = build_store(
        db_path
    )
    restarted_store = build_store(
        db_path
    )
    indeterminate = await create_indeterminate(
        first_store
    )
    decision = build_decision(
        outcome=(
            ActionExecutionReconciliationOutcome.FAILED
        ),
        reason=(
            "Audit evidence confirms the mutation did not occur"
        ),
        result={
            "success": False,
            "evidence": "kubernetes_audit",
        },
        error_type="MutationNotObserved",
        error_message=(
            "Deployment generation and Pod UID were unchanged"
        ),
    )

    result = await first_store.reconcile(
        indeterminate.id,
        decision,
    )
    persisted = await restarted_store.get(
        str(
            indeterminate.id
        )
    )

    assert result.applied is True
    assert result.execution.status == (
        ActionExecutionStatus.FAILED
    )
    assert result.execution.error_type == (
        "MutationNotObserved"
    )
    assert result.execution.error_message == (
        "Deployment generation and Pod UID were unchanged"
    )
    assert persisted == result.execution
    assert persisted is not None
    assert persisted.reconciliation == decision
    assert len(
        await restarted_store.list_all()
    ) == 1


@pytest.mark.asyncio
async def test_non_indeterminate_and_missing_execution_fail_closed(
    tmp_path: Path,
):
    store = build_store(
        tmp_path
        / "reconcile-invalid-status.db"
    )
    running_claim = await store.claim(
        build_execution()
    )

    with pytest.raises(
        ActionExecutionConflictError,
        match="Only an indeterminate",
    ):
        await store.reconcile(
            running_claim.execution.id,
            build_decision(),
        )

    completed = running_claim.execution.model_copy(
        deep=True
    )
    completed.succeed(
        {
            "success": True,
        }
    )
    await store.update(
        completed,
        expected_status=(
            ActionExecutionStatus.RUNNING
        ),
    )

    with pytest.raises(
        ActionExecutionConflictError,
        match="Only an indeterminate",
    ):
        await store.reconcile(
            completed.id,
            build_decision(),
        )

    with pytest.raises(
        ValueError,
        match="not found",
    ):
        await store.reconcile(
            uuid4(),
            build_decision(),
        )


@pytest.mark.asyncio
async def test_cross_instance_same_request_has_one_applier(
    tmp_path: Path,
):
    db_path = tmp_path / "reconcile-concurrent-replay.db"
    owner = build_store(
        db_path
    )
    indeterminate = await create_indeterminate(
        owner
    )
    stores = [
        build_store(
            db_path
        )
        for _ in range(6)
    ]
    decisions = [
        build_decision()
        for _ in stores
    ]

    results = await asyncio.gather(
        *[
            store.reconcile(
                indeterminate.id,
                decision,
            )
            for store, decision in zip(
                stores,
                decisions,
                strict=True,
            )
        ]
    )

    assert all(
        isinstance(
            result,
            ActionExecutionReconciliationResult,
        )
        for result in results
    )
    assert sum(
        result.applied
        for result in results
    ) == 1
    assert sum(
        result.is_replay
        for result in results
    ) == 5
    assert len(
        {
            result.execution.id
            for result in results
        }
    ) == 1
    assert len(
        await owner.list_all()
    ) == 1

    persisted = await owner.get(
        str(
            indeterminate.id
        )
    )

    assert persisted is not None
    assert persisted.status == (
        ActionExecutionStatus.SUCCEEDED
    )
    assert persisted.automatic_replay_allowed is False


@pytest.mark.asyncio
async def test_cross_instance_conflicting_decisions_have_one_winner(
    tmp_path: Path,
):
    db_path = tmp_path / "reconcile-concurrent-conflict.db"
    first_store = build_store(
        db_path
    )
    second_store = build_store(
        db_path
    )
    indeterminate = await create_indeterminate(
        first_store
    )
    success_decision = build_decision(
        idempotency_key="reconcile-success",
    )
    failure_decision = build_decision(
        outcome=(
            ActionExecutionReconciliationOutcome.FAILED
        ),
        idempotency_key="reconcile-failure",
        reason="External failure was confirmed",
        result={
            "success": False,
            "evidence": "kubernetes_audit",
        },
    )

    outcomes = await asyncio.gather(
        first_store.reconcile(
            indeterminate.id,
            success_decision,
        ),
        second_store.reconcile(
            indeterminate.id,
            failure_decision,
        ),
        return_exceptions=True,
    )

    applied = [
        outcome
        for outcome in outcomes
        if isinstance(
            outcome,
            ActionExecutionReconciliationResult,
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

    assert len(applied) == 1
    assert len(conflicts) == 1
    assert applied[0].applied is True

    persisted = await first_store.get(
        str(
            indeterminate.id
        )
    )

    assert persisted == applied[0].execution
    assert persisted is not None
    assert persisted.reconciliation is not None
    assert persisted.status in {
        ActionExecutionStatus.SUCCEEDED,
        ActionExecutionStatus.FAILED,
    }
    assert len(
        await first_store.list_all()
    ) == 1
