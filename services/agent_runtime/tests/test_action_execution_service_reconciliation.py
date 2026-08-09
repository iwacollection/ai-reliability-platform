import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionReconciliationOutcome,
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
    "cccccccc-dddd-4eee-8fff-aaaaaaaaaaaa"
)


def build_service(
    db_path: Path,
) -> ActionExecutionService:
    return ActionExecutionService(
        ActionExecutionStore(
            db_path=db_path
        )
    )


def approved_plan() -> ActionPlan:
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


async def create_indeterminate(
    service: ActionExecutionService,
    *,
    suffix: str | None = None,
) -> ActionExecutionRecord:
    unique_suffix = (
        suffix
        or str(
            uuid4()
        )
    )
    claim = await service.claim(
        approval_id=(
            f"approval-{unique_suffix}"
        ),
        incident_id=INCIDENT_ID,
        operator_id="executor-operator",
        idempotency_key=(
            f"execution-{unique_suffix}"
        ),
        action=approved_plan(),
        metadata={
            "source": "service-test",
        },
    )

    assert claim.created is True

    return await service.mark_indeterminate(
        str(
            claim.execution.id
        ),
        "External executor result was not observed",
    )


def success_request(
    **overrides,
) -> dict:
    values = {
        "outcome": (
            ActionExecutionReconciliationOutcome.SUCCEEDED
        ),
        "operator_id": "reconciliation-operator",
        "idempotency_key": "reconciliation-key-1",
        "reason": "External success was verified",
        "result": {
            "success": True,
            "evidence": "kubernetes_audit",
        },
        "metadata": {
            "ticket": "INC-2026-0002",
        },
    }
    values.update(
        overrides
    )

    return values


@pytest.mark.asyncio
async def test_service_persists_normalized_success_audit(
    tmp_path: Path,
):
    service = build_service(
        tmp_path
        / "service-success.db"
    )
    execution = await create_indeterminate(
        service
    )

    outcome = await service.reconcile(
        execution.id,
        **success_request(
            operator_id="  sre-operator  ",
            idempotency_key="  reconciliation-request-1  ",
            reason="  Kubernetes evidence confirms success  ",
        ),
    )

    assert outcome.applied is True
    assert outcome.is_replay is False
    assert outcome.execution.status == (
        ActionExecutionStatus.SUCCEEDED
    )
    assert outcome.execution.reconciliation is not None
    decision = outcome.execution.reconciliation
    assert decision.operator_id == "sre-operator"
    assert decision.idempotency_key == (
        "reconciliation-request-1"
    )
    assert decision.reason == (
        "Kubernetes evidence confirms success"
    )
    assert decision.metadata == {
        "ticket": "INC-2026-0002",
    }
    assert outcome.execution.error_type is None
    assert outcome.execution.error_message is None
    assert outcome.execution.automatic_replay_allowed is False


@pytest.mark.asyncio
async def test_service_persists_failure_error_audit(
    tmp_path: Path,
):
    db_path = tmp_path / "service-failure.db"
    service = build_service(
        db_path
    )
    restarted_service = build_service(
        db_path
    )
    execution = await create_indeterminate(
        service
    )

    outcome = await service.reconcile(
        execution.id,
        outcome=(
            ActionExecutionReconciliationOutcome.FAILED
        ),
        operator_id="sre-operator",
        idempotency_key="reconciliation-failure-1",
        reason="The requested mutation did not occur",
        result={
            "success": False,
            "evidence": "kubernetes_audit",
        },
        error_type="MutationNotObserved",
        error_message=(
            "Deployment generation and Pod UID were unchanged"
        ),
        metadata={
            "ticket": "INC-2026-0003",
        },
    )
    persisted = await restarted_service.get(
        str(
            execution.id
        )
    )

    assert outcome.applied is True
    assert outcome.execution.status == (
        ActionExecutionStatus.FAILED
    )
    assert outcome.execution.error_type == (
        "MutationNotObserved"
    )
    assert outcome.execution.error_message == (
        "Deployment generation and Pod UID were unchanged"
    )
    assert persisted == outcome.execution
    assert persisted is not None
    assert persisted.reconciliation is not None
    assert persisted.reconciliation.metadata == {
        "ticket": "INC-2026-0003",
    }


@pytest.mark.asyncio
async def test_service_exact_replay_returns_persisted_decision(
    tmp_path: Path,
):
    db_path = tmp_path / "service-replay.db"
    first_service = build_service(
        db_path
    )
    restarted_service = build_service(
        db_path
    )
    execution = await create_indeterminate(
        first_service
    )
    request = success_request()

    first = await first_service.reconcile(
        execution.id,
        **request,
    )
    replay = await restarted_service.reconcile(
        execution.id,
        **request,
    )

    assert first.applied is True
    assert first.is_replay is False
    assert replay.applied is False
    assert replay.is_replay is True
    assert replay.execution == first.execution
    assert replay.execution.reconciliation is not None
    assert first.execution.reconciliation is not None
    assert (
        replay.execution.reconciliation.reconciled_at
        == first.execution.reconciliation.reconciled_at
    )
    assert len(
        await restarted_service.list_all()
    ) == 1


@pytest.mark.asyncio
async def test_service_propagates_reconciliation_conflicts(
    tmp_path: Path,
):
    service = build_service(
        tmp_path
        / "service-conflicts.db"
    )
    execution = await create_indeterminate(
        service
    )
    first = await service.reconcile(
        execution.id,
        **success_request(),
    )

    with pytest.raises(
        ActionExecutionConflictError,
        match="key was reused",
    ):
        await service.reconcile(
            execution.id,
            **success_request(
                reason=(
                    "The same key now carries a different reason"
                )
            ),
        )

    with pytest.raises(
        ActionExecutionConflictError,
        match="already reconciled",
    ):
        await service.reconcile(
            execution.id,
            **success_request(
                idempotency_key=(
                    "second-reconciliation-key"
                )
            ),
        )

    assert await service.get(
        str(
            execution.id
        )
    ) == first.execution


@pytest.mark.asyncio
async def test_service_rejects_non_dictionary_result_before_store(
    tmp_path: Path,
):
    service = build_service(
        tmp_path
        / "service-result-type.db"
    )
    execution = await create_indeterminate(
        service
    )

    with pytest.raises(
        TypeError,
        match="must be a dictionary",
    ):
        await service.reconcile(
            execution.id,
            **success_request(
                result="success"
            ),
        )

    persisted = await service.get(
        str(
            execution.id
        )
    )

    assert persisted is not None
    assert persisted.status == (
        ActionExecutionStatus.INDETERMINATE
    )
    assert persisted.reconciliation is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {
            "operator_id": "   ",
        },
        {
            "idempotency_key": "   ",
        },
        {
            "reason": "   ",
        },
        {
            "result": {
                "success": "true",
            },
        },
        {
            "result": {
                "success": 1,
            },
        },
        {
            "result": {},
        },
    ],
)
async def test_service_rejects_invalid_audit_decision(
    tmp_path: Path,
    override: dict,
):
    service = build_service(
        tmp_path
        / f"invalid-{uuid4()}.db"
    )
    execution = await create_indeterminate(
        service
    )

    with pytest.raises(
        ValidationError
    ):
        await service.reconcile(
            execution.id,
            **success_request(
                **override
            ),
        )

    persisted = await service.get(
        str(
            execution.id
        )
    )

    assert persisted is not None
    assert persisted.status == (
        ActionExecutionStatus.INDETERMINATE
    )
    assert persisted.reconciliation is None


@pytest.mark.asyncio
async def test_service_only_reconciles_indeterminate_execution(
    tmp_path: Path,
):
    service = build_service(
        tmp_path
        / "service-status-boundary.db"
    )
    running = await service.claim(
        approval_id="running-approval",
        incident_id=INCIDENT_ID,
        operator_id="executor-operator",
        idempotency_key="running-execution",
        action=approved_plan(),
    )

    with pytest.raises(
        ActionExecutionConflictError,
        match="Only an indeterminate",
    ):
        await service.reconcile(
            running.execution.id,
            **success_request(),
        )

    with pytest.raises(
        ValueError,
        match="not found",
    ):
        await service.reconcile(
            uuid4(),
            **success_request(),
        )


@pytest.mark.asyncio
async def test_cross_instance_service_replay_has_one_applier(
    tmp_path: Path,
):
    db_path = tmp_path / "service-concurrent.db"
    owner = build_service(
        db_path
    )
    execution = await create_indeterminate(
        owner
    )
    services = [
        build_service(
            db_path
        )
        for _ in range(5)
    ]
    request = success_request(
        idempotency_key=(
            "concurrent-reconciliation-key"
        )
    )

    outcomes = await asyncio.gather(
        *[
            service.reconcile(
                execution.id,
                **request,
            )
            for service in services
        ]
    )

    assert sum(
        outcome.applied
        for outcome in outcomes
    ) == 1
    assert sum(
        outcome.is_replay
        for outcome in outcomes
    ) == 4
    assert len(
        {
            outcome.execution.id
            for outcome in outcomes
        }
    ) == 1

    all_executions = await owner.list_all()
    incident_executions = await (
        owner.list_by_incident(
            INCIDENT_ID
        )
    )

    assert len(all_executions) == 1
    assert len(incident_executions) == 1
    assert all_executions[0] == (
        incident_executions[0]
    )
    assert all_executions[0].status == (
        ActionExecutionStatus.SUCCEEDED
    )
    assert all_executions[0].automatic_replay_allowed is False
