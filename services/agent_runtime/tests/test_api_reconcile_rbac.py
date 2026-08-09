from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from services.agent_runtime.app.action.execution_models import (
    ActionExecutionStatus,
)
from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
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
    "api-reconcile-rbac-invalid-key-00000001"
)


PROVIDER_FAILURE_CREDENTIAL = (
    "api-reconcile-rbac-provider-failure-key-01"
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


def reconciliation_path(
    execution_id,
) -> str:
    return (
        "/action-executions/"
        f"{execution_id}"
        "/reconcile"
    )


def failure_body() -> dict:
    return {
        "outcome": "failed",
        "reason": (
            "Kubernetes audit confirms that the Action did not apply"
        ),
        "result": {
            "success": False,
            "evidence": "kubernetes_audit",
        },
        "error_type": "MutationNotObserved",
        "error_message": (
            "Deployment generation and Pod UID were unchanged"
        ),
        "metadata": {
            "ticket": "INC-RBAC-RECONCILE-1001",
        },
    }


def unauthenticated_headers() -> dict[str, str]:
    return {
        "X-Operator-ID": "claimed-reconciler",
        "Idempotency-Key": (
            "reconcile-rbac-negative-key"
        ),
    }


def reconciliation_headers(
    security: ApiTestSecurityHarness,
    role: OperatorRole,
    *,
    claimed_operator_id: str | None = None,
) -> dict[str, str]:
    headers = security.headers(
        role,
        include_operator_id=True,
        idempotency_key=(
            "reconcile-rbac-"
            f"{role.value}-decision-key"
        ),
    )

    if claimed_operator_id is not None:
        headers[
            "X-Operator-ID"
        ] = claimed_operator_id

    return headers


def block_reconciliation_work(
    monkeypatch,
    runtime: AgentRuntime,
) -> list[str]:
    """Fail if a rejected request reaches persistence or side effects."""

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
                "Rejected Reconcile request reached protected work: "
                f"{label}"
            )

        return call

    targets = (
        (
            runtime.action_execution_service,
            "get",
            "action_execution.get",
        ),
        (
            runtime.action_execution_service,
            "reconcile",
            "action_execution.reconcile",
        ),
        (
            runtime.incident_store,
            "get",
            "incident.get",
        ),
        (
            runtime.incident_store,
            "update",
            "incident.update",
        ),
        (
            runtime.verification_coordinator,
            "run",
            "verification_coordinator.run",
        ),
        (
            runtime.verification,
            "get_by_action_execution",
            "verification.get_by_action_execution",
        ),
        (
            runtime.action_runtime.executor,
            "execute",
            "action_executor.execute",
        ),
    )

    for owner, method_name, label in targets:
        monkeypatch.setattr(
            owner,
            method_name,
            forbidden_call(
                label
            ),
        )

    return calls


async def create_indeterminate_execution(
    runtime: AgentRuntime,
    security: ApiTestSecurityHarness,
):
    incident = IncidentState()
    incident.update(
        IncidentStatus.HEALING,
        reason=(
            "Action outcome requires authenticated reconciliation"
        ),
    )
    incident = await runtime.incident_store.save(
        incident
    )

    plan = ActionPlan(
        type=(
            ActionType.INCREASE_MEMORY_LIMIT
        ),
        target="payment-api",
        namespace="payment",
        cluster="production-tw",
        risk=ActionRisk.MEDIUM,
        approved=False,
        metadata={
            "memory_limit": "1Gi",
        },
    )
    approval = await runtime.approval.create_approval(
        action=plan,
        reason="Operator approval is required",
        incident_id=incident.id,
    )
    approval = await runtime.approval.approve(
        approval.id,
        operator_id=security.principal_id(
            OperatorRole.APPROVER
        ),
        idempotency_key=(
            f"approve:{approval.id}"
        ),
        reason="Approved for Reconcile RBAC test",
    )
    claim = await (
        runtime.action_execution_service.claim(
            approval_id=approval.id,
            incident_id=incident.id,
            operator_id=security.principal_id(
                OperatorRole.EXECUTOR
            ),
            idempotency_key=(
                f"execute:{approval.id}"
            ),
            action=approval.action,
            metadata={
                "source": "reconcile-rbac-test",
            },
        )
    )
    execution = await (
        runtime.action_execution_service
        .mark_indeterminate(
            str(
                claim.execution.id
            ),
            (
                "Executor connection closed before a result "
                "was received"
            ),
        )
    )

    return (
        incident,
        approval,
        execution,
    )


class ExplodingAuthenticationProvider(
    BaseAuthenticationProvider
):
    @property
    def name(
        self,
    ) -> str:
        return "api_reconcile_rbac_exploding"

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
async def test_reconcile_returns_safe_401_before_storage_or_cas(
    api_environment,
    monkeypatch,
    authorization,
):
    app, runtime, _, _ = (
        api_environment
    )
    calls = block_reconciliation_work(
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
        response = await client.post(
            reconciliation_path(
                uuid4()
            ),
            json=failure_body(),
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
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        OperatorRole.VIEWER,
        OperatorRole.ANALYST,
        OperatorRole.APPROVER,
        OperatorRole.EXECUTOR,
        OperatorRole.SERVICE,
    ],
)
async def test_roles_without_reconcile_permission_return_403_before_cas(
    api_environment,
    monkeypatch,
    role,
):
    app, runtime, security, _ = (
        api_environment
    )
    calls = block_reconciliation_work(
        monkeypatch,
        runtime,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            reconciliation_path(
                uuid4()
            ),
            json=failure_body(),
            headers=reconciliation_headers(
                security,
                role,
            ),
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Authorization denied"
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
            OperatorRole.RECONCILER,
            OperatorRole.ADMIN,
        ),
        (
            OperatorRole.ADMIN,
            OperatorRole.RECONCILER,
        ),
    ],
)
async def test_reconcile_identity_claim_cannot_override_principal(
    api_environment,
    monkeypatch,
    authenticated_role,
    claimed_role,
):
    app, runtime, security, _ = (
        api_environment
    )
    calls = block_reconciliation_work(
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
            reconciliation_path(
                uuid4()
            ),
            json=failure_body(),
            headers=reconciliation_headers(
                security,
                authenticated_role,
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
async def test_reconcile_provider_failure_returns_safe_503_before_cas(
    api_environment,
    monkeypatch,
):
    (
        app,
        runtime,
        _,
        api_module,
    ) = api_environment
    calls = block_reconciliation_work(
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
        response = await client.post(
            reconciliation_path(
                uuid4()
            ),
            json=failure_body(),
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
    "missing_header",
    [
        "X-Operator-ID",
        "Idempotency-Key",
    ],
)
async def test_reconcile_requires_bounded_compatibility_headers_without_work(
    api_environment,
    monkeypatch,
    missing_header,
):
    app, runtime, security, _ = (
        api_environment
    )
    calls = block_reconciliation_work(
        monkeypatch,
        runtime,
    )
    headers = reconciliation_headers(
        security,
        OperatorRole.RECONCILER,
    )
    headers.pop(
        missing_header
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            reconciliation_path(
                uuid4()
            ),
            json=failure_body(),
            headers=headers,
        )

    assert response.status_code == 422
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        OperatorRole.RECONCILER,
        OperatorRole.ADMIN,
    ],
)
async def test_authorized_reconcile_persists_authenticated_audit_principal(
    api_environment,
    monkeypatch,
    role,
):
    app, runtime, security, _ = (
        api_environment
    )
    incident, _, execution = (
        await create_indeterminate_execution(
            runtime,
            security,
        )
    )
    side_effect_calls: list[str] = []

    async def forbidden_execute(
        *args,
        **kwargs,
    ):
        side_effect_calls.append(
            "action_executor.execute"
        )
        raise AssertionError(
            "Reconciliation attempted to execute the Action"
        )

    async def forbidden_verification(
        *args,
        **kwargs,
    ):
        side_effect_calls.append(
            "verification_coordinator.run"
        )
        raise AssertionError(
            "Failed reconciliation attempted Verification"
        )

    monkeypatch.setattr(
        runtime.action_runtime.executor,
        "execute",
        forbidden_execute,
    )
    monkeypatch.setattr(
        runtime.verification_coordinator,
        "run",
        forbidden_verification,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            reconciliation_path(
                execution.id
            ),
            json=failure_body(),
            headers=reconciliation_headers(
                security,
                role,
            ),
        )

    assert response.status_code == 200
    body = response.json()
    authenticated_principal = (
        security.principal_id(
            role
        )
    )

    assert body["success"] is True
    assert body["workflow_success"] is False
    assert body["workflow_status"] == (
        "action_failed"
    )
    assert body["execution"]["status"] == (
        "failed"
    )
    assert body["reconciliation"][
        "operator_id"
    ] == authenticated_principal
    assert body["reconciliation"][
        "metadata"
    ]["source"] == "api"
    assert body["reconciliation"][
        "metadata"
    ]["ticket"] == (
        "INC-RBAC-RECONCILE-1001"
    )
    assert body["verification"] is None
    assert body["verification_required"] is False
    assert body["incident"]["id"] == str(
        incident.id
    )
    assert body["incident"]["status"] == (
        "failed"
    )
    assert side_effect_calls == []

    persisted = await (
        runtime.action_execution_service.get(
            str(
                execution.id
            )
        )
    )

    assert persisted is not None
    assert persisted.status == (
        ActionExecutionStatus.FAILED
    )
    assert persisted.reconciliation is not None
    assert persisted.reconciliation.operator_id == (
        authenticated_principal
    )
    assert persisted.reconciliation.metadata[
        "source"
    ] == "api"


@pytest.mark.asyncio
async def test_authorized_reconcile_never_relaunches_action(
    api_environment,
    monkeypatch,
):
    app, runtime, security, _ = (
        api_environment
    )
    _, _, execution = (
        await create_indeterminate_execution(
            runtime,
            security,
        )
    )
    executor_calls = {
        "count": 0
    }

    async def forbidden_execute(
        *args,
        **kwargs,
    ):
        executor_calls["count"] += 1
        raise AssertionError(
            "Manual reconciliation relaunched the Action"
        )

    monkeypatch.setattr(
        runtime.action_runtime.executor,
        "execute",
        forbidden_execute,
    )

    async with api_client(
        app
    ) as client:
        response = await client.post(
            reconciliation_path(
                execution.id
            ),
            json=failure_body(),
            headers=reconciliation_headers(
                security,
                OperatorRole.RECONCILER,
            ),
        )

    assert response.status_code == 200
    assert executor_calls["count"] == 0
