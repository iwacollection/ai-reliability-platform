from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
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
from services.agent_runtime.app.security.models import (
    OperatorRole,
)
from services.agent_runtime.app.verification.models import (
    VerificationCheck,
)
from services.agent_runtime.tests.api_security_support import (
    ApiTestSecurityHarness,
    wire_api_test_security,
)


class CountingCollector:
    """Return deterministic evidence and expose unexpected repeated probes."""

    def __init__(
        self,
        *,
        required_passed: bool | None = True,
    ) -> None:
        self.required_passed = (
            required_passed
        )
        self.calls = 0

    async def collect(
        self,
        probes,
        context=None,
    ) -> list[VerificationCheck]:
        self.calls += 1

        return [
            VerificationCheck(
                name=probe.name,
                source=probe.source,
                passed=(
                    self.required_passed
                    if probe.required
                    else True
                ),
                required=probe.required,
                observed_value=(
                    "workflow-query-evidence"
                ),
                expected_value=(
                    "verification-profile-rule"
                ),
                message=(
                    "Deterministic Workflow Query test evidence"
                ),
            )
            for probe in probes
        ]


@pytest.fixture
def api_environment(
    monkeypatch,
    tmp_path,
):
    """Create a test-local Runtime and isolated SQLite databases."""

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

    from services.agent_runtime.app.api import (
        runtime as api_module,
    )

    isolated_runtime = AgentRuntime()
    security = wire_api_test_security(
        monkeypatch,
        api_module,
        isolated_runtime,
    )

    app = FastAPI()
    app.include_router(
        api_module.router
    )

    return (
        app,
        isolated_runtime,
        security,
    )


def api_client(
    app: FastAPI,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app
        ),
        base_url="http://test",
    )


def approval_headers(
    security: ApiTestSecurityHarness,
    approval_id: str,
) -> dict[str, str]:
    return security.headers(
        OperatorRole.APPROVER,
        include_operator_id=True,
        idempotency_key=(
            f"workflow-approval:{approval_id}"
        ),
    )


def execution_headers(
    security: ApiTestSecurityHarness,
    approval_id: str,
) -> dict[str, str]:
    return security.headers(
        OperatorRole.EXECUTOR,
        include_operator_id=True,
        idempotency_key=(
            f"workflow-execution:{approval_id}"
        ),
    )


def healing_result() -> dict[str, Any]:
    return {
        "agent": "healing",
        "success": True,
        "score": 1.0,
        "message": "increase memory limit",
        "data": {
            "action": (
                "increase_memory_limit"
            ),
            "target": "payment-api",
            "risk": "medium",
            "reason": (
                "Pod memory limit exceeded"
            ),
            "approval_required": True,
        },
    }


async def create_pending_action(
    runtime: AgentRuntime,
):
    incident = IncidentState()

    plan, execution = await (
        runtime.action_runtime.execute(
            healing_result(),
            incident=incident,
            namespace="payment",
            cluster="production-a",
        )
    )

    assert execution["status"] == (
        "pending_approval"
    )

    return (
        incident,
        plan,
        execution["approval_id"],
    )


async def approve_action(
    client: httpx.AsyncClient,
    security: ApiTestSecurityHarness,
    approval_id: str,
) -> None:
    response = await client.post(
        f"/approvals/{approval_id}/approve",
        json={
            "reason": "Workflow execution approved"
        },
        headers=approval_headers(
            security,
            approval_id
        ),
    )

    assert response.status_code == 200
    assert response.json()["approval"][
        "status"
    ] == "approved"


def workflow_path(
    approval_id: str,
) -> str:
    return (
        f"/workflows/approvals/{approval_id}"
    )


@pytest.mark.asyncio
async def test_unknown_approval_returns_404(
    api_environment,
):
    app, _, security = api_environment

    async with api_client(
        app
    ) as client:
        response = await client.get(
            workflow_path(
                str(
                    uuid4()
                )
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "Approval request not found"
    )


@pytest.mark.asyncio
async def test_query_tracks_pending_and_approved_awaiting_execution(
    api_environment,
):
    app, runtime, security = api_environment
    incident, _, approval_id = (
        await create_pending_action(
            runtime
        )
    )

    async with api_client(
        app
    ) as client:
        pending_response = await client.get(
            workflow_path(
                approval_id
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )
        await approve_action(
            client,
            security,
            approval_id,
        )
        approved_response = await client.get(
            workflow_path(
                approval_id
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert pending_response.status_code == 200
    pending = pending_response.json()
    assert pending["success"] is True
    assert pending["workflow_success"] is False
    assert pending["workflow_status"] == (
        "pending_approval"
    )
    assert pending["terminal"] is False
    assert pending["follow_up_required"] is True
    assert pending[
        "manual_reconciliation_required"
    ] is False
    assert pending["consistency"] == {
        "passed": True,
        "issues": [],
    }
    assert pending["incident"]["id"] == str(
        incident.id
    )
    assert pending["action_execution"] is None
    assert pending["verification"] is None

    assert approved_response.status_code == 200
    approved = approved_response.json()
    assert approved["workflow_status"] == (
        "approved_awaiting_execution"
    )
    assert approved["workflow_success"] is False
    assert approved["approval"]["status"] == (
        "approved"
    )
    assert approved["approval"]["action"][
        "approved"
    ] is True
    assert approved["consistency"][
        "passed"
    ] is True


@pytest.mark.asyncio
async def test_missing_incident_link_is_reported_as_inconsistent(
    api_environment,
):
    app, runtime, security = api_environment
    approval = await (
        runtime.approval.create_approval(
            action=ActionPlan(
                type=(
                    ActionType.INCREASE_MEMORY_LIMIT
                ),
                target="payment-api",
                namespace="payment",
                cluster="production-a",
            ),
            reason="Legacy unlinked approval",
        )
    )

    async with api_client(
        app
    ) as client:
        response = await client.get(
            workflow_path(
                approval.id
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["workflow_success"] is False
    assert body["workflow_status"] == (
        "inconsistent"
    )
    assert body["terminal"] is False
    assert body["follow_up_required"] is True
    assert body[
        "manual_reconciliation_required"
    ] is True
    assert body["consistency"] == {
        "passed": False,
        "issues": [
            "approval_missing_incident_link"
        ],
    }
    assert body["incident"] is None


@pytest.mark.asyncio
async def test_resolved_query_is_complete_replayable_and_read_only(
    api_environment,
    monkeypatch,
):
    app, runtime, security = api_environment
    incident, _, approval_id = (
        await create_pending_action(
            runtime
        )
    )
    collector = CountingCollector(
        required_passed=True
    )
    runtime.verification_coordinator.collector = (
        collector
    )

    async with api_client(
        app
    ) as client:
        await approve_action(
            client,
            security,
            approval_id,
        )
        resume_response = await client.post(
            f"/approvals/{approval_id}/resume",
            headers=execution_headers(
                security,
                approval_id
            ),
        )

        assert resume_response.status_code == 200
        resume = resume_response.json()

        async def forbidden_mutation(
            *args,
            **kwargs,
        ):
            pytest.fail(
                "Workflow Query attempted a state mutation"
            )

        monkeypatch.setattr(
            runtime.action_runtime,
            "resume",
            forbidden_mutation,
        )
        monkeypatch.setattr(
            runtime.verification_coordinator,
            "run",
            forbidden_mutation,
        )
        monkeypatch.setattr(
            runtime.verification_coordinator.collector,
            "collect",
            forbidden_mutation,
        )
        monkeypatch.setattr(
            runtime.incident_store,
            "update",
            forbidden_mutation,
        )
        monkeypatch.setattr(
            runtime.action_execution_service,
            "claim",
            forbidden_mutation,
        )
        monkeypatch.setattr(
            runtime.verification,
            "claim_verification",
            forbidden_mutation,
        )

        first_response = await client.get(
            workflow_path(
                approval_id
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )
        replay_response = await client.get(
            workflow_path(
                approval_id
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert first_response.status_code == 200
    assert replay_response.status_code == 200
    assert first_response.json() == (
        replay_response.json()
    )
    assert collector.calls == 1

    body = first_response.json()
    assert body["success"] is True
    assert body["workflow_success"] is True
    assert body["workflow_status"] == "resolved"
    assert body["terminal"] is True
    assert body["follow_up_required"] is False
    assert body[
        "manual_reconciliation_required"
    ] is False
    assert body["consistency"] == {
        "passed": True,
        "issues": [],
    }
    assert body["approval"]["status"] == (
        "approved"
    )
    assert body["action_execution"][
        "status"
    ] == "succeeded"
    assert body["verification"]["status"] == (
        "passed"
    )
    assert body["incident"]["status"] == (
        "resolved"
    )
    assert body["incident"]["id"] == str(
        incident.id
    )
    assert body["links"]["execution_id"] == (
        resume["execution"]["execution_id"]
    )
    assert body["links"][
        "verification_id"
    ] == resume["verification"]["id"]
    assert body["links"][
        "verification_action_execution_id"
    ] == body["links"]["execution_id"]


@pytest.mark.asyncio
async def test_passed_verification_with_unsynced_incident_fails_closed(
    api_environment,
    monkeypatch,
):
    app, runtime, security = api_environment
    incident, _, approval_id = (
        await create_pending_action(
            runtime
        )
    )
    collector = CountingCollector(
        required_passed=True
    )
    runtime.verification_coordinator.collector = (
        collector
    )
    original_update = (
        runtime.incident_store.update
    )
    failed_once = {
        "value": False
    }

    async def fail_first_resolved_update(
        incident_state,
        *args,
        **kwargs,
    ):
        if (
            incident_state.status
            == IncidentStatus.RESOLVED
            and not failed_once["value"]
        ):
            failed_once["value"] = True
            raise RuntimeError(
                "Injected Incident synchronization failure"
            )

        return await original_update(
            incident_state,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        runtime.incident_store,
        "update",
        fail_first_resolved_update,
    )

    async with api_client(
        app
    ) as client:
        await approve_action(
            client,
            security,
            approval_id,
        )
        resume_response = await client.post(
            f"/approvals/{approval_id}/resume",
            headers=execution_headers(
                security,
                approval_id
            ),
        )
        first_query = await client.get(
            workflow_path(
                approval_id
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )
        replay_query = await client.get(
            workflow_path(
                approval_id
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert resume_response.status_code == 503
    assert first_query.status_code == 200
    assert replay_query.status_code == 200
    assert first_query.json() == replay_query.json()
    assert collector.calls == 1
    assert failed_once["value"] is True

    body = first_query.json()
    assert body["success"] is True
    assert body["workflow_success"] is False
    assert body["workflow_status"] == (
        "inconsistent"
    )
    assert body["terminal"] is False
    assert body["follow_up_required"] is True
    assert body[
        "manual_reconciliation_required"
    ] is True
    assert body["consistency"] == {
        "passed": False,
        "issues": [
            "verification_incident_status_mismatch"
        ],
    }
    assert body["action_execution"][
        "status"
    ] == "succeeded"
    assert body["verification"]["status"] == (
        "passed"
    )
    assert body["incident"]["id"] == str(
        incident.id
    )
    assert body["incident"]["status"] == (
        "healing"
    )


@pytest.mark.asyncio
async def test_indeterminate_action_requires_manual_reconciliation(
    api_environment,
    monkeypatch,
):
    app, runtime, security = api_environment
    incident, _, approval_id = (
        await create_pending_action(
            runtime
        )
    )
    executor_calls = {
        "count": 0
    }

    async def indeterminate_execute(
        *args,
        **kwargs,
    ):
        executor_calls["count"] += 1
        raise RuntimeError(
            "External action outcome is unknown"
        )

    monkeypatch.setattr(
        runtime.action_runtime.executor,
        "execute",
        indeterminate_execute,
    )
    collector = CountingCollector()
    runtime.verification_coordinator.collector = (
        collector
    )

    async with api_client(
        app
    ) as client:
        await approve_action(
            client,
            security,
            approval_id,
        )
        resume_response = await client.post(
            f"/approvals/{approval_id}/resume",
            headers=execution_headers(
                security,
                approval_id
            ),
        )
        query_response = await client.get(
            workflow_path(
                approval_id
            ),
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert resume_response.status_code == 503
    assert query_response.status_code == 200
    assert executor_calls["count"] == 1
    assert collector.calls == 0

    body = query_response.json()
    assert body["success"] is True
    assert body["workflow_success"] is False
    assert body["workflow_status"] == (
        "action_indeterminate"
    )
    assert body["terminal"] is False
    assert body["follow_up_required"] is True
    assert body[
        "manual_reconciliation_required"
    ] is True
    assert body["consistency"] == {
        "passed": True,
        "issues": [],
    }
    assert body["action_execution"][
        "status"
    ] == "indeterminate"
    assert body["verification"] is None
    assert body["incident"]["id"] == str(
        incident.id
    )
    assert body["incident"]["status"] == (
        "healing"
    )
