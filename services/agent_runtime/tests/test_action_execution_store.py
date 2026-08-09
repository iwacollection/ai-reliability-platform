import asyncio

from pathlib import Path
from uuid import UUID

import pytest

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionRecord,
    ActionExecutionStatus,
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
    "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
)


def build_store(
    db_path: Path,
) -> ActionExecutionStore:
    return ActionExecutionStore(
        db_path=db_path
    )


def build_execution(
    *,
    approval_id: str = "approval-1",
    idempotency_key: str = "execution-1",
    operator_id: str = "sre-1",
    target: str = "payment-api",
    approved: bool = True,
    metadata: dict | None = None,
) -> ActionExecutionRecord:
    return ActionExecutionRecord(
        approval_id=approval_id,
        incident_id=INCIDENT_ID,
        operator_id=operator_id,
        idempotency_key=idempotency_key,
        action=ActionPlan(
            type=(
                ActionType.INCREASE_MEMORY_LIMIT
            ),
            target=target,
            namespace="payment",
            cluster="production-tw",
            risk=ActionRisk.MEDIUM,
            approved=approved,
            metadata={
                "memory_limit": "1Gi",
            },
        ),
        metadata=dict(metadata or {}),
    )


@pytest.mark.asyncio
async def test_claim_persists_scope_and_exact_replay(
    tmp_path: Path,
):
    db_path = tmp_path / "action-executions.db"
    first_store = build_store(db_path)
    restarted_store = build_store(db_path)

    first_request = build_execution()
    first_claim = await first_store.claim(
        first_request
    )

    assert first_claim.created is True
    assert first_claim.is_replay is False

    replay_request = build_execution()
    assert replay_request.id != first_request.id

    replay = await restarted_store.claim(
        replay_request
    )

    assert replay.created is False
    assert replay.is_replay is True
    assert replay.execution.id == first_claim.execution.id
    assert replay.execution.status == (
        ActionExecutionStatus.RUNNING
    )
    assert replay.execution.action.target == (
        "payment-api"
    )
    assert replay.execution.action.namespace == (
        "payment"
    )
    assert replay.execution.action.cluster == (
        "production-tw"
    )
    assert replay.execution.automatic_replay_allowed is False

    by_id = await restarted_store.get(
        str(first_claim.execution.id)
    )
    by_approval = (
        await restarted_store.get_by_approval(
            "approval-1"
        )
    )
    by_key = (
        await restarted_store.get_by_idempotency_key(
            "execution-1"
        )
    )

    assert by_id == first_claim.execution
    assert by_approval == first_claim.execution
    assert by_key == first_claim.execution


@pytest.mark.asyncio
async def test_cross_instance_concurrent_claim_has_one_owner(
    tmp_path: Path,
):
    db_path = tmp_path / "concurrent-claim.db"
    first_store = build_store(db_path)
    second_store = build_store(db_path)

    first, second = await asyncio.gather(
        first_store.claim(
            build_execution()
        ),
        second_store.claim(
            build_execution()
        ),
    )

    assert sorted(
        [
            first.created,
            second.created,
        ]
    ) == [
        False,
        True,
    ]
    assert first.execution.id == second.execution.id

    rows = await first_store.list_all()

    assert len(rows) == 1
    assert rows[0].id == first.execution.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "approval_id",
        "idempotency_key",
    ),
    [
        (
            "approval-1",
            "different-execution-key",
        ),
        (
            "different-approval",
            "execution-1",
        ),
    ],
)
async def test_claim_rejects_partial_identity_collision(
    tmp_path: Path,
    approval_id: str,
    idempotency_key: str,
):
    store = build_store(
        tmp_path / "identity-conflict.db"
    )

    await store.claim(
        build_execution()
    )

    with pytest.raises(
        ActionExecutionConflictError,
    ):
        await store.claim(
            build_execution(
                approval_id=approval_id,
                idempotency_key=idempotency_key,
            )
        )

    assert len(await store.list_all()) == 1


@pytest.mark.asyncio
async def test_exact_keys_with_changed_payload_fail_closed(
    tmp_path: Path,
):
    store = build_store(
        tmp_path / "payload-conflict.db"
    )

    original = await store.claim(
        build_execution()
    )

    with pytest.raises(
        ActionExecutionConflictError,
    ):
        await store.claim(
            build_execution(
                target="different-service",
            )
        )

    persisted = await store.get_by_approval(
        "approval-1"
    )

    assert persisted == original.execution


@pytest.mark.asyncio
async def test_claim_requires_approved_action_plan(
    tmp_path: Path,
):
    store = build_store(
        tmp_path / "unapproved.db"
    )

    with pytest.raises(
        ValueError,
        match="approved ActionPlan",
    ):
        await store.claim(
            build_execution(
                approved=False,
            )
        )

    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_success_transition_is_cas_and_retry_idempotent(
    tmp_path: Path,
):
    db_path = tmp_path / "successful-execution.db"
    writer = build_store(db_path)
    restarted_reader = build_store(db_path)

    claim = await writer.claim(
        build_execution()
    )

    completed = claim.execution
    completed.succeed(
        {
            "success": True,
            "action": "increase_memory_limit",
            "target": "payment-api",
        }
    )

    persisted = await writer.update(
        completed,
        expected_status=(
            ActionExecutionStatus.RUNNING
        ),
    )

    assert persisted.status == (
        ActionExecutionStatus.SUCCEEDED
    )
    assert persisted.is_terminal is True

    retried_update = await restarted_reader.update(
        completed,
        expected_status=(
            ActionExecutionStatus.RUNNING
        ),
    )

    assert retried_update == persisted

    replay = await restarted_reader.claim(
        build_execution()
    )

    assert replay.created is False
    assert replay.execution == persisted
    assert replay.execution.automatic_replay_allowed is False


@pytest.mark.asyncio
async def test_concurrent_terminal_updates_have_one_winner(
    tmp_path: Path,
):
    db_path = tmp_path / "terminal-race.db"
    first_store = build_store(db_path)
    second_store = build_store(db_path)

    claim = await first_store.claim(
        build_execution()
    )

    successful = claim.execution.model_copy(
        deep=True
    )
    successful.succeed(
        {
            "success": True,
        }
    )

    failed = claim.execution.model_copy(
        deep=True
    )
    failed.fail(
        {
            "success": False,
        },
        error_type="ExecutorError",
        error_message="executor failed",
    )

    outcomes = await asyncio.gather(
        first_store.update(
            successful,
            expected_status=(
                ActionExecutionStatus.RUNNING
            ),
        ),
        second_store.update(
            failed,
            expected_status=(
                ActionExecutionStatus.RUNNING
            ),
        ),
        return_exceptions=True,
    )

    successful_updates = [
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

    assert len(successful_updates) == 1
    assert len(conflicts) == 1

    persisted = await first_store.get(
        str(claim.execution.id)
    )

    assert persisted == successful_updates[0]
    assert persisted is not None
    assert persisted.status in {
        ActionExecutionStatus.SUCCEEDED,
        ActionExecutionStatus.FAILED,
    }


@pytest.mark.asyncio
async def test_running_execution_can_be_frozen_concurrently(
    tmp_path: Path,
):
    db_path = tmp_path / "indeterminate.db"
    first_store = build_store(db_path)
    second_store = build_store(db_path)

    claim = await first_store.claim(
        build_execution()
    )

    first, second = await asyncio.gather(
        first_store.mark_indeterminate(
            str(claim.execution.id),
            "runtime stopped during executor call",
        ),
        second_store.mark_indeterminate(
            str(claim.execution.id),
            "executor outcome requires reconciliation",
        ),
    )

    assert first.id == second.id
    assert first.status == (
        ActionExecutionStatus.INDETERMINATE
    )
    assert second.status == (
        ActionExecutionStatus.INDETERMINATE
    )
    assert first.requires_reconciliation is True
    assert first.automatic_replay_allowed is False

    replay = await second_store.claim(
        build_execution()
    )

    assert replay.created is False
    assert replay.execution.status == (
        ActionExecutionStatus.INDETERMINATE
    )

    frozen_rows = await first_store.list_by_status(
        ActionExecutionStatus.INDETERMINATE
    )

    assert len(frozen_rows) == 1
    assert frozen_rows[0].id == claim.execution.id


@pytest.mark.asyncio
async def test_update_rejects_immutable_identity_change(
    tmp_path: Path,
):
    store = build_store(
        tmp_path / "immutable-fields.db"
    )

    claim = await store.claim(
        build_execution()
    )

    changed = claim.execution.model_copy(
        deep=True
    )
    changed.operator_id = "different-operator"
    changed.fail(
        {
            "success": False,
        }
    )

    with pytest.raises(
        ActionExecutionConflictError,
        match="Immutable",
    ):
        await store.update(
            changed,
            expected_status=(
                ActionExecutionStatus.RUNNING
            ),
        )

    persisted = await store.get(
        str(claim.execution.id)
    )

    assert persisted == claim.execution
    assert persisted is not None
    assert persisted.status == (
        ActionExecutionStatus.RUNNING
    )
