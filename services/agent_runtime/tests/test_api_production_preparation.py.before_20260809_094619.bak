from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from services.agent_runtime.app.action.preflight_artifact_models import (
    PreflightArtifactStatus,
    ProductionActionPreparationRequest,
)
from services.agent_runtime.app.action.kubernetes_preflight import (
    KubernetesPreflightAuthorizationError,
    KubernetesPreflightConfigurationError,
    KubernetesPreflightConflictError,
    KubernetesPreflightPolicyError,
    KubernetesPreflightResourceNotFoundError,
    KubernetesPreflightResponseError,
)
from services.agent_runtime.app.action.production_action_preparation import (
    ProductionActionPreparationConflictError,
    ProductionActionPreparationService,
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


INCIDENT_ID = UUID(
    "00000000-0000-4000-8000-000000000711"
)
ARTIFACT_ID = UUID(
    "00000000-0000-4000-8000-000000000712"
)
APPROVAL_ID = (
    "00000000-0000-4000-8000-000000000713"
)
PATCH_SHA256 = "7" * 64


def request_body() -> dict:
    return {
        "preflight": {
            "incident_id": str(
                INCIDENT_ID
            ),
            "cluster": "production-tw",
            "namespace": "payments",
            "pod_name": (
                "payment-api-7d4c9f8b56-x9k2p"
            ),
            "container": "payment-api",
            "reason": (
                "Confirmed OOMKilled requires bounded memory increase"
            ),
        },
        "approval_reason": (
            "Production remediation requires explicit approval"
        ),
    }


def preparation_result(
    *,
    artifact_created: bool,
    approval_created: bool,
):
    prepared_at = datetime(
        2026,
        8,
        9,
        12,
        0,
        tzinfo=UTC,
    )
    contract = SimpleNamespace(
        contract_id=ARTIFACT_ID,
        incident_id=INCIDENT_ID,
        action_type=SimpleNamespace(
            value="increase_memory_limit"
        ),
        scope=SimpleNamespace(
            cluster="production-tw",
            namespace="payments",
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
            patch_sha256=PATCH_SHA256,
            server_dry_run=True,
        ),
        policy_version=(
            "oom-memory-increase-v1"
        ),
        prepared_at=prepared_at,
        expires_at=(
            prepared_at
            + timedelta(minutes=10)
        ),
    )
    record = SimpleNamespace(
        artifact_id=ARTIFACT_ID,
        incident_id=INCIDENT_ID,
        idempotency_key=(
            "prepare-payment-api-001"
        ),
        status=(
            PreflightArtifactStatus
            .APPROVAL_BOUND
        ),
        artifact=SimpleNamespace(
            contract=contract,
            plan=SimpleNamespace(
                risk=SimpleNamespace(
                    value="medium"
                )
            ),
        ),
    )
    approval = SimpleNamespace(
        id=APPROVAL_ID,
        status=SimpleNamespace(
            value="pending"
        ),
    )

    return SimpleNamespace(
        record=record,
        approval=approval,
        artifact_created=artifact_created,
        approval_created=approval_created,
        idempotent_replay=(
            not artifact_created
            and not approval_created
        ),
    )


class RecordingPreparationService:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                ProductionActionPreparationRequest,
                str | None,
            ]
        ] = []

    async def prepare(
        self,
        request: ProductionActionPreparationRequest,
        *,
        operator_id: str | None = None,
    ):
        self.calls.append(
            (
                request,
                operator_id,
            )
        )
        first = len(
            self.calls
        ) == 1
        return preparation_result(
            artifact_created=first,
            approval_created=first,
        )


class FailingPreparationService:
    def __init__(
        self,
        error: Exception,
    ) -> None:
        self.error = error
        self.calls = 0

    async def prepare(
        self,
        request,
        *,
        operator_id=None,
    ):
        self.calls += 1
        raise self.error


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
    service = RecordingPreparationService()
    runtime.production_action_preparation = (
        service
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
        service,
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


def headers(
    security,
    role: OperatorRole,
    *,
    idempotency_key: str = (
        "prepare-payment-api-001"
    ),
) -> dict[str, str]:
    return security.headers(
        role,
        idempotency_key=(
            idempotency_key
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    (
        OperatorRole.ANALYST,
        OperatorRole.SERVICE,
        OperatorRole.ADMIN,
    ),
)
async def test_authorized_roles_prepare_without_executing(
    api_environment,
    monkeypatch,
    role,
):
    (
        app,
        runtime,
        service,
        security,
        _,
    ) = api_environment

    async def forbidden(*args, **kwargs):
        raise AssertionError(
            "Preparation API must not execute or verify an Action"
        )

    monkeypatch.setattr(
        runtime.action_runtime,
        "resume",
        forbidden,
    )
    monkeypatch.setattr(
        runtime.verification_coordinator,
        "run",
        forbidden,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/production-actions/prepare",
            json=request_body(),
            headers=headers(
                security,
                role,
            ),
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload[
        "execution_started"
    ] is False
    assert payload[
        "verification_started"
    ] is False
    assert payload[
        "artifact"
    ]["server_dry_run"] is True
    assert payload[
        "artifact"
    ]["artifact_id"] == str(
        ARTIFACT_ID
    )
    assert payload[
        "approval"
    ]["approval_id"] == APPROVAL_ID
    assert payload[
        "action"
    ]["desired_memory_limit"] == "640Mi"
    assert "patch_json" not in response.text
    assert "Authorization" not in response.text
    assert "api_key" not in response.text.lower()
    assert len(
        service.calls
    ) == 1
    request, operator_id = service.calls[0]
    assert isinstance(
        request,
        ProductionActionPreparationRequest,
    )
    assert request.idempotency_key == (
        "prepare-payment-api-001"
    )
    assert operator_id == (
        security.principal_id(
            role
        )
    )


@pytest.mark.asyncio
async def test_exact_replay_reuses_artifact_and_approval(
    api_environment,
):
    app, _, service, security, _ = (
        api_environment
    )
    request_headers = headers(
        security,
        OperatorRole.ANALYST,
    )

    async with api_client(
        app
    ) as client:
        first = await client.post(
            "/production-actions/prepare",
            json=request_body(),
            headers=request_headers,
        )
        replay = await client.post(
            "/production-actions/prepare",
            json=request_body(),
            headers=request_headers,
        )

    assert first.status_code == 201
    assert replay.status_code == 200
    first_payload = first.json()
    replay_payload = replay.json()
    assert replay_payload[
        "idempotent_replay"
    ] is True
    assert replay_payload[
        "artifact"
    ]["created"] is False
    assert replay_payload[
        "approval"
    ]["created"] is False
    assert replay_payload[
        "artifact"
    ]["artifact_id"] == first_payload[
        "artifact"
    ]["artifact_id"]
    assert replay_payload[
        "approval"
    ]["approval_id"] == first_payload[
        "approval"
    ]["approval_id"]
    assert len(
        service.calls
    ) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    (
        OperatorRole.VIEWER,
        OperatorRole.APPROVER,
        OperatorRole.EXECUTOR,
        OperatorRole.RECONCILER,
    ),
)
async def test_unprivileged_roles_are_rejected_before_preparation(
    api_environment,
    role,
):
    app, _, service, security, _ = (
        api_environment
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/production-actions/prepare",
            json=request_body(),
            headers=headers(
                security,
                role,
            ),
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Authorization denied"
    }
    assert service.calls == []


@pytest.mark.asyncio
async def test_authentication_precedes_body_and_header_validation(
    api_environment,
):
    app, _, service, _, _ = (
        api_environment
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/production-actions/prepare",
            content=b"not-json",
            headers={
                "Content-Type": (
                    "application/json"
                ),
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication failed"
    }
    assert service.calls == []


@pytest.mark.asyncio
async def test_disabled_preparation_fails_closed(
    api_environment,
):
    (
        app,
        runtime,
        service,
        security,
        _,
    ) = api_environment
    runtime.production_action_preparation = (
        None
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/production-actions/prepare",
            json=request_body(),
            headers=headers(
                security,
                OperatorRole.ANALYST,
            ),
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "Production action preparation "
            "is unavailable"
        )
    }
    assert service.calls == []


@pytest.mark.asyncio
async def test_body_cannot_supply_a_second_idempotency_key(
    api_environment,
):
    app, _, service, security, _ = (
        api_environment
    )
    body = request_body()
    body[
        "idempotency_key"
    ] = "untrusted-body-key"

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/production-actions/prepare",
            json=body,
            headers=headers(
                security,
                OperatorRole.ANALYST,
            ),
        )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
async def test_claimed_operator_header_cannot_override_identity(
    api_environment,
):
    app, _, service, security, _ = (
        api_environment
    )
    request_headers = headers(
        security,
        OperatorRole.ANALYST,
    )
    request_headers[
        "X-Operator-ID"
    ] = "spoofed-production-admin"

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/production-actions/prepare",
            json=request_body(),
            headers=request_headers,
        )

    assert response.status_code == 201
    assert service.calls[0][1] == (
        security.principal_id(
            OperatorRole.ANALYST
        )
    )
    assert (
        "spoofed-production-admin"
        not in response.text
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "error",
        "expected_status",
        "expected_detail",
    ),
    (
        (
            KubernetesPreflightPolicyError(
                "secret allowlist detail"
            ),
            403,
            (
                "Production remediation target "
                "is not allowed"
            ),
        ),
        (
            KubernetesPreflightResourceNotFoundError(
                "secret resource detail"
            ),
            404,
            (
                "Kubernetes preflight resource "
                "was not found"
            ),
        ),
        (
            KubernetesPreflightConflictError(
                "secret resourceVersion detail"
            ),
            409,
            (
                "Production action preparation "
                "conflicts with persisted state"
            ),
        ),
        (
            ProductionActionPreparationConflictError(
                "secret approval detail"
            ),
            409,
            (
                "Production action preparation "
                "conflicts with persisted state"
            ),
        ),
        (
            KubernetesPreflightConfigurationError(
                "secret credential detail"
            ),
            503,
            (
                "Production action preparation "
                "is unavailable"
            ),
        ),
        (
            KubernetesPreflightAuthorizationError(
                "secret bearer token detail"
            ),
            503,
            (
                "Production action preparation "
                "is unavailable"
            ),
        ),
        (
            KubernetesPreflightResponseError(
                "secret upstream detail"
            ),
            502,
            "Kubernetes preflight failed",
        ),
    ),
)
async def test_domain_failures_use_safe_http_mapping(
    api_environment,
    error,
    expected_status,
    expected_detail,
):
    (
        app,
        runtime,
        _,
        security,
        _,
    ) = api_environment
    service = FailingPreparationService(
        error
    )
    runtime.production_action_preparation = (
        service
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/production-actions/prepare",
            json=request_body(),
            headers=headers(
                security,
                OperatorRole.ANALYST,
            ),
        )

    assert response.status_code == (
        expected_status
    )
    assert response.json() == {
        "detail": expected_detail
    }
    assert str(
        error
    ) not in response.text
    assert service.calls == 1


def test_authenticated_operator_is_bound_to_approval_metadata():
    record = SimpleNamespace(
        artifact_id=ARTIFACT_ID,
        idempotency_key=(
            "prepare-payment-api-001"
        ),
        artifact=SimpleNamespace(
            contract=SimpleNamespace(
                contract_id=ARTIFACT_ID,
                dry_run=SimpleNamespace(
                    patch_sha256=(
                        PATCH_SHA256
                    )
                ),
            )
        ),
    )

    metadata = (
        ProductionActionPreparationService
        ._approval_metadata(
            record,
            operator_id=(
                "test-analyst-operator"
            ),
        )
    )

    assert metadata[
        "preparation_operator_id"
    ] == "test-analyst-operator"
    assert "credential" not in metadata
    assert "api_key" not in metadata
