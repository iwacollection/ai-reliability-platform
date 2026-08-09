from pathlib import Path
from uuid import UUID

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module

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
from services.agent_runtime.app.incident.store import (
    IncidentStore,
)
from services.agent_runtime.app.verification.store import (
    VerificationStore,
)


def patch_runtime_databases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Path]:
    paths = {
        "approval": tmp_path / "approvals.db",
        "incident": tmp_path / "incidents.db",
        "verification": tmp_path / "verifications.db",
        "action_execution": (
            tmp_path / "action-executions.db"
        ),
    }

    def create_approval_service() -> ApprovalService:
        return ApprovalService(
            manager=ApprovalManager(
                store=ApprovalStore(
                    db_path=paths["approval"]
                )
            )
        )

    def create_incident_store() -> IncidentStore:
        return IncidentStore(
            db_path=paths["incident"]
        )

    def create_verification_store() -> VerificationStore:
        return VerificationStore(
            db_path=paths["verification"]
        )

    def create_action_execution_store(
    ) -> ActionExecutionStore:
        return ActionExecutionStore(
            db_path=paths["action_execution"]
        )

    monkeypatch.setattr(
        runtime_module,
        "ApprovalService",
        create_approval_service,
    )
    monkeypatch.setattr(
        runtime_module,
        "IncidentStore",
        create_incident_store,
    )
    monkeypatch.setattr(
        runtime_module,
        "VerificationStore",
        create_verification_store,
    )
    monkeypatch.setattr(
        runtime_module,
        "ActionExecutionStore",
        create_action_execution_store,
    )

    return paths


def test_agent_runtime_owns_shared_action_execution_components(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    paths = patch_runtime_databases(
        monkeypatch,
        tmp_path,
    )

    runtime = runtime_module.AgentRuntime()

    assert isinstance(
        runtime.action_execution_store,
        ActionExecutionStore,
    )
    assert isinstance(
        runtime.action_execution_service,
        ActionExecutionService,
    )
    assert (
        runtime.action_execution_service.store
        is runtime.action_execution_store
    )

    assert (
        runtime.action_runtime.approval
        is runtime.approval
    )
    assert (
        runtime.action_runtime.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.pipeline.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.verification_runtime.incident_store
        is runtime.incident_store
    )
    assert (
        runtime.verification_runtime.verification_service
        is runtime.verification
    )
    assert (
        runtime.verification.store
        is runtime.verification_store
    )
    assert (
        runtime.verification_coordinator.verification_runtime
        is runtime.verification_runtime
    )
    assert (
        runtime.verification_coordinator.collector
        is runtime.verification_collector
    )
    assert (
        runtime.verification_coordinator.profile_factory
        is runtime.verification_profile_factory
    )

    approval_store = runtime.approval.manager.store

    assert approval_store.db_path == paths["approval"]
    assert runtime.incident_store.db_path == paths["incident"]
    assert (
        runtime.verification_store.db_path
        == paths["verification"]
    )
    assert (
        runtime.action_execution_store.db_path
        == paths["action_execution"]
    )

    for database_path in paths.values():
        assert database_path.exists()
        assert database_path.parent == tmp_path


@pytest.mark.asyncio
async def test_action_execution_service_survives_runtime_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    paths = patch_runtime_databases(
        monkeypatch,
        tmp_path,
    )

    first_runtime = runtime_module.AgentRuntime()

    action = ActionPlan(
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
    incident_id = UUID(
        "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    )

    first_claim = (
        await first_runtime.action_execution_service.claim(
            approval_id="approval-1",
            incident_id=incident_id,
            operator_id="sre-1",
            idempotency_key="execution-1",
            action=action,
            metadata={
                "request_id": "request-1",
            },
        )
    )

    assert first_claim.created is True

    restarted_runtime = runtime_module.AgentRuntime()

    assert (
        restarted_runtime.action_execution_store
        is not first_runtime.action_execution_store
    )
    assert (
        restarted_runtime.action_execution_service.store
        is restarted_runtime.action_execution_store
    )
    assert (
        restarted_runtime.action_execution_store.db_path
        == paths["action_execution"]
    )

    restored = (
        await restarted_runtime.action_execution_service.get(
            str(first_claim.execution.id)
        )
    )

    assert restored == first_claim.execution
    assert restored is not None
    assert restored.incident_id == incident_id
    assert restored.action.namespace == "payment"
    assert restored.action.cluster == "production-tw"
    assert restored.automatic_replay_allowed is False

    replay = (
        await restarted_runtime.action_execution_service.claim(
            approval_id="approval-1",
            incident_id=incident_id,
            operator_id="sre-1",
            idempotency_key="execution-1",
            action=action,
            metadata={
                "request_id": "request-1",
            },
        )
    )

    assert replay.created is False
    assert replay.execution == first_claim.execution

