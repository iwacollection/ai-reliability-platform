from __future__ import annotations

import asyncio

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.session_driver import (
    DurableInvestigationSessionDriver,
)
from services.agent_runtime.app.investigation.session_loop import (
    DurableInvestigationSessionLoop,
    InvestigationSessionLoopOutcome,
    InvestigationSessionLoopStopReason,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionStatus,
    InvestigationStepKind,
    canonical_digest,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)


class QueueReasoner(BaseInvestigationReasoner):
    def __init__(self, decisions=None, error=None, delay=0.0):
        self.decisions = list(
            decisions or [_continuing_decision()]
        )
        self.error = error
        self.delay = delay
        self.calls = 0

    async def decide(self, scope, state):
        index = self.calls
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.decisions[
            min(index, len(self.decisions) - 1)
        ]


class CountingProbeExecutor:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    async def collect(self, context, scope, probe):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return EvidenceItem(
            evidence_id="evidence-1",
            probe=probe,
            source="kubernetes",
            success=True,
            trusted=True,
            production_signal=True,
            reliability=0.95,
            observed_at=datetime.now(UTC),
            cluster="prod-a",
            cluster_verified=True,
            facts={"reason": "OOMKilled"},
        )


def _state() -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            resource="payment-api-abc",
            namespace="payment",
            cluster="prod-a",
        )
    )


def _continuing_decision() -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="h1",
                cause="container memory pressure",
                confidence=0.5,
                missing_evidence=["pod state"],
            )
        ],
        rationale_summary="collect pod state",
        stop=False,
        next_probe=InvestigationProbe.KUBERNETES_POD_STATE,
    )


def _terminal_decision() -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="h1",
                cause="container memory pressure",
                confidence=0.4,
                supporting_evidence_ids=["evidence-1"],
                missing_evidence=["historical peak"],
            )
        ],
        rationale_summary="bounded evidence cannot prove the mechanism",
        stop=True,
        stop_reason=InvestigationStopReason.INSUFFICIENT_EVIDENCE,
    )


async def _components(
    tmp_path,
    *,
    db_name="sessions.db",
    reasoner=None,
    probe_executor=None,
    now=None,
    clock_offset=1,
):
    base_time = now or datetime.now(UTC)
    service = InvestigationSessionService(
        InvestigationSessionStore(tmp_path / db_name)
    )
    reasoner = reasoner or QueueReasoner()
    probe_executor = probe_executor or CountingProbeExecutor()
    driver = DurableInvestigationSessionDriver(
        session_service=service,
        reasoner=reasoner,
        probe_executor=probe_executor,
        utc_clock=lambda: (
            base_time + timedelta(seconds=clock_offset)
        ),
    )
    loop = DurableInvestigationSessionLoop(
        session_service=service,
        session_driver=driver,
    )
    return loop, service, reasoner, probe_executor, base_time


async def _create(service, *, now):
    return (
        await service.create_or_get(
            incident_id=uuid4(),
            run_key="automatic-shadow-v1",
            initial_state=_state(),
            now=now,
        )
    ).session


@pytest.mark.asyncio
async def test_default_run_advances_one_reasoner_step_then_pauses(tmp_path):
    loop, service, reasoner, probe, now = await _components(tmp_path)
    session = await _create(service, now=now)

    result = await loop.run(
        session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    assert result.outcome == InvestigationSessionLoopOutcome.PAUSED
    assert result.stop_reason == InvestigationSessionLoopStopReason.STEP_LIMIT
    assert result.external_calls_made == 1
    assert result.session.status == InvestigationSessionStatus.PAUSED
    assert result.session.steps[-1].kind == InvestigationStepKind.REASONER
    assert reasoner.calls == 1
    assert probe.calls == 0


@pytest.mark.asyncio
async def test_restart_resumes_with_probe_instead_of_repeating_reasoner(tmp_path):
    db_name = "restart.db"
    first, service, reasoner, _, now = await _components(
        tmp_path,
        db_name=db_name,
    )
    session = await _create(service, now=now)
    reasoned = await first.run(
        session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    restarted, _, restarted_reasoner, probe, _ = await _components(
        tmp_path,
        db_name=db_name,
        now=now,
    )
    resumed = await restarted.run(
        reasoned.session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    assert reasoner.calls == 1
    assert restarted_reasoner.calls == 0
    assert probe.calls == 1
    assert resumed.session.steps[-1].kind == InvestigationStepKind.PROBE
    assert resumed.session.state.tool_call_count == 1


@pytest.mark.asyncio
async def test_bounded_loop_reaches_terminal_without_repeating_calls(tmp_path):
    reasoner = QueueReasoner(
        [_continuing_decision(), _terminal_decision()]
    )
    probe = CountingProbeExecutor()
    loop, service, _, _, now = await _components(
        tmp_path,
        reasoner=reasoner,
        probe_executor=probe,
    )
    session = await _create(service, now=now)

    result = await loop.run(
        session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
        max_external_steps=3,
    )

    assert result.outcome == InvestigationSessionLoopOutcome.COMPLETED
    assert result.stop_reason == (
        InvestigationSessionLoopStopReason.SESSION_COMPLETED
    )
    assert result.external_calls_made == 3
    assert reasoner.calls == 2
    assert probe.calls == 1
    assert len(result.session.steps) == 3


@pytest.mark.asyncio
async def test_terminal_replay_is_zero_call(tmp_path):
    reasoner = QueueReasoner(
        [_continuing_decision(), _terminal_decision()]
    )
    probe = CountingProbeExecutor()
    loop, service, _, _, now = await _components(
        tmp_path,
        reasoner=reasoner,
        probe_executor=probe,
    )
    session = await _create(service, now=now)
    completed = await loop.run(
        session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
        max_external_steps=3,
    )

    replay = await loop.run(
        completed.session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
        max_external_steps=3,
    )

    assert replay.outcome == InvestigationSessionLoopOutcome.COMPLETED
    assert replay.external_calls_made == 0
    assert reasoner.calls == 2
    assert probe.calls == 1


@pytest.mark.asyncio
async def test_claim_only_state_blocks_automatic_resume(tmp_path):
    loop, service, reasoner, probe, now = await _components(tmp_path)
    session = await _create(service, now=now)
    digest = canonical_digest(
        {
            "operation": loop.session_driver._REASONER_OPERATION,
            "session_id": session.session_id,
            "input_digest": session.input_digest,
            "state": session.state,
        }
    )
    await service.claim_step(
        session.session_id,
        kind=InvestigationStepKind.REASONER,
        request_digest=digest,
        claimant="runtime-worker-1",
    )

    result = await loop.run(
        session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    assert result.outcome == InvestigationSessionLoopOutcome.BLOCKED
    assert result.recovery_required is True
    assert result.external_calls_made == 0
    assert reasoner.calls == 0
    assert probe.calls == 0


@pytest.mark.asyncio
async def test_indeterminate_reasoner_stops_loop_without_retry(tmp_path):
    reasoner = QueueReasoner(error=ConnectionError("private endpoint"))
    loop, service, _, probe, now = await _components(
        tmp_path,
        reasoner=reasoner,
    )
    session = await _create(service, now=now)

    result = await loop.run(
        session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
        max_external_steps=3,
    )

    assert reasoner.calls == 1
    assert probe.calls == 0
    assert result.outcome == InvestigationSessionLoopOutcome.BLOCKED
    assert result.recovery_required is True
    assert result.external_calls_made == 1
    assert "private endpoint" not in result.session.model_dump_json()


@pytest.mark.asyncio
async def test_cross_instance_loops_make_one_external_call(tmp_path):
    db_name = "concurrent.db"
    now = datetime.now(UTC)
    reasoner = QueueReasoner(delay=0.02)
    first, first_service, _, _, _ = await _components(
        tmp_path,
        db_name=db_name,
        reasoner=reasoner,
        now=now,
    )
    second, _, _, _, _ = await _components(
        tmp_path,
        db_name=db_name,
        reasoner=reasoner,
        now=now,
    )
    session = await _create(first_service, now=now)

    results = await asyncio.gather(
        first.run(
            session.session_id,
            context=SimpleNamespace(),
            claimant="runtime-worker-1",
        ),
        second.run(
            session.session_id,
            context=SimpleNamespace(),
            claimant="runtime-worker-1",
        ),
    )

    assert reasoner.calls == 1
    assert sum(item.external_calls_made for item in results) == 1
    assert any(
        item.stop_reason
        in {
            InvestigationSessionLoopStopReason.RECOVERY_REQUIRED,
            InvestigationSessionLoopStopReason.CONCURRENT_REPLAY,
        }
        for item in results
    )


@pytest.mark.asyncio
async def test_expired_time_budget_is_blocked_before_claim(tmp_path):
    now = datetime.now(UTC)
    loop, service, reasoner, probe, _ = await _components(
        tmp_path,
        now=now,
        clock_offset=61,
    )
    session = await _create(service, now=now)

    result = await loop.run(
        session.session_id,
        context=SimpleNamespace(),
        claimant="runtime-worker-1",
    )

    assert result.outcome == InvestigationSessionLoopOutcome.BLOCKED
    assert result.stop_reason == (
        InvestigationSessionLoopStopReason.DRIVER_BLOCKED
    )
    assert result.external_calls_made == 0
    assert result.session.status == InvestigationSessionStatus.READY
    assert result.session.steps == ()
    assert reasoner.calls == 0
    assert probe.calls == 0


@pytest.mark.asyncio
async def test_step_limit_is_strictly_bounded(tmp_path):
    loop, service, _, _, now = await _components(tmp_path)
    session = await _create(service, now=now)

    for invalid in (0, 33, True, 1.5):
        with pytest.raises(ValueError, match="step limit"):
            await loop.run(
                session.session_id,
                context=SimpleNamespace(),
                claimant="runtime-worker-1",
                max_external_steps=invalid,
            )


@pytest.mark.asyncio
async def test_loop_does_not_publish_context_metadata(tmp_path):
    context = SimpleNamespace(metadata={"existing": "value"})
    loop, service, _, _, now = await _components(tmp_path)
    session = await _create(service, now=now)

    await loop.run(
        session.session_id,
        context=context,
        claimant="runtime-worker-1",
    )

    assert context.metadata == {"existing": "value"}


def test_loop_requires_one_shared_service(tmp_path):
    first_service = InvestigationSessionService(
        InvestigationSessionStore(tmp_path / "first.db")
    )
    second_service = InvestigationSessionService(
        InvestigationSessionStore(tmp_path / "second.db")
    )
    driver = DurableInvestigationSessionDriver(
        session_service=second_service,
        reasoner=QueueReasoner(),
        probe_executor=CountingProbeExecutor(),
    )

    with pytest.raises(ValueError, match="share one Service"):
        DurableInvestigationSessionLoop(
            session_service=first_service,
            session_driver=driver,
        )
