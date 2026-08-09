from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionType,
)
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
    "api-approval-rbac-invalid-key-000000001"
)


PROVIDER_FAILURE_CREDENTIAL = (
    "api-approval-rbac-provider-failure-key-01"
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


def create_plan() -> ActionPlan:
    return ActionPlan(
        type=ActionType.INCREASE_MEMORY_LIMIT,
        target="payment-api",
        namespace="payment",
        cluster="production-a",
    )


def decision_body() -> dict:
    return {
        "reason": "Operator reviewed remediation scope",
        "metadata": {
            "ticket": "INC-RBAC-1001",
            "source": "untrusted-client-value",
        },
    }


def unauthenticated_headers() -> dict[str, str]:
    return {
        "X-Operator-ID": "claimed-approver",
        "Idempotency-Key": (
            "approval-rbac-negative-key"
        ),
    }


def decision_headers(
    security: ApiTestSecurityHarness,
    role: OperatorRole,
    *,
    operation: str,
    claimed_operator_id: str | None = None,
) -> dict[str, str]:
    headers = security.headers(
        role,
        include_operator_id=True,
        idempotency_key=(
            "approval-rbac-"
            f"{role.value}-{operation}-key"
        ),
    )

    if claimed_operator_id is not None:
        headers[
            "X-Operator-ID"
        ] = claimed_operator_id

    return headers


def block_approval_persistence(
    monkeypatch,
    runtime: AgentRuntime,
) -> list[str]:
    """Fail if a rejected request reads or mutates Approval state."""

    calls: list[str] = []

    def forbidden_call(
        label: str,
    ):
        async def call(
            *args,
            **kwargs,
        ):
            calls.append(
                label
            )
            raise AssertionError(
                "Rejected approval request reached persistence: "
                f"{label}"
            )

        return call

    for method_name in (
        "get",
        "approve",
        "reject",
    ):
        monkeypatch.setattr(
            runtime.approval,
            method_name,
            forbidden_call(
                f"approval.{method_name}"
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
        return "api_approval_rbac_exploding"

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
    "authorization",
    [
        None,
        "Bearer unsupported-token",
        "ApiKey " + INVALID_CREDENTIAL,
    ],
    ids=(
        "missing-credential",
        "unsupported-scheme",
        "invalid-api-key",
    ),
)
async def test_approval_routes_return_safe_401_before_persistence(
    api_environment,
    monkeypatch,
    authorization,
):
    app, runtime, _, _ = (
        api_environment
    )
    calls = block_approval_persistence(
        monkeypatch,
        runtime,
    )
    headers = unauthenticated_headers()

    if authorization is not None:
        headers[
            "Authorization"
        ] = authorization

    async with api_client(
        app
    ) as client:
        for operation in (
            "approve",
            "reject",
        ):
            response = await client.post(
                "/approvals/"
                f"{uuid4()}/{operation}",
                json=decision_body(),
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

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        OperatorRole.VIEWER,
        OperatorRole.ANALYST,
        OperatorRole.EXECUTOR,
        OperatorRole.RECONCILER,
        OperatorRole.SERVICE,
    ],
)
async def test_roles_without_approval_decide_permission_return_403_before_write(
    api_environment,
    monkeypatch,
    role,
):
    app, runtime, security, _ = (
        api_environment
    )
    calls = block_approval_persistence(
        monkeypatch,
        runtime,
    )

    async with api_client(
        app
    ) as client:
        for operation in (
            "approve",
            "reject",
        ):
            response = await client.post(
                "/approvals/"
                f"{uuid4()}/{operation}",
                json=decision_body(),
                headers=decision_headers(
                    security,
                    role,
                    operation=operation,
                ),
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

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "authenticated_role",
        "claimed_role",
    ),
    [
        (
            OperatorRole.APPROVER,
            OperatorRole.ADMIN,
        ),
        (
            OperatorRole.ADMIN,
            OperatorRole.APPROVER,
        ),
    ],
)
async def test_x_operator_id_cannot_override_authenticated_principal(
    api_environment,
    monkeypatch,
    authenticated_role,
    claimed_role,
):
    app, runtime, security, _ = (
        api_environment
    )
    calls = block_approval_persistence(
        monkeypatch,
        runtime,
    )
    claimed_operator_id = (
        security.principal_id(
            claimed_role
        )
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/approvals/"
            f"{uuid4()}/approve",
            json=decision_body(),
            headers=decision_headers(
                security,
                authenticated_role,
                operation="identity-mismatch",
                claimed_operator_id=(
                    claimed_operator_id
                ),
            ),
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": (
            "Authenticated operator identity does not match "
            "X-Operator-ID"
        )
    }
    assert claimed_operator_id not in (
        response.text
    )
    assert calls == []


@pytest.mark.asyncio
async def test_approval_provider_failure_returns_safe_503_before_persistence(
    api_environment,
    monkeypatch,
):
    (
        app,
        runtime,
        _,
        api_module,
    ) = api_environment
    calls = block_approval_persistence(
        monkeypatch,
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
    headers = unauthenticated_headers()
    headers[
        "Authorization"
    ] = (
        "ApiKey "
        + PROVIDER_FAILURE_CREDENTIAL
    )

    async with api_client(
        app
    ) as client:
        for operation in (
            "approve",
            "reject",
        ):
            response = await client.post(
                "/approvals/"
                f"{uuid4()}/{operation}",
                json=decision_body(),
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

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "role",
        "operation",
        "expected_status",
    ),
    [
        (
            OperatorRole.APPROVER,
            "approve",
            "approved",
        ),
        (
            OperatorRole.APPROVER,
            "reject",
            "rejected",
        ),
        (
            OperatorRole.ADMIN,
            "approve",
            "approved",
        ),
        (
            OperatorRole.ADMIN,
            "reject",
            "rejected",
        ),
    ],
)
async def test_authorized_decision_persists_authenticated_audit_principal(
    api_environment,
    role,
    operation,
    expected_status,
):
    app, runtime, security, _ = (
        api_environment
    )
    approval = await (
        runtime.approval.create_approval(
            action=create_plan(),
            reason=(
                "Approval RBAC audit test"
            ),
        )
    )
    headers = decision_headers(
        security,
        role,
        operation=operation,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            "/approvals/"
            f"{approval.id}/{operation}",
            json=decision_body(),
            headers=headers,
        )

    assert response.status_code == 200
    response_approval = response.json()[
        "approval"
    ]
    authenticated_principal = (
        security.principal_id(
            role
        )
    )

    assert response_approval[
        "status"
    ] == expected_status
    assert response_approval[
        "decision"
    ]["operator_id"] == (
        authenticated_principal
    )
    assert response_approval[
        "decision"
    ]["metadata"]["source"] == "api"
    assert response_approval[
        "decision"
    ]["metadata"]["ticket"] == (
        "INC-RBAC-1001"
    )

    stored = await runtime.approval.get(
        approval.id
    )

    assert stored is not None
    assert stored.decision is not None
    assert stored.decision.operator_id == (
        authenticated_principal
    )
    assert stored.status.value == (
        expected_status
    )
