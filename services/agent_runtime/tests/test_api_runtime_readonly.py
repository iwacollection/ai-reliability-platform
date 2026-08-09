from datetime import UTC, datetime
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
from services.agent_runtime.tests.api_security_support import (
    ApiTestSecurityHarness,
    wire_api_test_security,
)


@pytest.fixture
def api_environment(
    monkeypatch,
    tmp_path,
):
    """Create an API whose runtime and SQLite files are test-local."""

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


def event_payload() -> dict:
    return {
        "header": {
            "source": "alertmanager",
            "occurred_at": datetime.now(
                UTC
            ).isoformat(),
        },
        "signal": {
            "type": "alert",
            "name": "PodHighCPU",
            "severity": "critical",
            "message": "CPU > 90%",
        },
        "resources": [
            {
                "kind": "pod",
                "name": "payment-api",
                "namespace": "payment",
                "cluster": "production-a",
            }
        ],
    }


def api_client(
    app: FastAPI,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=app
        ),
        base_url="http://test",
    )


def test_api_environment_uses_shared_authenticated_security(
    api_environment,
):
    _, runtime, security = api_environment

    assert isinstance(
        security,
        ApiTestSecurityHarness,
    )
    assert security.runtime is runtime
    assert security.adapter.authentication is (
        runtime.authentication
    )
    assert security.adapter.policy is (
        runtime.security_policy
    )
    assert security.adapter.authentication.default_provider_name == (
        "api_key"
    )


@pytest.mark.asyncio
async def test_execute_returns_current_incident_and_scoped_trace_summaries(
    api_environment,
):
    app, _, security = api_environment

    async with api_client(
        app
    ) as client:
        first_response = await client.post(
            "/execute",
            json=event_payload(),
            headers=security.headers(
                OperatorRole.ANALYST,
                request_id="request-one",
            ),
        )
        second_response = await client.post(
            "/execute",
            json=event_payload(),
            headers=security.headers(
                OperatorRole.ANALYST,
                request_id="request-two",
            ),
        )

        assert first_response.status_code == 200
        assert second_response.status_code == 200

        first = first_response.json()
        second = second_response.json()

        assert first["request_id"] == "request-one"
        assert second["request_id"] == "request-two"
        assert first["incident"]["id"] != (
            second["incident"]["id"]
        )

        first_trace_ids = {
            trace["trace_id"]
            for trace in first["traces"]
        }
        second_trace_ids = {
            trace["trace_id"]
            for trace in second["traces"]
        }

        assert first_trace_ids
        assert second_trace_ids
        assert first_trace_ids.isdisjoint(
            second_trace_ids
        )
        assert first["trace_count"] == len(
            first_trace_ids
        )
        assert second["trace_count"] == len(
            second_trace_ids
        )

        allowed_trace_fields = {
            "trace_id",
            "agent",
            "duration_ms",
            "success",
            "score",
            "message",
        }

        for trace in (
            first["traces"]
            + second["traces"]
        ):
            assert set(trace) == (
                allowed_trace_fields
            )
            assert "input_data" not in trace
            assert "output_data" not in trace
            assert "spans" not in trace

        incident_response = await client.get(
            "/incidents/"
            + first["incident"]["id"],
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

        assert incident_response.status_code == 200
        assert incident_response.json()[
            "incident"
        ]["id"] == first["incident"]["id"]


@pytest.mark.asyncio
async def test_read_endpoints_return_404_for_unknown_ids(
    api_environment,
):
    app, _, security = api_environment
    unknown_id = str(
        uuid4()
    )

    paths = (
        f"/incidents/{unknown_id}",
        f"/approvals/{unknown_id}",
        f"/verifications/{unknown_id}",
    )

    async with api_client(
        app
    ) as client:
        for path in paths:
            response = await client.get(
                path,
                headers=security.headers(
                    OperatorRole.VIEWER
                ),
            )
            assert response.status_code == 404


@pytest.mark.asyncio
async def test_read_endpoints_return_persisted_workflow_records(
    api_environment,
):
    app, runtime, security = api_environment

    incident = IncidentState()
    incident.update(
        status=IncidentStatus.HEALING,
        reason=(
            "Remediation action executed; "
            "awaiting verification"
        ),
    )
    incident = await runtime.incident_store.save(
        incident
    )

    plan = ActionPlan(
        type=ActionType.INCREASE_MEMORY_LIMIT,
        target="payment-api",
        namespace="payment",
        cluster="production-a",
    )

    approval = await runtime.approval.create_approval(
        action=plan,
        reason="Medium risk action requires approval",
        incident_id=incident.id,
    )

    verification = await (
        runtime.verification_runtime.create(
            incident_id=incident.id,
            action=plan.type.value,
            target=plan.target,
            metadata={
                "namespace": plan.namespace,
                "cluster": plan.cluster,
            },
        )
    )

    async with api_client(
        app
    ) as client:
        incident_response = await client.get(
            f"/incidents/{incident.id}",
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )
        approval_response = await client.get(
            f"/approvals/{approval.id}",
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )
        verification_response = await client.get(
            f"/verifications/{verification.id}",
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert incident_response.status_code == 200
    assert approval_response.status_code == 200
    assert verification_response.status_code == 200

    incident_data = incident_response.json()[
        "incident"
    ]
    approval_data = approval_response.json()[
        "approval"
    ]
    verification_data = verification_response.json()[
        "verification"
    ]

    assert incident_data["id"] == str(
        incident.id
    )
    assert incident_data["status"] == "healing"

    assert approval_data["id"] == approval.id
    assert approval_data["status"] == "pending"
    assert approval_data["incident_id"] == str(
        incident.id
    )
    assert approval_data["action"][
        "namespace"
    ] == "payment"
    assert approval_data["action"][
        "cluster"
    ] == "production-a"

    assert verification_data["id"] == str(
        verification.id
    )
    assert verification_data["status"] == "pending"
    assert verification_data["incident_id"] == str(
        incident.id
    )
