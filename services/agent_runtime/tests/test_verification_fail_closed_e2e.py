import pytest

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionStatus,
)
from services.agent_runtime.app.incident.enums import (
    IncidentStatus,
)
from services.agent_runtime.app.incident.state import (
    IncidentState,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.verification.models import (
    VerificationStatus,
)


def create_isolated_runtime(
    monkeypatch,
    tmp_path,
) -> AgentRuntime:
    """
    Build a runtime that cannot reach live verification providers.

    Changing the working directory keeps Approval, Incident, Verification and
    Action Execution SQLite databases inside pytest's temporary directory.
    """

    monkeypatch.chdir(
        tmp_path
    )

    for name in (
        "PROMETHEUS_URL",
        "KUBERNETES_API_URL",
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "KUBERNETES_SERVICE_PORT_HTTPS",
    ):
        monkeypatch.delenv(
            name,
            raising=False,
        )

    monkeypatch.setenv(
        "PROMETHEUS_ALLOW_MOCK_FALLBACK",
        "true",
    )
    monkeypatch.setenv(
        "KUBERNETES_ALLOW_DRY_RUN_FALLBACK",
        "true",
    )

    return AgentRuntime()


def healing_result() -> dict:
    return {
        "agent": "healing",
        "success": True,
        "score": 1.0,
        "message": "increase memory limit",
        "data": {
            "action": "increase_memory_limit",
            "target": "payment-api",
            "risk": "medium",
            "reason": (
                "Pod memory limit exceeded"
            ),
            "rollback": (
                "Restore the previous memory limit"
            ),
            "verification": (
                "Verify pod readiness and memory headroom"
            ),
            "approval_required": True,
        },
    }


@pytest.mark.asyncio
async def test_mock_evidence_cannot_resolve_incident_end_to_end(
    monkeypatch,
    tmp_path,
):
    """
    Cover the complete local remediation and verification boundary.

    Action execution may succeed after approval, but Kubernetes dry-run and
    Prometheus mock responses are not production evidence. The Coordinator
    must therefore persist an INCONCLUSIVE verification and keep the linked
    Incident in HEALING.
    """

    runtime = create_isolated_runtime(
        monkeypatch,
        tmp_path,
    )
    current_incident = IncidentState()

    plan, execution = (
        await runtime.action_runtime.execute(
            healing_result(),
            incident=current_incident,
            namespace="payment",
            cluster="production-a",
        )
    )

    assert execution["status"] == (
        "pending_approval"
    )
    assert plan.namespace == "payment"
    assert plan.cluster == "production-a"

    approval_id = execution[
        "approval_id"
    ]
    incident_id = execution[
        "incident_id"
    ]

    await runtime.approval.approve(
        approval_id
    )

    execution = await (
        runtime.action_runtime.resume(
            approval_id,
            operator_id="pytest-sre",
            idempotency_key=(
                "verification-fail-closed-execution-1"
            ),
        )
    )

    assert execution["success"] is True
    assert execution["incident_status"] == (
        IncidentStatus.HEALING.value
    )
    assert execution["execution_status"] == (
        ActionExecutionStatus.SUCCEEDED.value
    )
    assert execution["idempotent_replay"] is False
    assert execution["automatic_replay_allowed"] is False

    execution_id = execution[
        "execution_id"
    ]

    verification, incident = await (
        runtime.verification_coordinator.run(
            incident_id=incident_id,
            plan=plan,
            namespace=plan.namespace,
            cluster=plan.cluster,
            metadata={
                "source": "pytest_e2e",
                "trigger": (
                    "post_action_execution"
                ),
            },
        )
    )

    assert verification.status == (
        VerificationStatus.INCONCLUSIVE
    )
    assert verification.required_checks_passed is False
    assert incident.status == (
        IncidentStatus.HEALING
    )
    assert "awaiting more evidence" in (
        incident.reason
    )

    assert verification.metadata[
        "namespace"
    ] == "payment"
    assert verification.metadata[
        "cluster"
    ] == "production-a"
    assert verification.metadata[
        "profile"
    ] == "increase_memory_limit_v1"

    required_checks = [
        check
        for check in verification.checks
        if check.required
    ]

    assert len(required_checks) == 2
    assert all(
        check.passed is None
        for check in required_checks
    )

    assert all(
        check.metadata.get("trusted")
        is False
        for check in verification.checks
    )
    assert all(
        any(
            "not allowed" in reason
            for reason in check.metadata.get(
                "rejection_reasons",
                [],
            )
        )
        for check in verification.checks
    )

    # A new runtime instance must observe the same Action Execution,
    # terminal verification and non-resolved Incident from SQLite.
    restarted_runtime = AgentRuntime()

    stored_execution = await (
        restarted_runtime.action_execution_service.get(
            execution_id
        )
    )
    stored_verification = await (
        restarted_runtime.verification.get(
            verification.id
        )
    )
    stored_incident = await (
        restarted_runtime.incident_store.get(
            str(
                incident.id
            )
        )
    )

    assert stored_execution is not None
    assert stored_execution.status == (
        ActionExecutionStatus.SUCCEEDED
    )
    assert stored_execution.approval_id == approval_id
    assert stored_execution.incident_id == incident.id
    assert stored_execution.action.namespace == "payment"
    assert stored_execution.action.cluster == "production-a"
    assert stored_execution.automatic_replay_allowed is False

    assert stored_verification is not None
    assert stored_verification.status == (
        VerificationStatus.INCONCLUSIVE
    )
    assert stored_incident is not None
    assert stored_incident.status == (
        IncidentStatus.HEALING
    )

    replay = await (
        restarted_runtime.action_runtime.resume(
            approval_id,
            operator_id="pytest-sre",
            idempotency_key=(
                "verification-fail-closed-execution-1"
            ),
        )
    )

    assert replay["success"] is True
    assert replay["execution_id"] == execution_id
    assert replay["execution_status"] == (
        ActionExecutionStatus.SUCCEEDED.value
    )
    assert replay["idempotent_replay"] is True
    assert replay["automatic_replay_allowed"] is False
