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

MATRIX_DIGEST = "e" * 64
RELEASE_DIGEST = "f" * 64


class CountingReasoner(BaseInvestigationReasoner):
    def __init__(self, *, error=None):
        self.calls = 0
        self.error = error

    async def decide(self, scope, state):
        self.calls += 1
        if self.error is not None:
            raise self.error
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


class CountingProbeExecutor:
    def __init__(self):
        self.calls = 0

    @staticmethod
    def available_probes(context):
        return [InvestigationProbe.KUBERNETES_POD_STATE]

    async def collect(self, context, scope, probe):
        self.calls += 1
        raise AssertionError("one-step Shadow invocation called a Probe")


class FakeTools:
    def __init__(self):
        self.calls = 0

    async def call(self, name, **kwargs):
        self.calls += 1
        raise AssertionError("one-step Shadow invocation called a Tool")


def _settings(tmp_path, *, max_concurrent_sessions=1):
    return InvestigationEngineShadowSettings(
        enabled=True,
        acknowledgement=(INVESTIGATION_LANGGRAPH_SHADOW_ACKNOWLEDGEMENT),
        kill_switch_engaged=False,
        shadow_db_path=str(tmp_path / "shadow.db"),
        sample_rate=0.05,
        max_concurrent_sessions=max_concurrent_sessions,
        expected_matrix_digest=MATRIX_DIGEST,
        expected_release_digest=RELEASE_DIGEST,
    )


def _primary_settings(tmp_path):
    return InvestigationSessionRuntimeSettings(
        enabled=True,
        acknowledgement=(INVESTIGATION_SESSION_RUNTIME_ACKNOWLEDGEMENT),
        db_path=str(tmp_path / "primary.db"),
    )


def _evidence(now):
    return InvestigationEngineShadowEvidence(
        matrix_digest=MATRIX_DIGEST,
        release_digest=RELEASE_DIGEST,
        generated_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=30),
    )


def _runner(tmp_path, *, reasoner=None, max_concurrent_sessions=1):
    now = datetime.now(UTC)
    settings = _settings(
        tmp_path,
        max_concurrent_sessions=max_concurrent_sessions,
    )
    primary_settings = _primary_settings(tmp_path)
    reasoner = reasoner or CountingReasoner()
    probes = CountingProbeExecutor()
    tools = FakeTools()
    primary = create_investigation_session_runtime(
        settings=primary_settings,
        reasoner=reasoner,
        probe_executor=probes,
    )
    plan = plan_investigation_engine_shadow_runtime(
        settings=settings,
        evidence=_evidence(now),
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
        settings=settings,
        tools=tools,
        utc_clock=lambda: now,
    )
    return runner, reasoner, probes, tools


def _selected_incident(decision, run_key, *, selected):
    for value in range(1, 10000):
        incident_id = UUID(int=value)
        outcome = InvestigationEngineShadowGate.selected_for_shadow(
            decision=decision,
            incident_id=incident_id,
            run_key=run_key,
        )
        if outcome is selected:
            return incident_id
    raise AssertionError("deterministic Shadow sample was not found")


def _context(incident_id, tools, *, resource="payment-api-abc"):
    event = SimpleNamespace(
        header=SimpleNamespace(occurred_at=datetime.now(UTC)),
        signal=SimpleNamespace(
            name="PodOOMKilled",
            message="Container was OOMKilled",
        ),
        resources=[
            SimpleNamespace(
                name=resource,
                namespace="payment",
                cluster="prod-a",
            )
        ],
    )
    return SimpleNamespace(
        request_id="request-primary",
        event=event,
        incident=SimpleNamespace(id=incident_id),
        tools=tools,
        metadata={"primary": "unchanged"},
        results={"rca": {"authoritative": True}},
    )


@pytest.mark.asyncio
async def test_not_selected_is_zero_session_and_zero_external_call(tmp_path):
    runner, reasoner, probes, tools = _runner(tmp_path)
    run_key = "shadow-run-v1"
    incident_id = _selected_incident(
        runner.runtime.decision,
        run_key,
        selected=False,
    )
    context = _context(incident_id, tools)

    result = await runner.run_once(context, run_key=run_key)

    assert result.status == InvestigationEngineShadowRunStatus.NOT_SELECTED
    assert result.selected is False
    assert result.external_calls_made == 0
    assert reasoner.calls == 0
    assert probes.calls == 0
    assert tools.calls == 0
    assert await runner.runtime.service.list_by_incident(incident_id) == []
    assert context.metadata == {"primary": "unchanged"}
    assert context.results == {"rca": {"authoritative": True}}


@pytest.mark.asyncio
async def test_selected_invocation_executes_exactly_one_isolated_step(tmp_path):
    runner, reasoner, probes, tools = _runner(tmp_path)
    run_key = "shadow-run-v1"
    incident_id = _selected_incident(
        runner.runtime.decision,
        run_key,
        selected=True,
    )
    context = _context(incident_id, tools)

    result = await runner.run_once(context, run_key=run_key)

    assert result.status == InvestigationEngineShadowRunStatus.EXECUTED
    assert result.selected is True
    assert result.external_calls_made == 1
    assert result.primary_result_influence is False
    assert result.read_only is True
    assert result.max_external_steps == 1
    assert reasoner.calls == 1
    assert probes.calls == 0
    assert tools.calls == 0
    assert context.metadata == {"primary": "unchanged"}
    assert context.results == {"rca": {"authoritative": True}}
    assert "Container was OOMKilled" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_exact_replay_never_advances_the_second_step(tmp_path):
    runner, reasoner, probes, tools = _runner(tmp_path)
    run_key = "shadow-replay-v1"
    incident_id = _selected_incident(
        runner.runtime.decision,
        run_key,
        selected=True,
    )
    context = _context(incident_id, tools)

    first = await runner.run_once(context, run_key=run_key)
    replay = await runner.run_once(context, run_key=run_key)

    assert first.status == InvestigationEngineShadowRunStatus.EXECUTED
    assert replay.status == InvestigationEngineShadowRunStatus.REPLAYED
    assert replay.session_id == first.session_id
    assert replay.external_calls_made == 0
    assert reasoner.calls == 1
    assert probes.calls == 0
    assert tools.calls == 0
    persisted = await runner.runtime.service.require(first.session_id)
    assert len(persisted.steps) == 1


@pytest.mark.asyncio
async def test_process_concurrency_limit_rejects_without_session_or_call(
    tmp_path,
):
    reasoner = BlockingReasoner()
    runner, _, probes, tools = _runner(
        tmp_path,
        reasoner=reasoner,
        max_concurrent_sessions=1,
    )
    first_key = "shadow-concurrency-a"
    second_key = "shadow-concurrency-b"
    first_id = _selected_incident(
        runner.runtime.decision,
        first_key,
        selected=True,
    )
    second_id = _selected_incident(
        runner.runtime.decision,
        second_key,
        selected=True,
    )
    first_context = _context(first_id, tools)
    second_context = _context(second_id, tools)

    first_task = asyncio.create_task(runner.run_once(first_context, run_key=first_key))
    await reasoner.entered.wait()
    second = await runner.run_once(second_context, run_key=second_key)
    reasoner.release.set()
    first = await first_task

    assert first.status == InvestigationEngineShadowRunStatus.EXECUTED
    assert second.status == InvestigationEngineShadowRunStatus.CONCURRENCY_LIMIT
    assert second.external_calls_made == 0
    assert reasoner.calls == 1
    assert probes.calls == 0
    assert tools.calls == 0
    assert await runner.runtime.service.list_by_incident(second_id) == []
    assert runner.active_invocations == 0


@pytest.mark.asyncio
async def test_concurrent_exact_replay_grants_one_external_call(tmp_path):
    reasoner = BlockingReasoner()
    runner, _, probes, tools = _runner(
        tmp_path,
        reasoner=reasoner,
        max_concurrent_sessions=2,
    )
    run_key = "shadow-concurrent-replay"
    incident_id = _selected_incident(
        runner.runtime.decision,
        run_key,
        selected=True,
    )
    context = _context(incident_id, tools)

    first_task = asyncio.create_task(runner.run_once(context, run_key=run_key))
    await reasoner.entered.wait()
    replay = await runner.run_once(context, run_key=run_key)
    reasoner.release.set()
    first = await first_task

    assert first.status == InvestigationEngineShadowRunStatus.EXECUTED
    assert replay.status == InvestigationEngineShadowRunStatus.REPLAYED
    assert replay.external_calls_made == 0
    assert reasoner.calls == 1
    assert probes.calls == 0
    assert tools.calls == 0
    assert runner.active_invocations == 0


@pytest.mark.asyncio
async def test_conflicting_replay_is_sanitized_and_never_calls_again(tmp_path):
    runner, reasoner, probes, tools = _runner(tmp_path)
    run_key = "shadow-conflict-v1"
    incident_id = _selected_incident(
        runner.runtime.decision,
        run_key,
        selected=True,
    )
    first_context = _context(incident_id, tools)
    conflicting_context = _context(
        incident_id,
        tools,
        resource="different-pod",
    )

    first = await runner.run_once(first_context, run_key=run_key)
    failed = await runner.run_once(conflicting_context, run_key=run_key)

    assert first.status == InvestigationEngineShadowRunStatus.EXECUTED
    assert failed.status == InvestigationEngineShadowRunStatus.FAILED
    assert failed.external_calls_made is None
    assert failed.failure_code == "InvestigationSessionConflictError"
    assert "different-pod" not in failed.model_dump_json()
    assert reasoner.calls == 1
    assert probes.calls == 0
    assert tools.calls == 0


@pytest.mark.asyncio
async def test_runner_rejects_unshared_tools_before_sampling_or_store_access(
    tmp_path,
):
    runner, reasoner, probes, tools = _runner(tmp_path)
    context = _context(UUID(int=1), FakeTools())

    with pytest.raises(TypeError, match="shared Runtime tools"):
        await runner.run_once(context, run_key="shadow-invalid-tools")

    assert reasoner.calls == 0
    assert probes.calls == 0
    assert tools.calls == 0
