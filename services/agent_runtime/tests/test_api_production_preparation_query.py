from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

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


ARTIFACT_ID = UUID(
    "00000000-0000-4000-8000-000000000921"
)
INCIDENT_ID = UUID(
    "00000000-0000-4000-8000-000000000922"
)
APPROVAL_ID = (
    "00000000-0000-4000-8000-000000000923"
)
PATCH_SHA256 = "9" * 64
READ_ROLES = (
    OperatorRole.VIEWER,
    OperatorRole.ANALYST,
    OperatorRole.APPROVER,
    OperatorRole.EXECUTOR,
    OperatorRole.RECONCILER,
    OperatorRole.ADMIN,
)


def query_result():
    prepared_at = datetime(
        2026,
        8,
        9,
        8,
        0,
        tzinfo=UTC,
    )
    contract = SimpleNamespace(
        contract_id=ARTIFACT_ID,
        action_type=SimpleNamespace(
            value="increase_memory_limit"
        ),
        policy_version=(
            "oom-memory-increase-v1"
        ),
        scope=SimpleNamespace(
            cluster="production-a",
            namespace="payment",
            kind=SimpleNamespace(
                value="Deployment"
            ),
            name="payment-api",
            container="payment-api",
        ),
        memory=SimpleNamespace(
            current_limit="512Mi",
            desired_limit="640Mi",
            rollback_limit="512Mi",
            increase_percent=25.0,
        ),
        dry_run=SimpleNamespace(
            server_dry_run=True,
            patch_sha256=PATCH_SHA256,
            warnings=(),
        ),
        prepared_at=prepared_at,
        expires_at=(
            prepared_at
            + timedelta(minutes=10)
        ),
    )
    artifact = SimpleNamespace(
        contract=contract,
        plan=SimpleNamespace(
            risk=SimpleNamespace(
                value="medium"
            ),
            metadata={
                "source_pod": (
                    "payment-api-abc"
                ),
            },
        ),
        source_restart_count=4,
        patch_json=(
            "must-never-appear-in-response"
        ),
    )
    record = SimpleNamespace(
        artifact_id=ARTIFACT_ID,
        incident_id=INCIDENT_ID,
        approval_id=APPROVAL_ID,
        idempotency_key=(
            "query-payment-api-0001"
        ),
        status=SimpleNamespace(
            value="approval_bound"
        ),
        created_at=prepared_at,
        updated_at=prepared_at,
        artifact=artifact,
    )
    approval = SimpleNamespace(
        id=APPROVAL_ID,
        status=SimpleNamespace(
            value="pending"
        ),
        action=SimpleNamespace(
            approved=False
        ),
        metadata={
            "preparation_operator_id": (
                "test-analyst-operator"
            ),
        },
    )
    incident = SimpleNamespace(
        id=INCIDENT_ID,
        status=SimpleNamespace(
            value="confirmed"
        ),
        reason=(
            "Production remediation is awaiting approval"
        ),
        updated_at=prepared_at,
    )

    return SimpleNamespace(
        record=record,
        approval=approval,
        incident=incident,
        checked_at=(
            prepared_at
            + timedelta(minutes=5)
        ),
        clock_valid=True,
        expired=False,
        remaining_seconds=300,
        replacement_preflight_required=False,
        approval_decision_required=True,
        consistency_passed=True,
        consistency_issues=(),
        execution_eligible=False,
        execution_blockers=(
            "approval_pending",
        ),
        phase="pending_approval",
    )


class RecordingQueryService:
    def __init__(
        self,
        result,
    ) -> None:
        self.result = result
        self.calls = []

    async def get(
        self,
        artifact_id,
    ):
        self.calls.append(
            artifact_id
        )
        return self.result


class FailingQueryService:
    def __init__(self) -> None:
        self.calls = 0

    async def get(
        self,
        artifact_id,
    ):
        self.calls += 1
        raise RuntimeError(
            "database path and secret detail"
        )


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

    runtime = AgentRuntime()
    query = RecordingQueryService(
        query_result()
    )
    runtime.production_action_query = query
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
        query,
        security,
        api_module,
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
@pytest.mark.parametrize(
    "role",
    READ_ROLES,
)
async def test_read_roles_receive_bounded_snapshot(
    api_environment,
    role,
):
    app, _, query, security, _ = (
        api_environment
    )

    async with api_client(
        app
    ) as client:
        response = await client.get(
            "/production-actions/"
            f"preflight-artifacts/{ARTIFACT_ID}",
            headers=security.headers(
                role
            ),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload[
        "read_only"
    ] is True
    assert payload[
        "phase"
    ] == "pending_approval"
    assert payload[
        "contract"
    ]["expired"] is False
    assert payload[
        "contract"
    ]["clock_valid"] is True
    assert payload[
        "contract"
    ]["remaining_seconds"] == 300
    assert payload[
        "consistency"
    ] == {
        "passed": True,
        "issues": [],
    }
    assert payload[
        "execution_candidate"
    ] == {
        "eligible": False,
        "blockers": [
            "approval_pending",
        ],
    }
    assert all(
        value is False
        for value in payload[
            "query_side_effects"
        ].values()
    )
    assert "patch_json" not in response.text
    assert (
        "must-never-appear-in-response"
        not in response.text
    )
    assert "resource_version" not in response.text
    assert "workload_uid" not in response.text
    assert query.calls == [
        ARTIFACT_ID
    ]


@pytest.mark.asyncio
async def test_service_role_is_denied_before_query(
    api_environment,
):
    app, _, query, security, _ = (
        api_environment
    )

    async with api_client(
        app
    ) as client:
        response = await client.get(
            "/production-actions/"
            f"preflight-artifacts/{ARTIFACT_ID}",
            headers=security.headers(
                OperatorRole.SERVICE
            ),
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Authorization denied"
    }
    assert query.calls == []


@pytest.mark.asyncio
async def test_unknown_artifact_returns_404(
    api_environment,
):
    app, _, query, security, _ = (
        api_environment
    )
    query.result = None

    async with api_client(
        app
    ) as client:
        response = await client.get(
            "/production-actions/"
            f"preflight-artifacts/{ARTIFACT_ID}",
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": (
            "Preflight Artifact not found"
        )
    }
    assert query.calls == [
        ARTIFACT_ID
    ]


@pytest.mark.asyncio
async def test_disabled_query_is_fail_closed(
    api_environment,
):
    (
        app,
        runtime,
        query,
        security,
        _,
    ) = api_environment
    runtime.production_action_query = None

    async with api_client(
        app
    ) as client:
        response = await client.get(
            "/production-actions/"
            f"preflight-artifacts/{ARTIFACT_ID}",
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "Production preparation query "
            "is unavailable"
        )
    }
    assert query.calls == []


@pytest.mark.asyncio
async def test_query_failure_is_sanitized(
    api_environment,
):
    (
        app,
        runtime,
        _,
        security,
        _,
    ) = api_environment
    query = FailingQueryService()
    runtime.production_action_query = query

    async with api_client(
        app
    ) as client:
        response = await client.get(
            "/production-actions/"
            f"preflight-artifacts/{ARTIFACT_ID}",
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "Production preparation query "
            "is unavailable"
        )
    }
    assert "database path" not in (
        response.text
    )
    assert "secret" not in response.text
    assert query.calls == 1


@pytest.mark.asyncio
async def test_query_route_never_invokes_workflow_side_effects(
    api_environment,
    monkeypatch,
):
    (
        app,
        runtime,
        _,
        security,
        _,
    ) = api_environment
    forbidden_calls = []

    def forbidden(
        label: str,
    ):
        async def call(*args, **kwargs):
            forbidden_calls.append(
                label
            )
            raise AssertionError(
                "Read-only query reached "
                f"a side effect: {label}"
            )

        return call

    monkeypatch.setattr(
        runtime.action_runtime,
        "resume",
        forbidden(
            "action_runtime.resume"
        ),
    )
    monkeypatch.setattr(
        runtime.verification_coordinator,
        "run",
        forbidden(
            "verification_coordinator.run"
        ),
    )
    monkeypatch.setattr(
        runtime.approval,
        "approve",
        forbidden(
            "approval.approve"
        ),
    )
    monkeypatch.setattr(
        runtime.approval,
        "reject",
        forbidden(
            "approval.reject"
        ),
    )

    preparation = getattr(
        runtime,
        "production_action_preparation",
        None,
    )
    if preparation is not None:
        monkeypatch.setattr(
            preparation,
            "prepare",
            forbidden(
                "production_action_preparation.prepare"
            ),
        )

    async with api_client(
        app
    ) as client:
        response = await client.get(
            "/production-actions/"
            f"preflight-artifacts/{ARTIFACT_ID}",
            headers=security.headers(
                OperatorRole.VIEWER
            ),
        )

    assert response.status_code == 200
    assert forbidden_calls == []
