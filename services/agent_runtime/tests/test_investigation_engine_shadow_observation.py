from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from services.agent_runtime.app.investigation.engine_shadow_gate import (
    InvestigationEngineShadowGateCode,
    InvestigationEngineShadowGateDecision,
)
from services.agent_runtime.app.investigation.engine_shadow_observation import (
    InvestigationEngineShadowComparisonStatus,
    InvestigationEngineShadowObservationService,
    InvestigationEngineShadowObservationUnavailableError,
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

INCIDENT_ID = UUID("00000000-0000-4000-8000-000000000831")
NOW = datetime(2026, 8, 16, 15, 0, tzinfo=UTC)


def state() -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            alert_message="Container was OOMKilled",
            event_occurred_at=NOW,
            resource="payment-api-abc",
            namespace="payment",
            cluster="prod-a",
        ),
        available_probes=[
            InvestigationProbe.KUBERNETES_POD_STATE,
        ],
        started_at=NOW,
        updated_at=NOW,
    )


def service(path) -> InvestigationSessionService:
    return InvestigationSessionService(
        InvestigationSessionStore(path)
    )


def allowed_decision() -> InvestigationEngineShadowGateDecision:
    return InvestigationEngineShadowGateDecision(
        allowed=True,
        code=InvestigationEngineShadowGateCode.ALLOWED,
        sample_rate=0.05,
        max_concurrent_sessions=1,
        matrix_digest="a" * 64,
        release_digest="b" * 64,
    )


def disabled_decision() -> InvestigationEngineShadowGateDecision:
    return InvestigationEngineShadowGateDecision(
        allowed=False,
        code=InvestigationEngineShadowGateCode.DISABLED,
        sample_rate=0.0,
        max_concurrent_sessions=0,
    )


async def create_sessions(
    session_service,
    *,
    count,
    prefix,
    start=NOW,
):
    records = []
    for index in range(count):
        result = await session_service.create_or_get(
            incident_id=INCIDENT_ID,
            run_key=f"{prefix}-{index:02d}",
            initial_state=state(),
            created_by=prefix,
            now=start + timedelta(seconds=index),
        )
        records.append(result.session)
    return records


@pytest.mark.asyncio
async def test_recent_store_query_is_bounded_and_chronological(tmp_path):
    session_service = service(tmp_path / "recent.db")
    await create_sessions(
        session_service,
        count=25,
        prefix="primary",
    )

    recent = await session_service.list_recent_by_incident(
        INCIDENT_ID,
        limit=5,
    )

    assert [session.run_key for session in recent] == [
        "primary-20",
        "primary-21",
        "primary-22",
        "primary-23",
        "primary-24",
    ]
    with pytest.raises(ValueError, match="query limit"):
        await session_service.list_recent_by_incident(
            INCIDENT_ID,
            limit=0,
        )


@pytest.mark.asyncio
async def test_disabled_observation_has_zero_store_and_bounded_no_go():
    observation = InvestigationEngineShadowObservationService(
        primary_service=None,
        shadow_service=None,
        decision=disabled_decision(),
        orchestration_settings=(
            InvestigationEngineShadowOrchestrationSettings()
        ),
        orchestrator=None,
        utc_clock=lambda: NOW,
    )

    snapshot = await observation.observe(INCIDENT_ID)

    assert snapshot.gate.code == InvestigationEngineShadowGateCode.DISABLED
    assert snapshot.gate.allowed is False
    assert snapshot.primary_runtime_available is False
    assert snapshot.shadow_runtime_available is False
    assert snapshot.primary_sessions == ()
    assert snapshot.shadow_sessions == ()
    assert snapshot.comparison.status == (
        InvestigationEngineShadowComparisonStatus.NO_SHADOW_SESSION
    )


@pytest.mark.asyncio
async def test_matching_input_comparison_is_lifecycle_only_and_sanitized(tmp_path):
    primary = service(tmp_path / "primary.db")
    shadow = service(tmp_path / "shadow.db")
    await primary.create_or_get(
        incident_id=INCIDENT_ID,
        run_key="primary-secret-run-key",
        initial_state=state(),
        created_by="primary-operator",
        now=NOW,
    )
    await shadow.create_or_get(
        incident_id=INCIDENT_ID,
        run_key="shadow-secret-run-key",
        initial_state=state(),
        created_by="langgraph-shadow-runtime-v1",
        now=NOW,
    )
    observation = InvestigationEngineShadowObservationService(
        primary_service=primary,
        shadow_service=shadow,
        decision=allowed_decision(),
        orchestration_settings=(
            InvestigationEngineShadowOrchestrationSettings()
        ),
        orchestrator=None,
        utc_clock=lambda: NOW,
    )

    snapshot = await observation.observe(INCIDENT_ID)

    assert snapshot.comparison.status == (
        InvestigationEngineShadowComparisonStatus.MATCHING_INPUT_AVAILABLE
    )
    assert snapshot.comparison.immutable_input_match is True
    assert snapshot.comparison.lifecycle_status_equal is True
    assert snapshot.comparison.step_count_equal is True
    assert snapshot.comparison.semantic_equivalence_evaluated is False
    assert snapshot.comparison.decision_influence is False
    serialized = snapshot.model_dump_json().lower()
    for forbidden in (
        "run_key",
        "input_digest",
        "claimant",
        "request_digest",
        "primary-secret-run-key",
        "shadow-secret-run-key",
        "container was oomkilled",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_observation_returns_only_twenty_recent_sessions(tmp_path):
    primary = service(tmp_path / "primary.db")
    shadow = service(tmp_path / "shadow.db")
    created = await create_sessions(
        shadow,
        count=25,
        prefix="shadow",
    )
    observation = InvestigationEngineShadowObservationService(
        primary_service=primary,
        shadow_service=shadow,
        decision=allowed_decision(),
        orchestration_settings=(
            InvestigationEngineShadowOrchestrationSettings()
        ),
        orchestrator=None,
        utc_clock=lambda: NOW + timedelta(minutes=1),
    )

    snapshot = await observation.observe(INCIDENT_ID)

    assert snapshot.shadow_sessions_truncated is True
    assert len(snapshot.shadow_sessions) == 20
    assert snapshot.shadow_sessions[0].session_id == created[5].session_id
    assert snapshot.shadow_sessions[-1].session_id == created[-1].session_id
    assert snapshot.comparison.status == (
        InvestigationEngineShadowComparisonStatus.NO_MATCHING_PRIMARY_IN_WINDOW
    )


@pytest.mark.asyncio
async def test_store_failure_is_sanitized_and_does_not_mutate_sessions(
    tmp_path,
    monkeypatch,
):
    primary = service(tmp_path / "primary.db")
    shadow = service(tmp_path / "shadow.db")
    original = await create_sessions(
        shadow,
        count=1,
        prefix="shadow",
    )
    secret = "sqlite:///secret-shadow-location?credential=leak"

    async def explode(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        shadow,
        "list_recent_by_incident",
        explode,
    )
    observation = InvestigationEngineShadowObservationService(
        primary_service=primary,
        shadow_service=shadow,
        decision=allowed_decision(),
        orchestration_settings=(
            InvestigationEngineShadowOrchestrationSettings()
        ),
        orchestrator=None,
        utc_clock=lambda: NOW,
    )

    with pytest.raises(
        InvestigationEngineShadowObservationUnavailableError,
        match="storage is unavailable",
    ) as captured:
        await observation.observe(INCIDENT_ID)

    assert secret not in str(captured.value)
    persisted = await shadow.store.list_by_incident(INCIDENT_ID)
    assert persisted == original
