import sqlite3

import httpx
import pytest
from fastapi import FastAPI

from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.security.models import (
    OperatorRole,
)
from services.agent_runtime.tests.api_security_support import (
    wire_api_test_security,
)


PATH = (
    "/production-actions/"
    "pilot-crash-recovery-rehearsal"
)


@pytest.fixture
def api_environment(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)

    from services.agent_runtime.app.api import (
        runtime as api_module,
    )

    runtime = AgentRuntime()
    security = wire_api_test_security(
        monkeypatch,
        api_module,
        runtime,
    )
    app = FastAPI()
    app.include_router(
        api_module.router
    )
    return app, runtime, security


def api_client(
    app: FastAPI,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app
        ),
        base_url="http://test",
    )


def role_headers(
    security,
    role: OperatorRole,
) -> dict[str, str]:
    headers = security.headers(
        role
    )
    headers["X-Operator-ID"] = (
        security.principal_id(
            role
        )
    )
    return headers


@pytest.mark.asyncio
async def test_api_crash_rehearsal_enforces_rbac_and_identity(
    api_environment,
):
    app, _, security = api_environment

    async with api_client(app) as client:
        anonymous = await client.post(
            PATH,
            headers={
                "X-Operator-ID": "anonymous-executor",
            },
        )
        viewer = await client.post(
            PATH,
            headers=role_headers(
                security,
                OperatorRole.VIEWER,
            ),
        )
        spoofed = await client.post(
            PATH,
            headers={
                **security.headers(
                    OperatorRole.EXECUTOR
                ),
                "X-Operator-ID": "spoofed-admin",
            },
        )
        executor = await client.post(
            PATH,
            headers=role_headers(
                security,
                OperatorRole.EXECUTOR,
            ),
        )
        admin = await client.post(
            PATH,
            headers=role_headers(
                security,
                OperatorRole.ADMIN,
            ),
        )

    assert anonymous.status_code == 401
    assert viewer.status_code == 403
    assert spoofed.status_code == 403
    assert executor.status_code == 200
    assert admin.status_code == 200
    assert executor.json()["operator_id"] == (
        security.principal_id(
            OperatorRole.EXECUTOR
        )
    )


@pytest.mark.asyncio
async def test_api_crash_rehearsal_is_zero_write_even_when_authorized(
    api_environment,
    monkeypatch,
):
    app, runtime, security = api_environment
    domain_calls: list[str] = []

    def forbidden_connect(*args, **kwargs):
        domain_calls.append("sqlite.connect")
        raise AssertionError(
            "Crash rehearsal read or wrote SQLite"
        )

    async def forbidden_call(*args, **kwargs):
        domain_calls.append("domain.call")
        raise AssertionError(
            "Crash rehearsal reached workflow execution"
        )

    monkeypatch.setattr(
        sqlite3,
        "connect",
        forbidden_connect,
    )
    monkeypatch.setattr(
        runtime.pipeline,
        "execute",
        forbidden_call,
    )
    monkeypatch.setattr(
        runtime.action_runtime,
        "resume",
        forbidden_call,
    )
    monkeypatch.setattr(
        runtime.verification_coordinator,
        "run",
        forbidden_call,
    )

    async with api_client(app) as client:
        response = await client.post(
            PATH,
            headers=role_headers(
                security,
                OperatorRole.EXECUTOR,
            ),
        )

    assert response.status_code == 200
    assert domain_calls == []
    body = response.json()
    report = body["report"]
    assert body["read_only"] is True
    assert body["synthetic_rehearsal"] is True
    assert report["passed"] is True
    assert report["checkpoint_count"] == 13
    assert report["passed_checkpoint_count"] == 13
    assert report["storage_read_count"] == 0
    assert report["storage_write_count"] == 0
    assert report["external_call_count"] == 0
    assert report["kubernetes_call_count"] == 0
    assert report["production_executor_call_count"] == 0
    assert report["verification_call_count"] == 0
    assert report["budget_reservation_count"] == 0
    assert report["real_write_attempted"] is False
    assert report["authorizes_enablement"] is False
    assert report["authorizes_execution"] is False


@pytest.mark.asyncio
async def test_api_crash_rehearsal_exact_replay_is_stable(
    api_environment,
):
    app, _, security = api_environment
    headers = role_headers(
        security,
        OperatorRole.EXECUTOR,
    )

    async with api_client(app) as client:
        first = await client.post(
            PATH,
            headers=headers,
        )
        second = await client.post(
            PATH,
            headers=headers,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert (
        first.json()["report"]["report_sha256"]
        == second.json()["report"]["report_sha256"]
    )


@pytest.mark.asyncio
async def test_api_crash_rehearsal_failure_is_sanitized(
    api_environment,
    monkeypatch,
):
    app, runtime, security = api_environment
    secret = "bearer-production-secret-value"

    async def fail_safely(*args, **kwargs):
        raise RuntimeError(
            f"backend failure containing {secret}"
        )

    monkeypatch.setattr(
        runtime.production_pilot_crash_recovery_rehearsal,
        "run",
        fail_safely,
    )

    async with api_client(app) as client:
        response = await client.post(
            PATH,
            headers=role_headers(
                security,
                OperatorRole.EXECUTOR,
            ),
        )

    assert response.status_code == 503
    serialized = response.text.lower()
    assert secret not in serialized
    assert "backend failure" not in serialized
    assert response.json()["detail"] == (
        "Production pilot crash recovery rehearsal "
        "could not be evaluated safely"
    )


@pytest.mark.asyncio
async def test_api_crash_rehearsal_omits_sensitive_runtime_material(
    api_environment,
):
    app, _, security = api_environment

    async with api_client(app) as client:
        response = await client.post(
            PATH,
            headers=role_headers(
                security,
                OperatorRole.EXECUTOR,
            ),
        )

    assert response.status_code == 200
    serialized = response.text.lower()
    for forbidden in (
        "authorization",
        "credential",
        "bearer",
        "token",
        "patch_json",
        "canonical_patch",
        "resource_version",
        "workload_uid",
        "idempotency_key",
        "kill_switch_file",
    ):
        assert forbidden not in serialized

