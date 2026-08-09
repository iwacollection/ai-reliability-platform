from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from services.agent_runtime.app.approval.models import (
    ApprovalStatus,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.security.models import (
    OperatorRole,
)
from services.agent_runtime.tests.api_security_support import (
    ApiTestSecurityHarness,
    wire_api_test_security,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
    MutableClock,
    NOW,
    persist_prepared_workflow,
    resolver,
)


class RecordingExecutor:
    def __init__(
        self,
    ) -> None:
        self.calls = []

    async def execute(
        self,
        action,
    ):
        self.calls.append(
            action
        )
        return {
            "success": True,
            "message": "unexpected execution",
        }


@pytest.fixture
def api_environment(
    monkeypatch,
    tmp_path,
):
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

    from services.agent_runtime.app.api import runtime as api_module

    runtime = AgentRuntime(
        kubernetes_preflight=resolver()
    )
    clock = MutableClock(
        NOW
        + timedelta(minutes=1)
    )
    runtime.production_action_guard._clock = (
        clock
    )
    security = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
    )
    app = FastAPI()
    app.include_router(
        api_module.router
    )

    return (
        app,
        runtime,
        clock,
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


async def create_workflow(
    runtime: AgentRuntime,
):
    return await persist_prepared_workflow(
        artifact_service=(
            runtime.preflight_artifact_service
        ),
        approval_service=runtime.approval,
        incident_store=runtime.incident_store,
    )


def approval_headers(
    security: ApiTestSecurityHarness,
    *,
    idempotency_key: str,
) -> dict[str, str]:
    return security.headers(
        OperatorRole.APPROVER,
        include_operator_id=True,
        idempotency_key=idempotency_key,
    )


def execution_headers(
    security: ApiTestSecurityHarness,
) -> dict[str, str]:
    return security.headers(
        OperatorRole.EXECUTOR,
        include_operator_id=True,
        idempotency_key=(
            "api-expired-execution-0001"
        ),
    )


def approval_body() -> dict:
    return {
        "reason": (
            "Reviewed bounded production remediation"
        ),
        "metadata": {
            "ticket": "INC-EXPIRY-1001",
        },
    }


@pytest.mark.asyncio
async def test_expired_prepare_cannot_be_approved_through_api(
    api_environment,
):
    app, runtime, clock, security = (
        api_environment
    )
    await create_workflow(
        runtime
    )
    clock.set(
        NOW
        + timedelta(minutes=11)
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            f"/approvals/{APPROVAL_ID}/approve",
            json=approval_body(),
            headers=approval_headers(
                security,
                idempotency_key=(
                    "api-expired-approve-0001"
                ),
            ),
        )

    assert response.status_code == 409
    assert "Safety Contract has expired" in (
        response.json()["detail"]
    )
    current = await runtime.approval.get(
        APPROVAL_ID
    )
    assert current.status == ApprovalStatus.PENDING
    assert current.action.approved is False
    assert current.decision is None
    assert await runtime.action_execution_service.get_by_approval(
        APPROVAL_ID
    ) is None


@pytest.mark.asyncio
async def test_exact_api_approval_replay_is_safe_after_expiry(
    api_environment,
):
    app, runtime, clock, security = (
        api_environment
    )
    await create_workflow(
        runtime
    )
    headers = approval_headers(
        security,
        idempotency_key=(
            "api-approval-replay-0001"
        ),
    )

    async with api_client(
        app
    ) as client:
        first = await client.post(
            f"/approvals/{APPROVAL_ID}/approve",
            json=approval_body(),
            headers=headers,
        )
        clock.set(
            NOW
            + timedelta(minutes=11)
        )
        replay = await client.post(
            f"/approvals/{APPROVAL_ID}/approve",
            json=approval_body(),
            headers=headers,
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()


@pytest.mark.asyncio
async def test_expired_prepare_can_be_rejected_through_api(
    api_environment,
):
    app, runtime, clock, security = (
        api_environment
    )
    await create_workflow(
        runtime
    )
    clock.set(
        NOW
        + timedelta(minutes=11)
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            f"/approvals/{APPROVAL_ID}/reject",
            json={
                "reason": (
                    "Expired preparation is rejected"
                )
            },
            headers=approval_headers(
                security,
                idempotency_key=(
                    "api-expired-reject-0001"
                ),
            ),
        )

    assert response.status_code == 200
    assert response.json()[
        "approval"
    ]["status"] == "rejected"


@pytest.mark.asyncio
async def test_expired_resume_has_zero_execution_and_verification(
    api_environment,
    monkeypatch,
):
    app, runtime, clock, security = (
        api_environment
    )
    _, approval, _ = await create_workflow(
        runtime
    )
    approved = await runtime.approval.approve(
        approval.id,
        operator_id="test-approver-operator",
        idempotency_key=(
            "api-resume-approval-0001"
        ),
        reason="Approved before expiry",
        metadata={
            "source": "test",
        },
    )
    assert approved.status == (
        ApprovalStatus.APPROVED
    )
    clock.set(
        NOW
        + timedelta(minutes=11)
    )
    executor = RecordingExecutor()
    runtime.action_runtime.executor = executor
    verification_calls = []

    async def forbidden_verification(
        *args,
        **kwargs,
    ):
        verification_calls.append(
            True
        )
        raise AssertionError(
            "Expired Action reached Verification"
        )

    monkeypatch.setattr(
        runtime.verification_coordinator,
        "run",
        forbidden_verification,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            f"/approvals/{APPROVAL_ID}/resume",
            headers=execution_headers(
                security
            ),
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload[
        "execution"
    ]["status"] == (
        "preflight_contract_expired"
    )
    assert payload[
        "execution"
    ]["executor_called"] is False
    assert payload[
        "verification"
    ] is None
    assert executor.calls == []
    assert verification_calls == []
    assert await runtime.action_execution_service.get_by_approval(
        APPROVAL_ID
    ) is None
