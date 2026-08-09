from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from services.agent_runtime.app.action.production_pilot import (
    KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED,
    KUBERNETES_PRODUCTION_KILL_SWITCH_ENGAGED,
)
from services.agent_runtime.app.action.production_pilot_budget_service import (
    ProductionPilotBudgetService,
)
from services.agent_runtime.app.action.production_pilot_budget_store import (
    ProductionPilotBudgetStore,
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
from services.agent_runtime.tests.test_api_production_pilot_readiness import (
    NOW,
    pilot_control,
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
    budget_service = ProductionPilotBudgetService(
        store=ProductionPilotBudgetStore(
            tmp_path
            / "production_pilot_budget.db"
        ),
        clock=lambda: (
            NOW + timedelta(minutes=1)
        ),
    )

    from services.agent_runtime.app.api import (
        runtime as api_module,
    )

    runtime = AgentRuntime(
        production_pilot_control=control,
        production_pilot_budget_service=(
            budget_service
        ),
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
        budget_service,
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


def executor_headers(
    security,
) -> dict[str, str]:
    headers = security.headers(
        OperatorRole.EXECUTOR
    )
    headers["X-Operator-ID"] = (
        security.principal_id(
            OperatorRole.EXECUTOR
        )
    )
    return headers


@pytest.mark.asyncio
async def test_api_rehearsal_is_authenticated_zero_write_and_read_only(
    api_environment,
):
    (
        app,
        runtime,
        security,
        _,
        budget_service,
    ) = api_environment
    path = (
        "/production-actions/pilot-rehearsal"
    )
    approvals_before = await (
        runtime.approval.manager.list_requests()
    )
    executions_before = await (
        runtime.action_execution_service.list_all()
    )

    async with api_client(app) as client:
        anonymous = await client.post(
            path,
            headers={
                "X-Operator-ID": (
                    "test-executor-operator"
                ),
            },
        )
        forbidden = await client.post(
            path,
            headers={
                **security.headers(
                    OperatorRole.VIEWER
                ),
                "X-Operator-ID": (
                    security.principal_id(
                        OperatorRole.VIEWER
                    )
                ),
            },
        )
        response = await client.post(
            path,
            headers=executor_headers(
                security
            ),
        )

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403
    assert response.status_code == 200
    body = response.json()
    rehearsal = body["rehearsal"]
    assert body["read_only"] is True
    assert rehearsal["passed"] is True
    assert rehearsal["zero_write"] is True
    assert rehearsal["budget_state"] == "available"
    assert rehearsal["durable_claim_created"] is False
    assert rehearsal["external_call_count"] == 0
    assert rehearsal["real_write_attempted"] is False
    assert await budget_service.get(
        "oom-api-readiness-v1"
    ) is None
    assert await runtime.approval.manager.list_requests() == (
        approvals_before
    )
    assert await runtime.action_execution_service.list_all() == (
        executions_before
    )

    serialized = str(body).lower()
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


@pytest.mark.asyncio
async def test_api_rehearsal_rejects_identity_spoof_and_disengaged_switch(
    api_environment,
):
    app, _, security, switch_file, _ = (
        api_environment
    )
    path = (
        "/production-actions/pilot-rehearsal"
    )

    async with api_client(app) as client:
        spoofed = await client.post(
            path,
            headers={
                **security.headers(
                    OperatorRole.EXECUTOR
                ),
                "X-Operator-ID": "spoofed-admin",
            },
        )
        switch_file.write_text(
            KUBERNETES_PRODUCTION_KILL_SWITCH_DISENGAGED,
            encoding="utf-8",
        )
        unsafe = await client.post(
            path,
            headers=executor_headers(
                security
            ),
        )

    assert spoofed.status_code == 403
    assert unsafe.status_code == 200
    rehearsal = unsafe.json()[
        "rehearsal"
    ]
    assert rehearsal["passed"] is False
    assert (
        "kill_switch_must_be_engaged_for_rehearsal"
        in rehearsal["blockers"]
    )
