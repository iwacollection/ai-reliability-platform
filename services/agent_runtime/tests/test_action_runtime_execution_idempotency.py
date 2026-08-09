import asyncio

from dataclasses import dataclass
from pathlib import Path

import pytest

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.execution_service import (
    ActionExecutionService,
)
from services.agent_runtime.app.action.execution_store import (
    ActionExecutionStore,
)
from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
)
from services.agent_runtime.app.approval.manager import (
    ApprovalManager,
)
from services.agent_runtime.app.approval.service import (
    ApprovalService,
)
from services.agent_runtime.app.approval.store import (
    ApprovalStore,
)
from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.incident.store import (
    IncidentStore,
)
from services.agent_runtime.app.runtime.action_runtime import (
    ActionRuntime,
)


@dataclass(frozen=True)
class RuntimeDatabases:
    approvals: Path
    incidents: Path
    executions: Path


class CountingExecutor:
    def __init__(
        self,
        *,
        result: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = dict(
            result
            or {
                "success": True,
                "message": "action executed",
            }
        )
        self.error = error
        self.calls = 0

    async def execute(
        self,
        action: ActionPlan,
    ) -> dict:
        self.calls += 1

        if self.error is not None:
            raise self.error

        return dict(
            self.result
        )


def build_databases(
    tmp_path: Path,
) -> RuntimeDatabases:
    return RuntimeDatabases(
        approvals=tmp_path / "approvals.db",
        incidents=tmp_path / "incidents.db",
        executions=tmp_path / "action-executions.db",
    )


def build_approval_service(
    databases: RuntimeDatabases,
) -> ApprovalService:
    return ApprovalService(
        manager=ApprovalManager(
            store=ApprovalStore(
                db_path=databases.approvals
            )
        )
    )


def build_incident_store(
    databases: RuntimeDatabases,
) -> IncidentStore:
    return IncidentStore(
        db_path=databases.incidents
    )


def build_execution_service(
    databases: RuntimeDatabases,
) -> ActionExecutionService:
    return ActionExecutionService(
        store=ActionExecutionStore(
            db_path=databases.executions
        )
    )


def build_runtime(
    databases: RuntimeDatabases,
    executor: CountingExecutor,
    *,
    with_execution_service: bool = True,
) -> ActionRuntime:
    runtime = ActionRuntime(
        approval_service=(
            build_approval_service(
                databases
            )
        ),
        incident_store=(
            build_incident_store(
                databases
            )
        ),
        action_execution_service=(
            build_execution_service(
                databases
            )
            if with_execution_service
            else None
        ),
    )
    runtime.executor = executor

    return runtime


async def create_approved_action(
    databases: RuntimeDatabases,
) -> tuple[str, IncidentState]:
    incident_store = build_incident_store(
        databases
    )
    incident = IncidentState()
    incident.update(
        status=IncidentStatus.CONFIRMED,
        reason="Healing action requires approval",
    )
    incident = await incident_store.save(
        incident
    )

    plan = ActionPlan(
        type=(
            ActionType.INCREASE_MEMORY_LIMIT
        ),
        target="payment-api",
        namespace="payment",
        cluster="production-tw",
        risk=ActionRisk.MEDIUM,
        approved=False,
        metadata={
            "memory_limit": "1Gi",
        },
    )
    approval_service = build_approval_service(
        databases
    )
    request = await approval_service.create_approval(
        action=plan,
        reason="Medium risk action requires approval",
        incident_id=incident.id,
    )
    approved = await approval_service.approve(
        request.id
    )

    assert approved.action.approved is True

    return request.id, incident


@pytest.mark.asyncio
async def test_configured_resume_requires_execution_identity(
    tmp_path: Path,
):
    databases = build_databases(
        tmp_path
    )
    approval_id, _ = await create_approved_action(
        databases
    )
    executor = CountingExecutor()
    runtime = build_runtime(
        databases,
        executor,
    )

    result = await runtime.resume(
        approval_id
    )

    assert result["success"] is False
    assert result["status"] == (
        "execution_identity_required"
    )
    assert result["required_fields"] == [
        "operator_id",
        "idempotency_key",
    ]
    assert executor.calls == 0
    assert (
        await runtime.action_execution_service.list_all()
        == []
    )


@pytest.mark.asyncio
async def test_resume_executes_once_and_replays_after_restart(
    tmp_path: Path,
):
    databases = build_databases(
        tmp_path
    )
    approval_id, incident = (
        await create_approved_action(
            databases
        )
    )
    first_executor = CountingExecutor()
    first_runtime = build_runtime(
        databases,
        first_executor,
    )

    first = await first_runtime.resume(
        approval_id,
        operator_id="sre-1",
        idempotency_key="execution-1",
    )

    assert first["success"] is True
    assert first["execution_status"] == (
        ActionExecutionStatus.SUCCEEDED.value
    )
    assert first["idempotent_replay"] is False
    assert first["automatic_replay_allowed"] is False
    assert first["incident_id"] == str(incident.id)
    assert first_executor.calls == 1

    restarted_executor = CountingExecutor()
    restarted_runtime = build_runtime(
        databases,
        restarted_executor,
    )

    replay = await restarted_runtime.resume(
        approval_id,
        operator_id="sre-1",
        idempotency_key="execution-1",
    )

    assert replay["success"] is True
    assert replay["execution_id"] == first["execution_id"]
    assert replay["execution_status"] == (
        ActionExecutionStatus.SUCCEEDED.value
    )
    assert replay["idempotent_replay"] is True
    assert replay["automatic_replay_allowed"] is False
    assert restarted_executor.calls == 0

    rows = (
        await restarted_runtime.action_execution_service.list_all()
    )

    assert len(rows) == 1

    stored_incident = (
        await restarted_runtime.incident_store.get(
            str(incident.id)
        )
    )

    assert stored_incident is not None
    assert stored_incident.status == (
        IncidentStatus.HEALING
    )


@pytest.mark.asyncio
async def test_concurrent_resume_has_one_executor_owner(
    tmp_path: Path,
):
    databases = build_databases(
        tmp_path
    )
    approval_id, _ = await create_approved_action(
        databases
    )
    first_executor = CountingExecutor()
    second_executor = CountingExecutor()
    first_runtime = build_runtime(
        databases,
        first_executor,
    )
    second_runtime = build_runtime(
        databases,
        second_executor,
    )

    first, second = await asyncio.gather(
        first_runtime.resume(
            approval_id,
            operator_id="sre-1",
            idempotency_key="execution-1",
        ),
        second_runtime.resume(
            approval_id,
            operator_id="sre-1",
            idempotency_key="execution-1",
        ),
    )

    assert (
        first_executor.calls
        + second_executor.calls
    ) == 1
    assert first["execution_id"] == second["execution_id"]
    assert sorted(
        [
            first["idempotent_replay"],
            second["idempotent_replay"],
        ]
    ) == [
        False,
        True,
    ]

    persisted = (
        await first_runtime.action_execution_service.get(
            first["execution_id"]
        )
    )

    assert persisted is not None
    assert persisted.status == (
        ActionExecutionStatus.SUCCEEDED
    )


@pytest.mark.asyncio
async def test_same_approval_with_different_key_conflicts(
    tmp_path: Path,
):
    databases = build_databases(
        tmp_path
    )
    approval_id, _ = await create_approved_action(
        databases
    )
    executor = CountingExecutor()
    runtime = build_runtime(
        databases,
        executor,
    )

    await runtime.resume(
        approval_id,
        operator_id="sre-1",
        idempotency_key="execution-1",
    )
    conflict = await runtime.resume(
        approval_id,
        operator_id="sre-1",
        idempotency_key="different-key",
    )

    assert conflict["success"] is False
    assert conflict["status"] == "execution_conflict"
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_executor_exception_freezes_execution(
    tmp_path: Path,
):
    databases = build_databases(
        tmp_path
    )
    approval_id, incident = (
        await create_approved_action(
            databases
        )
    )
    failing_executor = CountingExecutor(
        error=TimeoutError(
            "Kubernetes API outcome is unknown"
        )
    )
    runtime = build_runtime(
        databases,
        failing_executor,
    )

    result = await runtime.resume(
        approval_id,
        operator_id="sre-1",
        idempotency_key="execution-timeout",
    )

    assert result["success"] is False
    assert result["status"] == (
        "execution_indeterminate"
    )
    assert result["execution_status"] == (
        ActionExecutionStatus.INDETERMINATE.value
    )
    assert result["requires_reconciliation"] is True
    assert failing_executor.calls == 1

    replay_executor = CountingExecutor()
    restarted_runtime = build_runtime(
        databases,
        replay_executor,
    )
    replay = await restarted_runtime.resume(
        approval_id,
        operator_id="sre-1",
        idempotency_key="execution-timeout",
    )

    assert replay["status"] == (
        "execution_indeterminate"
    )
    assert replay["idempotent_replay"] is True
    assert replay_executor.calls == 0

    stored_incident = (
        await restarted_runtime.incident_store.get(
            str(incident.id)
        )
    )

    assert stored_incident is not None
    assert stored_incident.status == (
        IncidentStatus.HEALING
    )


@pytest.mark.asyncio
async def test_explicit_executor_failure_is_terminal(
    tmp_path: Path,
):
    databases = build_databases(
        tmp_path
    )
    approval_id, incident = (
        await create_approved_action(
            databases
        )
    )
    executor = CountingExecutor(
        result={
            "success": False,
            "message": "Kubernetes API denied the action",
        }
    )
    runtime = build_runtime(
        databases,
        executor,
    )

    result = await runtime.resume(
        approval_id,
        operator_id="sre-1",
        idempotency_key="execution-failed",
    )

    assert result["success"] is False
    assert result["execution_status"] == (
        ActionExecutionStatus.FAILED.value
    )
    assert executor.calls == 1

    stored_incident = await runtime.incident_store.get(
        str(incident.id)
    )

    assert stored_incident is not None
    assert stored_incident.status == (
        IncidentStatus.FAILED
    )


@pytest.mark.asyncio
async def test_persisted_running_claim_is_never_relaunched(
    tmp_path: Path,
):
    databases = build_databases(
        tmp_path
    )
    approval_id, incident = (
        await create_approved_action(
            databases
        )
    )
    approval_service = build_approval_service(
        databases
    )
    approval = await approval_service.get(
        approval_id
    )

    assert approval is not None

    execution_service = build_execution_service(
        databases
    )
    claim = await execution_service.claim(
        approval_id=approval.id,
        incident_id=approval.incident_id,
        operator_id="sre-1",
        idempotency_key="execution-before-crash",
        action=approval.action,
        metadata={
            "source": "action_runtime.resume",
        },
    )

    assert claim.created is True

    executor = CountingExecutor()
    restarted_runtime = build_runtime(
        databases,
        executor,
    )
    result = await restarted_runtime.resume(
        approval_id,
        operator_id="sre-1",
        idempotency_key="execution-before-crash",
    )

    assert result["success"] is False
    assert result["status"] == "execution_in_progress"
    assert result["execution_status"] == (
        ActionExecutionStatus.RUNNING.value
    )
    assert result["requires_reconciliation"] is True
    assert result["automatic_replay_allowed"] is False
    assert executor.calls == 0

    stored_incident = (
        await restarted_runtime.incident_store.get(
            str(incident.id)
        )
    )

    assert stored_incident is not None
    assert stored_incident.status == (
        IncidentStatus.CONFIRMED
    )


@pytest.mark.asyncio
async def test_execution_identity_without_service_fails_closed(
    tmp_path: Path,
):
    databases = build_databases(
        tmp_path
    )
    approval_id, _ = await create_approved_action(
        databases
    )
    executor = CountingExecutor()
    runtime = build_runtime(
        databases,
        executor,
        with_execution_service=False,
    )

    result = await runtime.resume(
        approval_id,
        operator_id="sre-1",
        idempotency_key="execution-1",
    )

    assert result["success"] is False
    assert result["status"] == (
        "action_execution_not_configured"
    )
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_legacy_resume_without_service_remains_compatible(
    tmp_path: Path,
):
    databases = build_databases(
        tmp_path
    )
    approval_id, _ = await create_approved_action(
        databases
    )
    executor = CountingExecutor()
    runtime = build_runtime(
        databases,
        executor,
        with_execution_service=False,
    )

    result = await runtime.resume(
        approval_id
    )

    assert result["success"] is True
    assert executor.calls == 1
    assert "execution_id" not in result
