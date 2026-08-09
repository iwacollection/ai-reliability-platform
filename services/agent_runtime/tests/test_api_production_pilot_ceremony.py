import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from services.agent_runtime.app.action.production_pilot_ceremony_models import (
    PRODUCTION_PILOT_ACTIVATION_ACKNOWLEDGEMENT,
)
from services.agent_runtime.app.runtime.runtime import AgentRuntime
from services.agent_runtime.app.security.models import OperatorRole
from services.agent_runtime.tests.api_security_support import (
    wire_api_test_security,
)
from services.agent_runtime.tests.production_action_expiry_support import (
    APPROVAL_ID,
)
from services.agent_runtime.tests.test_production_pilot_ceremony import (
    prepared_service,
)


def request_body(
    executor_operator_id: str = "executor-pilot-1",
) -> dict[str, object]:
    return {
        "executor_operator_id": executor_operator_id,
        "exact_target_verified": True,
        "separate_credentials_verified": True,
        "rollback_reviewed": True,
        "monitoring_ready": True,
        "kill_switch_tested": True,
        "budget_available_verified": True,
        "runbook_reviewed": True,
        "acknowledgement": (
            PRODUCTION_PILOT_ACTIVATION_ACKNOWLEDGEMENT
        ),
    }


def api_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


@pytest_asyncio.fixture
async def api_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    ceremony_service, budget_service = await prepared_service(
        tmp_path
    )

    from services.agent_runtime.app.api import runtime as api_module

    runtime = AgentRuntime()
    runtime.production_pilot_ceremony = ceremony_service
    runtime.production_pilot_ceremony_store = ceremony_service.store
    security = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
    )
    app = FastAPI()
    app.include_router(api_module.router)
    return app, runtime, security, ceremony_service, budget_service


def approval_headers(security) -> dict[str, str]:
    return security.headers(
        OperatorRole.APPROVER,
        include_operator_id=True,
        idempotency_key="pilot-ceremony-api-0001",
    )


@pytest.mark.asyncio
async def test_api_records_bounded_zero_write_evidence_and_exact_replay(
    api_environment,
):
    app, runtime, security, ceremony_service, budget_service = api_environment
    path = (
        f"/production-actions/{APPROVAL_ID}"
        "/pilot-activation-checklist"
    )
    executions_before = await runtime.action_execution_service.list_all()

    async with api_client(app) as client:
        first = await client.post(
            path,
            json=request_body(),
            headers=approval_headers(security),
        )
        replay = await client.post(
            path,
            json=request_body(),
            headers=approval_headers(security),
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    first_body = first.json()
    replay_body = replay.json()
    assert first_body["created"] is True
    assert replay_body["created"] is False
    assert replay_body["idempotent_replay"] is True
    assert first_body["ceremony"] == replay_body["ceremony"]
    assert first_body["action_execution_created"] is False
    assert first_body["external_call_count"] == 0
    assert first_body["real_write_attempted"] is False
    assert await budget_service.get("oom-pilot-v1") is None
    assert await runtime.action_execution_service.list_all() == executions_before
    assert await ceremony_service.get_by_approval(APPROVAL_ID) is not None

    serialized = str(first_body).lower()
    for forbidden_name in (
        "patch_json",
        "workload_uid",
        "resource_version",
        "authorization",
        "bearer",
        "token",
        "kill_switch_file",
    ):
        assert forbidden_name not in serialized


@pytest.mark.asyncio
async def test_api_security_and_conflict_fail_before_workflow_side_effects(
    api_environment,
):
    app, runtime, security, ceremony_service, budget_service = api_environment
    path = (
        f"/production-actions/{APPROVAL_ID}"
        "/pilot-activation-checklist"
    )

    async with api_client(app) as client:
        anonymous = await client.post(
            path,
            json=request_body(),
            headers={
                "X-Operator-ID": "anonymous-reviewer",
                "Idempotency-Key": "pilot-ceremony-anonymous-0001",
            },
        )
        forbidden = await client.post(
            path,
            json=request_body(),
            headers=security.headers(
                OperatorRole.VIEWER,
                include_operator_id=True,
                idempotency_key="pilot-ceremony-viewer-0001",
            ),
        )
        spoofed = await client.post(
            path,
            json=request_body(),
            headers={
                **security.headers(
                    OperatorRole.APPROVER,
                    idempotency_key="pilot-ceremony-spoof-0001",
                ),
                "X-Operator-ID": "spoofed-admin",
            },
        )
        wrong_executor = await client.post(
            path,
            json=request_body("executor-not-allowlisted"),
            headers=security.headers(
                OperatorRole.APPROVER,
                include_operator_id=True,
                idempotency_key="pilot-ceremony-wrong-executor-0001",
            ),
        )

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403
    assert spoofed.status_code == 403
    assert wrong_executor.status_code == 409
    assert await ceremony_service.get_by_approval(APPROVAL_ID) is None
    assert await budget_service.get("oom-pilot-v1") is None
    assert await runtime.action_execution_service.list_all() == []
