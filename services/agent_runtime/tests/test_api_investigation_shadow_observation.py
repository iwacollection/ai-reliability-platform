from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from services.agent_runtime.app.incident.state import IncidentState
from services.agent_runtime.app.security.models import OperatorRole

from services.agent_runtime.app.investigation.engine_shadow_gate import (
    InvestigationEngineShadowGateCode,
    InvestigationEngineShadowGateDecision,
)
from services.agent_runtime.app.investigation.engine_shadow_observation import (
    InvestigationEngineShadowObservationService,
)
from services.agent_runtime.app.investigation.engine_shadow_orchestration import (
    InvestigationEngineShadowOrchestrationSettings,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)
from services.agent_runtime.app.runtime.runtime import AgentRuntime
from services.agent_runtime.tests.api_security_support import (
    wire_api_test_security,
)

INCIDENT_ID = UUID("00000000-0000-4000-8000-000000000841")
NOW = datetime(2026, 8, 16, 16, 0, tzinfo=UTC)


def initial_state() -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            alert_message="sensitive primary alert text",
            event_occurred_at=NOW,
            resource="payment-api-abc",
            namespace="payment",
            cluster="prod-a",
        ),
        available_probes=[InvestigationProbe.KUBERNETES_POD_STATE],
        started_at=NOW,
        updated_at=NOW,
    )


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


@pytest_asyncio.fixture
async def api_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in (
        "PROMETHEUS_URL",
        "KUBERNETES_API_URL",
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "KUBERNETES_SERVICE_PORT_HTTPS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROMETHEUS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setenv("KUBERNETES_ALLOW_DRY_RUN_FALLBACK", "true")

    from services.agent_runtime.app.api import runtime as api_module

    isolated_runtime = AgentRuntime()
    primary = InvestigationSessionService(
        InvestigationSessionStore(tmp_path / "primary.db")
    )
    shadow = InvestigationSessionService(
        InvestigationSessionStore(tmp_path / "shadow.db")
    )
    decision = InvestigationEngineShadowGateDecision(
        allowed=True,
        code=InvestigationEngineShadowGateCode.ALLOWED,
        sample_rate=0.05,
        max_concurrent_sessions=1,
        matrix_digest="a" * 64,
        release_digest="b" * 64,
    )
    isolated_runtime.investigation_engine_shadow_observation = (
        InvestigationEngineShadowObservationService(
            primary_service=primary,
            shadow_service=shadow,
            decision=decision,
            orchestration_settings=(
                InvestigationEngineShadowOrchestrationSettings()
            ),
            orchestrator=None,
            utc_clock=lambda: NOW,
        )
    )
    security = wire_api_test_security(
        monkeypatch,
        api_module,
        isolated_runtime,
    )
    await isolated_runtime.incident_store.save(
        IncidentState(id=INCIDENT_ID)
    )
    await primary.create_or_get(
        incident_id=INCIDENT_ID,
        run_key="primary-api-secret-key",
        initial_state=initial_state(),
        created_by="primary-operator",
        now=NOW,
    )
    await shadow.create_or_get(
        incident_id=INCIDENT_ID,
        run_key="shadow-api-secret-key",
        initial_state=initial_state(),
        created_by="langgraph-shadow-runtime-v1",
        now=NOW,
    )

    app = FastAPI()
    app.include_router(api_module.router)
    return app, isolated_runtime, security, primary, shadow


@pytest.mark.asyncio
async def test_authenticated_observation_is_bounded_sanitized_and_read_only(
    api_environment,
    monkeypatch,
):
    app, runtime, security, primary, shadow = api_environment
    incident_before = await runtime.incident_store.get(INCIDENT_ID)
    primary_before = await primary.list_by_incident(INCIDENT_ID)
    shadow_before = await shadow.list_by_incident(INCIDENT_ID)

    async def forbidden(*args, **kwargs):
        raise AssertionError("read-only Shadow query reached workflow execution")

    monkeypatch.setattr(runtime.action_runtime, "resume", forbidden)
    monkeypatch.setattr(runtime.verification_coordinator, "run", forbidden)

    async with client(app) as api_client:
        response = await api_client.get(
            f"/incidents/{INCIDENT_ID}/investigation-shadow",
            headers=security.headers(OperatorRole.VIEWER),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    observation = payload["observation"]
    assert observation["primary_result_influence"] is False
    assert observation["cross_store_atomic"] is False
    assert observation["comparison"]["status"] == "matching_input_available"
    assert observation["comparison"]["semantic_equivalence_evaluated"] is False
    assert observation["gate"]["allowed"] is True
    assert len(observation["primary_sessions"]) == 1
    assert len(observation["shadow_sessions"]) == 1

    serialized = response.text.lower()
    for forbidden_value in (
        "run_key",
        "input_digest",
        "claimant",
        "request_digest",
        "primary-api-secret-key",
        "shadow-api-secret-key",
        "sensitive primary alert text",
    ):
        assert forbidden_value not in serialized

    assert await runtime.incident_store.get(INCIDENT_ID) == incident_before
    assert await primary.list_by_incident(INCIDENT_ID) == primary_before
    assert await shadow.list_by_incident(INCIDENT_ID) == shadow_before


@pytest.mark.asyncio
async def test_observation_authentication_failure_precedes_incident_read(
    api_environment,
    monkeypatch,
):
    app, runtime, _, _, _ = api_environment

    async def forbidden(*args, **kwargs):
        raise AssertionError("unauthenticated query reached Incident storage")

    monkeypatch.setattr(runtime.incident_store, "get", forbidden)

    async with client(app) as api_client:
        response = await api_client.get(
            f"/incidents/{INCIDENT_ID}/investigation-shadow"
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication failed"}


@pytest.mark.asyncio
async def test_observation_store_failure_is_sanitized_503(
    api_environment,
    monkeypatch,
):
    app, _, security, _, shadow = api_environment
    secret = "sqlite:///secret/path?api_key=must-not-leak"

    async def explode(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(shadow, "list_recent_by_incident", explode)

    async with client(app) as api_client:
        response = await api_client.get(
            f"/incidents/{INCIDENT_ID}/investigation-shadow",
            headers=security.headers(OperatorRole.VIEWER),
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Investigation Shadow observation is unavailable"
    }
    assert secret not in response.text
    assert "api_key" not in response.text.lower()

