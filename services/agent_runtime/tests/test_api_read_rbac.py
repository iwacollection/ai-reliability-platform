from collections.abc import Callable
from uuid import uuid4

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
    "api-read-rbac-invalid-key-0000000001"
)


PROVIDER_FAILURE_CREDENTIAL = (
    "api-read-rbac-provider-failure-key-0001"
)


@pytest.fixture
def api_environment(
    monkeypatch,
    tmp_path,
):
    """Create one authenticated API with test-local SQLite stores."""

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


def read_paths() -> tuple[str, ...]:
    resource_id = str(
        uuid4()
    )

    return (
        f"/incidents/{resource_id}",
        f"/incidents/{resource_id}/workflows",
        f"/approvals/{resource_id}",
        f"/workflows/approvals/{resource_id}",
        f"/verifications/{resource_id}",
    )


def block_persistence_reads(
    monkeypatch,
    runtime: AgentRuntime,
) -> list[str]:
    """Fail immediately if rejected requests reach a workflow store."""

    calls: list[str] = []
    targets = (
        (
            runtime.incident_store,
            "get",
            "incident.get",
        ),
        (
            runtime.approval,
            "get",
            "approval.get",
        ),
        (
            runtime.approval,
            "list_by_incident",
            "approval.list_by_incident",
        ),
        (
            runtime.action_execution_service,
            "get_by_approval",
            "action_execution.get_by_approval",
        ),
        (
            runtime.action_execution_service,
            "list_by_incident",
            "action_execution.list_by_incident",
        ),
        (
            runtime.verification,
            "get",
            "verification.get",
        ),
        (
            runtime.verification,
            "get_by_action_execution",
            "verification.get_by_action_execution",
        ),
        (
            runtime.verification,
            "list_by_incident",
            "verification.list_by_incident",
        ),
    )

    def forbidden_read(
        label: str,
    ) -> Callable:
        async def call(
            *args,
            **kwargs,
        ):
            calls.append(
                label
            )
            raise AssertionError(
                "Rejected API request reached persistence: "
                f"{label}"
            )

        return call

    for owner, method_name, label in targets:
        monkeypatch.setattr(
            owner,
            method_name,
            forbidden_read(
                label
            ),
        )

    return calls


class ExplodingAuthenticationProvider(
    BaseAuthenticationProvider
):
    @property
    def name(
        self,
    ) -> str:
        return "api_read_rbac_exploding"

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
async def test_read_routes_fail_with_safe_401_before_persistence(
    api_environment,
    monkeypatch,
    headers,
):
    app, runtime, _, _ = (
        api_environment
    )
    persistence_calls = (
        block_persistence_reads(
            monkeypatch,
            runtime,
        )
    )

    async with api_client(
        app
    ) as client:
        for path in read_paths():
            response = await client.get(
                path,
                headers=headers,
            )

            assert response.status_code == 401
            assert response.json() == {
                "detail": (
                    "Authentication failed"
                )
            }
            assert response.headers[
                "www-authenticate"
            ] == "ApiKey"
            assert INVALID_CREDENTIAL not in (
                response.text
            )

    assert persistence_calls == []


@pytest.mark.asyncio
async def test_service_role_is_denied_all_read_routes_before_persistence(
    api_environment,
    monkeypatch,
):
    app, runtime, security, _ = (
        api_environment
    )
    assert isinstance(
        security,
        ApiTestSecurityHarness,
    )
    persistence_calls = (
        block_persistence_reads(
            monkeypatch,
            runtime,
        )
    )
    headers = security.headers(
        OperatorRole.SERVICE
    )

    async with api_client(
        app
    ) as client:
        for path in read_paths():
            response = await client.get(
                path,
                headers=headers,
            )

            assert response.status_code == 403
            assert response.json() == {
                "detail": (
                    "Authorization denied"
                )
            }
            assert "permission" not in (
                response.text.lower()
            )
            assert (
                security.credential(
                    OperatorRole.SERVICE
                ).api_key
                not in response.text
            )

    assert persistence_calls == []


@pytest.mark.asyncio
async def test_authentication_provider_failure_returns_safe_503_before_persistence(
    api_environment,
    monkeypatch,
):
    (
        app,
        runtime,
        _,
        api_module,
    ) = api_environment
    persistence_calls = (
        block_persistence_reads(
            monkeypatch,
            runtime,
        )
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
    headers = {
        "Authorization": (
            "ApiKey "
            + PROVIDER_FAILURE_CREDENTIAL
        ),
    }

    async with api_client(
        app
    ) as client:
        for path in read_paths():
            response = await client.get(
                path,
                headers=headers,
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

    assert persistence_calls == []
