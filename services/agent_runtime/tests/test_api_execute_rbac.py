from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

from services.agent_runtime.app.runtime.runtime import (
    AgentRuntime,
)
from services.agent_runtime.app.security.api import (
    ApiSecurityAdapter,
)
from services.agent_runtime.app.security.authentication import (
    BaseAuthenticationProvider,
)
from services.agent_runtime.app.security.models import (
    OperatorIdentity,
    OperatorRole,
)
from services.agent_runtime.app.security.service import (
    AuthenticationProviderRegistry,
    AuthenticationService,
)
from services.agent_runtime.tests.api_security_support import (
    ApiTestSecurityHarness,
    wire_api_test_security,
)


INVALID_CREDENTIAL = (
    "api-execute-rbac-invalid-key-0000000001"
)


PROVIDER_FAILURE_CREDENTIAL = (
    "api-execute-rbac-provider-failure-key-001"
)


@pytest.fixture
def api_environment(
    monkeypatch,
    tmp_path,
):
    """Create one authenticated API with test-local Runtime state."""

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


def block_execute_work(
    monkeypatch,
    api_module,
    runtime: AgentRuntime,
) -> dict[str, int]:
    """Prove rejected requests do not validate events or run the Pipeline."""

    calls = {
        "event_validation": 0,
        "pipeline": 0,
    }

    class ForbiddenStandardEvent:
        @classmethod
        def model_validate(
            cls,
            value,
        ):
            calls[
                "event_validation"
            ] += 1
            raise AssertionError(
                "Rejected execute request reached event validation"
            )

    async def forbidden_pipeline(
        context,
    ):
        calls[
            "pipeline"
        ] += 1
        raise AssertionError(
            "Rejected execute request reached Agent Pipeline"
        )

    monkeypatch.setattr(
        api_module,
        "StandardEvent",
        ForbiddenStandardEvent,
    )
    monkeypatch.setattr(
        runtime.pipeline,
        "execute",
        forbidden_pipeline,
    )

    return calls


class ExplodingAuthenticationProvider(
    BaseAuthenticationProvider
):
    @property
    def name(
        self,
    ) -> str:
        return "api_execute_rbac_exploding"

    def authenticate(
        self,
        credential: str | None,
    ) -> OperatorIdentity:
        raise RuntimeError(
            "Authentication provider failed with credential "
            f"{credential}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {
            "X-Operator-ID": (
                "spoofed-admin"
            ),
        },
        {
            "Authorization": (
                "Bearer unsupported-token"
            ),
        },
        {
            "Authorization": (
                "ApiKey "
                + INVALID_CREDENTIAL
            ),
        },
    ],
    ids=(
        "missing-credential-with-spoofed-identity",
        "unsupported-scheme",
        "invalid-api-key",
    ),
)
async def test_execute_returns_safe_401_before_event_validation_and_pipeline(
    api_environment,
    monkeypatch,
    headers,
):
    (
        app,
        runtime,
        _,
        api_module,
    ) = api_environment
    calls = block_execute_work(
        monkeypatch,
        api_module,
        runtime,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/execute",
            json={},
            headers=headers,
        )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authentication failed"
    }
    assert response.headers[
        "www-authenticate"
    ] == "ApiKey"
    assert INVALID_CREDENTIAL not in (
        response.text
    )
    assert calls == {
        "event_validation": 0,
        "pipeline": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        OperatorRole.VIEWER,
        OperatorRole.APPROVER,
        OperatorRole.EXECUTOR,
        OperatorRole.RECONCILER,
    ],
)
async def test_roles_without_runtime_execute_permission_are_denied_before_work(
    api_environment,
    monkeypatch,
    role,
):
    (
        app,
        runtime,
        security,
        api_module,
    ) = api_environment
    assert isinstance(
        security,
        ApiTestSecurityHarness,
    )
    calls = block_execute_work(
        monkeypatch,
        api_module,
        runtime,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/execute",
            json={},
            headers=security.headers(
                role
            ),
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Authorization denied"
    }
    assert "permission" not in (
        response.text.lower()
    )
    assert (
        security.credential(
            role
        ).api_key
        not in response.text
    )
    assert calls == {
        "event_validation": 0,
        "pipeline": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        OperatorRole.ANALYST,
        OperatorRole.SERVICE,
        OperatorRole.ADMIN,
    ],
)
async def test_runtime_execute_roles_reach_pipeline_once(
    api_environment,
    monkeypatch,
    role,
):
    app, runtime, security, _ = (
        api_environment
    )
    request_id = (
        "execute-rbac-"
        f"{role.value}"
    )
    pipeline_calls: list[str | None] = []

    async def successful_pipeline(
        context,
    ):
        pipeline_calls.append(
            context.request_id
        )
        return []

    monkeypatch.setattr(
        runtime.pipeline,
        "execute",
        successful_pipeline,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/execute",
            json=event_payload(),
            headers=security.headers(
                role,
                request_id=request_id,
            ),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["request_id"] == (
        request_id
    )
    assert body["results"] == []
    assert body["executions"] == []
    assert body["evaluations"] == []
    assert pipeline_calls == [
        request_id
    ]


@pytest.mark.asyncio
async def test_execute_provider_failure_returns_safe_503_before_work(
    api_environment,
    monkeypatch,
):
    (
        app,
        runtime,
        _,
        api_module,
    ) = api_environment
    calls = block_execute_work(
        monkeypatch,
        api_module,
        runtime,
    )
    unavailable_authentication = (
        AuthenticationService(
            AuthenticationProviderRegistry(
                [
                    ExplodingAuthenticationProvider(),
                ]
            )
        )
    )
    unavailable_adapter = ApiSecurityAdapter(
        authentication=(
            unavailable_authentication
        ),
        policy=runtime.security_policy,
    )
    monkeypatch.setattr(
        api_module,
        "api_security",
        unavailable_adapter,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/execute",
            json={},
            headers={
                "Authorization": (
                    "ApiKey "
                    + PROVIDER_FAILURE_CREDENTIAL
                ),
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "Authentication service unavailable"
        )
    }
    assert "provider failed" not in (
        response.text.lower()
    )
    assert PROVIDER_FAILURE_CREDENTIAL not in (
        response.text
    )
    assert calls == {
        "event_validation": 0,
        "pipeline": 0,
    }
