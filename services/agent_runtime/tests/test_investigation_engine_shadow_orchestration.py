from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from services.agent_runtime.app.investigation.engine_shadow_gate import (
    INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT,
    InvestigationEngineShadowEvidence,
    InvestigationEngineShadowGate,
    InvestigationEngineShadowSettings,
)
from services.agent_runtime.app.investigation.engine_shadow_orchestration import (
    INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_ACKNOWLEDGEMENT,
    InvestigationEngineShadowCompletionStatus,
    InvestigationEngineShadowOrchestrationConfigurationError,
    InvestigationEngineShadowOrchestrationSettings,
    InvestigationEngineShadowOrchestrator,
    InvestigationEngineShadowSubmissionStatus,
    create_investigation_engine_shadow_orchestrator,
)
from services.agent_runtime.app.investigation.engine_shadow_runner import (
    InvestigationEngineShadowRunner,
    InvestigationEngineShadowRunStatus,
)
from services.agent_runtime.app.investigation.engine_shadow_runtime_factory import (
    create_investigation_engine_shadow_runtime,
    plan_investigation_engine_shadow_runtime,
)
from services.agent_runtime.app.investigation.models import (
    IncidentHypothesis,
    InvestigationDecision,
    InvestigationProbe,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.session_runtime_factory import (
    create_investigation_session_runtime,
)
from services.agent_runtime.app.investigation.session_runtime_settings import (
    INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT,
    InvestigationSessionRuntimeSettings,
)

MATRIX_DIGEST = "1" * 64
RELEASE_DIGEST = "2" * 64


class CountingReasoner(BaseInvestigationReasoner):
    def __init__(self):
        self.calls = 0

    async def decide(self, scope, state):
        self.calls += 1
        return InvestigationDecision(
            hypotheses=[
                IncidentHypothesis(
                    hypothesis_id="memory-pressure",
                    cause="container memory pressure",
                    confidence=0.5,
                    missing_evidence=["pod state"],
                )
            ],
            rationale_summary="collect bounded pod state",
            stop=False,
            next_probe=InvestigationProbe.KUBERNETES_POD_STATE,
        )


class BlockingReasoner(CountingReasoner):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def decide(self, scope, state):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return InvestigationDecision(
            hypotheses=[
                IncidentHypothesis(
                    hypothesis_id="memory-pressure",
                    cause="container memory pressure",
                    confidence=0.5,
                    missing_evidence=["pod state"],
                )
            ],
            rationale_summary="collect bounded pod state",
            stop=False,
            next_probe=InvestigationProbe.KUBERNETES_POD_STATE,
        )


class NoProbeExecutor:
    @staticmethod
    def available_probes(context):
        return [InvestigationProbe.KUBERNETES_POD_STATE]

    async def collect(self, context, scope, probe):
        raise AssertionError("one reasoner step must not call a Probe")


class FakeTools:
    async def call(self, name, **kwargs):
        raise AssertionError("one reasoner step must not call a Tool")


def orchestration_settings(**changes):
    values = {
        "enabled": True,
        "acknowledgement": (
            INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_ACKNOWLEDGEMENT
        ),
        "timeout_seconds": 1.0,
        "max_pending_tasks": 1,
        "completed_result_limit": 8,
    }
    values.update(changes)
    return InvestigationEngineShadowOrchestrationSettings(**values)


def build_orchestrator(tmp_path, *, reasoner=None, **orchestration_changes):
    now = datetime.now(UTC)
    reasoner = reasoner or CountingReasoner()
    probes = NoProbeExecutor()
    tools = FakeTools()
    primary_settings = InvestigationSessionRuntimeSettings(
        enabled=True,
        acknowledgement=INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT,
        db_path=str(tmp_path / "primary.db"),
    )
    shadow_settings = InvestigationEngineShadowSettings(
        enabled=True,
        acknowledgement=INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT,
        kill_switch_engaged=False,
        shadow_db_path=str(tmp_path / "shadow.db"),
        sample_rate=0.05,
        max_concurrent_sessions=1,
        expected_matrix_digest=MATRIX_DIGEST,
        expected_release_digest=RELEASE_DIGEST,
    )
    primary = create_investigation_session_runtime(
        settings=primary_settings,
        reasoner=reasoner,
        probe_executor=probes,
    )
    plan = plan_investigation_engine_shadow_runtime(
        settings=shadow_settings,
        evidence=InvestigationEngineShadowEvidence(
            matrix_digest=MATRIX_DIGEST,
            release_digest=RELEASE_DIGEST,
            generated_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=30),
        ),
        primary_settings=primary_settings,
        now=now,
    )
    runtime = create_investigation_engine_shadow_runtime(
        plan=plan,
        primary_components=primary,
        reasoner=reasoner,
        probe_executor=probes,
    )
    assert runtime is not None
    runner = InvestigationEngineShadowRunner(
        runtime=runtime,
        settings=shadow_settings,
        tools=tools,
        utc_clock=lambda: now,
    )
    orchestrator = InvestigationEngineShadowOrchestrator(
        runner=runner,
        settings=orchestration_settings(**orchestration_changes),
    )
    return orchestrator, reasoner, tools


def selected_incident(orchestrator):
    run_key = orchestrator._run_key()
    for value in range(1, 10000):
        incident_id = UUID(int=value)
        if InvestigationEngineShadowGate.selected_for_shadow(
            decision=orchestrator.runner.runtime.decision,
            incident_id=incident_id,
            run_key=run_key,
        ):
            return incident_id
    raise AssertionError("selected deterministic sample was not found")


def context(incident_id, tools):
    event = SimpleNamespace(
        header=SimpleNamespace(occurred_at=datetime.now(UTC)),
        signal=SimpleNamespace(
            name="PodOOMKilled",
            message="Container was OOMKilled",
        ),
        resources=[
            SimpleNamespace(
                name="payment-api-abc",
                namespace="payment",
                cluster="prod-a",
            )
        ],
    )
    return SimpleNamespace(
        request_id="primary-request",
        event=event,
        incident=SimpleNamespace(id=incident_id),
        tools=tools,
        metadata={"primary": "unchanged"},
        results={"authoritative": True},
    )


def test_settings_are_disabled_by_default_and_require_exact_acknowledgement():
    assert InvestigationEngineShadowOrchestrationSettings().enabled is False
    with pytest.raises(ValueError, match="exact acknowledgement"):
        InvestigationEngineShadowOrchestrationSettings(
            enabled=True,
            acknowledgement="wrong",
        )


def test_environment_errors_are_sanitized():
    with pytest.raises(
        InvestigationEngineShadowOrchestrationConfigurationError,
        match="configuration is invalid",
    ) as captured:
        InvestigationEngineShadowOrchestrationSettings.from_environment(
            {
                "AGENT_INVESTIGATION_LANGGRAPH_SHADOW_ORCHESTRATION_ENABLED": (
                    "secret-invalid-value"
                )
            }
        )
    assert "secret-invalid-value" not in str(captured.value)


def test_disabled_factory_does_not_inspect_runner():
    assert (
        create_investigation_engine_shadow_orchestrator(
            settings=InvestigationEngineShadowOrchestrationSettings(),
            runner=object(),
        )
        is None
    )


@pytest.mark.asyncio
async def test_submit_is_detached_and_primary_context_is_unchanged(tmp_path):
    reasoner = BlockingReasoner()
    orchestrator, _, tools = build_orchestrator(
        tmp_path,
        reasoner=reasoner,
    )
    primary = context(selected_incident(orchestrator), tools)
    event_before = primary.event

    submission = orchestrator.submit(primary)

    assert submission.status == InvestigationEngineShadowSubmissionStatus.SUBMITTED
    assert submission.accepted is True
    assert reasoner.calls == 0
    assert primary.event is event_before
    assert primary.metadata == {"primary": "unchanged"}
    assert primary.results == {"authoritative": True}

    await asyncio.wait_for(reasoner.entered.wait(), timeout=1.0)
    reasoner.release.set()
    await orchestrator.drain()

    completed = orchestrator.completed_results[-1]
    assert completed.status == InvestigationEngineShadowCompletionStatus.COMPLETED
    assert completed.runner_result.status == InvestigationEngineShadowRunStatus.EXECUTED
    assert completed.primary_result_influence is False
    assert primary.metadata == {"primary": "unchanged"}
    assert primary.results == {"authoritative": True}


@pytest.mark.asyncio
async def test_pending_capacity_rejects_without_starting_second_task(tmp_path):
    reasoner = BlockingReasoner()
    orchestrator, _, tools = build_orchestrator(
        tmp_path,
        reasoner=reasoner,
        max_pending_tasks=1,
    )
    primary = context(selected_incident(orchestrator), tools)

    first = orchestrator.submit(primary)
    second = orchestrator.submit(primary)

    assert first.status == InvestigationEngineShadowSubmissionStatus.SUBMITTED
    assert second.status == InvestigationEngineShadowSubmissionStatus.CAPACITY_LIMIT
    assert second.accepted is False
    await asyncio.wait_for(reasoner.entered.wait(), timeout=1.0)
    assert reasoner.calls == 1
    reasoner.release.set()
    await orchestrator.drain()


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_exact_replay_does_not_call_reasoner_again(
    tmp_path,
):
    reasoner = BlockingReasoner()
    orchestrator, _, tools = build_orchestrator(
        tmp_path,
        reasoner=reasoner,
        timeout_seconds=0.1,
    )
    primary = context(selected_incident(orchestrator), tools)

    assert orchestrator.submit(primary).accepted is True
    await orchestrator.drain()

    assert orchestrator.completed_results[-1].status == (
        InvestigationEngineShadowCompletionStatus.TIMED_OUT
    )
    assert reasoner.calls == 1
    assert orchestrator.runner.active_invocations == 0

    assert orchestrator.submit(primary).accepted is True
    await orchestrator.drain()

    replay = orchestrator.completed_results[-1]
    assert replay.status == InvestigationEngineShadowCompletionStatus.COMPLETED
    assert replay.runner_result.status == InvestigationEngineShadowRunStatus.REPLAYED
    assert reasoner.calls == 1


@pytest.mark.asyncio
async def test_runner_exception_is_sanitized_and_never_escapes(tmp_path, monkeypatch):
    orchestrator, _, tools = build_orchestrator(tmp_path)
    primary = context(selected_incident(orchestrator), tools)
    secret = "https://user:secret@example.invalid"

    async def explode(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(orchestrator.runner, "run_once", explode)

    assert orchestrator.submit(primary).accepted is True
    await orchestrator.drain()

    completed = orchestrator.completed_results[-1]
    assert completed.status == InvestigationEngineShadowCompletionStatus.FAILED
    assert completed.failure_code == "RuntimeError"
    assert secret not in completed.model_dump_json()
    assert primary.metadata == {"primary": "unchanged"}


@pytest.mark.asyncio
async def test_controlled_shutdown_consumes_cancelled_task(tmp_path):
    reasoner = BlockingReasoner()
    orchestrator, _, tools = build_orchestrator(
        tmp_path,
        reasoner=reasoner,
    )
    primary = context(selected_incident(orchestrator), tools)

    assert orchestrator.submit(primary).accepted is True
    await asyncio.wait_for(reasoner.entered.wait(), timeout=1.0)
    await orchestrator.cancel_pending()

    assert orchestrator.pending_count == 0
    assert orchestrator.completed_results[-1].status == (
        InvestigationEngineShadowCompletionStatus.CANCELLED
    )

