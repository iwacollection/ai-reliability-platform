from pathlib import Path

import pytest

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


def build_approval_service(
    db_path: Path,
) -> ApprovalService:
    return ApprovalService(
        manager=ApprovalManager(
            store=ApprovalStore(
                db_path
            )
        )
    )


def build_healing_result() -> dict:
    return {
        "agent":
        "healing",

        "success":
        True,

        "score":
        1.0,

        "message":
        "increase memory limit",

        "data":
        {
            "action":
            "increase_memory_limit",

            "target":
            "payment-api",

            "risk":
            "medium",

            "reason":
            "Pod memory limit exceeded",
        },
    }


async def create_pending_action(
    approval_db: Path,
    incident_db: Path,
):
    """
    Simulate Pipeline completion followed by ActionRuntime
    creating a pending ApprovalRequest.
    """

    approval_service = (
        build_approval_service(
            approval_db
        )
    )

    incident_store = IncidentStore(
        incident_db
    )

    incident = IncidentState()

    incident.update(
        IncidentStatus.CONFIRMED,
        reason=(
            "Healing plan generated; "
            "approval is required"
        ),
    )

    await incident_store.save(
        incident
    )

    action_runtime = ActionRuntime(
        approval_service=(
            approval_service
        ),
        incident_store=(
            incident_store
        ),
    )

    plan, execution = (
        await action_runtime.execute(
            build_healing_result(),
            incident=incident,
        )
    )

    assert plan.type.value == (
        "increase_memory_limit"
    )

    assert execution["status"] == (
        "pending_approval"
    )

    assert execution["incident_id"] == (
        str(
            incident.id
        )
    )

    stored_incident = (
        await incident_store.get(
            str(
                incident.id
            )
        )
    )

    assert stored_incident is not None

    assert stored_incident.status == (
        IncidentStatus.CONFIRMED
    )

    assert stored_incident.reason == (
        "Remediation action is waiting "
        "for human approval"
    )

    return (
        incident.id,
        execution["approval_id"],
    )


@pytest.mark.asyncio
async def test_resume_after_runtime_restart(
    tmp_path: Path,
):
    """
    Approval and Incident are restored by newly created objects.

    The caller supplies only approval_id and does not retain the
    original in-memory IncidentState.
    """

    approval_db = (
        tmp_path
        / "approvals.db"
    )

    incident_db = (
        tmp_path
        / "incidents.db"
    )

    incident_id, approval_id = (
        await create_pending_action(
            approval_db=approval_db,
            incident_db=incident_db,
        )
    )

    #
    # Simulate a separate human approval process.
    #
    approval_service = (
        build_approval_service(
            approval_db
        )
    )

    approval = (
        await approval_service.approve(
            approval_id
        )
    )

    assert approval.status.value == (
        "approved"
    )

    #
    # Simulate Agent Runtime restart.
    #
    restarted_runtime = ActionRuntime(
        approval_service=(
            build_approval_service(
                approval_db
            )
        ),
        incident_store=(
            IncidentStore(
                incident_db
            )
        ),
    )

    execution = (
        await restarted_runtime.resume(
            approval_id
        )
    )

    assert execution["success"] is True

    assert execution["action"] == (
        "increase_memory_limit"
    )

    assert execution["incident_id"] == (
        str(
            incident_id
        )
    )

    assert execution["incident_status"] == (
        IncidentStatus.HEALING.value
    )

    final_store = IncidentStore(
        incident_db
    )

    stored_incident = (
        await final_store.get(
            str(
                incident_id
            )
        )
    )

    assert stored_incident is not None

    assert stored_incident.status == (
        IncidentStatus.HEALING
    )

    assert stored_incident.reason == (
        "Remediation action executed; "
        "awaiting verification"
    )


class FailingExecutor:
    """
    Executor used to verify persistent failure handling.
    """

    async def execute(
        self,
        plan,
    ):
        raise RuntimeError(
            "simulated executor failure"
        )


@pytest.mark.asyncio
async def test_executor_failure_after_runtime_restart(
    tmp_path: Path,
):
    """
    Executor failure after restart must persist Incident FAILED.
    """

    approval_db = (
        tmp_path
        / "approvals.db"
    )

    incident_db = (
        tmp_path
        / "incidents.db"
    )

    incident_id, approval_id = (
        await create_pending_action(
            approval_db=approval_db,
            incident_db=incident_db,
        )
    )

    approval_service = (
        build_approval_service(
            approval_db
        )
    )

    await approval_service.approve(
        approval_id
    )

    restarted_runtime = ActionRuntime(
        approval_service=(
            build_approval_service(
                approval_db
            )
        ),
        incident_store=(
            IncidentStore(
                incident_db
            )
        ),
    )

    restarted_runtime.executor = (
        FailingExecutor()
    )

    execution = (
        await restarted_runtime.resume(
            approval_id
        )
    )

    assert execution["success"] is False

    assert execution["status"] == (
        "execution_failed"
    )

    assert execution["error_type"] == (
        "RuntimeError"
    )

    final_store = IncidentStore(
        incident_db
    )

    stored_incident = (
        await final_store.get(
            str(
                incident_id
            )
        )
    )

    assert stored_incident is not None

    assert stored_incident.status == (
        IncidentStatus.FAILED
    )

    assert stored_incident.reason == (
        "Remediation executor raised "
        "RuntimeError: simulated executor failure"
    )
