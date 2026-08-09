from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from common.config.settings import (
    KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT,
    KubernetesPreflightConfig,
    KubernetesPreflightTargetConfig,
    KubernetesProductionExecutionConfig,
)
from services.agent_runtime.app.action.production_pilot import (
    KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED,
    KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED,
    KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT,
    KubernetesProductionPilotConfig,
    KubernetesProductionPilotControl,
    ProductionPilotReadinessService,
)
from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.security.models import (
    OperatorRole,
)
from services.agent_runtime.tests.api_security_support import (
    wire_api_test_security,
)


NOW = datetime(
    2026,
    8,
    9,
    12,
    0,
    tzinfo=UTC,
)


def pilot_control(
    switch_file: str,
) -> KubernetesProductionPilotControl:
    return KubernetesProductionPilotControl(
        config=KubernetesProductionPilotConfig(
            enabled=True,
            pilot_id="oom-api-readiness-v1",
            change_ticket="CHG-7001",
            runbook_version="oom-runbook-v1",
            runbook_acknowledgement=(
                KUBERNETES_PRODUCTION_RUNBOOK_ACKNOWLEDGEMENT
            ),
            kill_switch_file=switch_file,
            authorized_operator_ids=(
                "test-executor-operator",
            ),
            starts_at=NOW,
            expires_at=(
                NOW + timedelta(hours=1)
            ),
        ),
        preflight_config=KubernetesPreflightConfig(
            enabled=True,
            api_url="https://kubernetes.test",
            cluster_name="production-a",
            bearer_token_env="K8S_PREFLIGHT_TOKEN",
            allowed_targets=(
                KubernetesPreflightTargetConfig(
                    cluster="production-a",
                    namespace="payment",
                    deployment="payment-api",
                    container="payment-api",
                ),
            ),
        ),
        execution_config=(
            KubernetesProductionExecutionConfig(
                enabled=True,
                write_acknowledgement=(
                    KUBERNETES_PRODUCTION_WRITE_ACKNOWLEDGEMENT
                ),
                bearer_token_env=(
                    "K8S_PRODUCTION_EXECUTION_TOKEN"
                ),
            )
        ),
        clock=lambda: (
            NOW + timedelta(minutes=1)
        ),
    )


@pytest.fixture
def api_environment(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(
        tmp_path
    )
    switch_file = (
        tmp_path
        / "production-pilot.switch"
    )
    switch_file.write_text(
        KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED,
        encoding="utf-8",
    )
    control = pilot_control(
        str(switch_file)
    )

    from services.agent_runtime.app.api import (
        runtime as api_module,
    )

    runtime = AgentRuntime(
        production_pilot_control=control
    )
    runtime.production_pilot_readiness = (
        ProductionPilotReadinessService(
            control=control,
            production_executor_configured=True,
        )
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
        security,
        switch_file,
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


@pytest.mark.asyncio
async def test_readiness_is_authenticated_bounded_and_read_only(
    api_environment,
):
    app, runtime, security, _ = (
        api_environment
    )
    path = (
        "/production-actions/pilot-readiness"
    )
    approvals_before = await (
        runtime.approval.manager.list_requests()
    )
    executions_before = await (
        runtime.action_execution_service
        .list_by_incident(
            "00000000-0000-4000-8000-000000000001"
        )
    )

    async with api_client(
        app
    ) as client:
        unauthenticated = await client.get(
            path
        )
        forbidden = await client.get(
            path,
            headers=security.headers(
                OperatorRole.SERVICE
            ),
        )
        first = await client.get(
            path,
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )
        replay = await client.get(
            path,
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json() == replay.json()

    body = first.json()
    readiness = body[
        "readiness"
    ]
    assert body[
        "read_only"
    ] is True
    assert readiness[
        "ready_for_enablement"
    ] is True
    assert readiness[
        "ready_for_execution"
    ] is False
    assert readiness[
        "kill_switch"
    ]["state"] == "engaged"
    assert readiness[
        "execution_blockers"
    ] == [
        "kill_switch_engaged",
    ]

    serialized = str(
        body
    ).lower()
    for forbidden_name in (
        "kill_switch_file",
        "production-pilot.switch",
        "authorization",
        "bearer",
        "token",
        "patch_json",
        "resource_version",
        "workload_uid",
    ):
        assert forbidden_name not in serialized

    assert await (
        runtime.approval.manager.list_requests()
    ) == approvals_before
    assert await (
        runtime.action_execution_service
        .list_by_incident(
            "00000000-0000-4000-8000-000000000001"
        )
    ) == executions_before


@pytest.mark.asyncio
async def test_dynamic_kill_switch_changes_readiness_without_restart(
    api_environment,
):
    app, _, security, switch_file = (
        api_environment
    )
    path = (
        "/production-actions/pilot-readiness"
    )

    async with api_client(
        app
    ) as client:
        engaged = await client.get(
            path,
            headers=security.headers(
                OperatorRole.ADMIN
            ),
        )
        switch_file.write_text(
            KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED,
            encoding="utf-8",
        )
        disengaged = await client.get(
            path,
            headers=security.headers(
                OperatorRole.ADMIN
            ),
        )
        switch_file.write_text(
            "INVALID",
            encoding="utf-8",
        )
        invalid = await client.get(
            path,
            headers=security.headers(
                OperatorRole.ADMIN
            ),
        )

    assert engaged.json()[
        "readiness"
    ]["ready_for_execution"] is False
    assert disengaged.json()[
        "readiness"
    ]["ready_for_execution"] is True
    assert invalid.json()[
        "readiness"
    ]["ready_for_execution"] is False
    assert invalid.json()[
        "readiness"
    ]["kill_switch"]["state"] == "invalid"
