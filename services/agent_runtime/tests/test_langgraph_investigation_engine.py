from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from services.agent_runtime.app.investigation.langgraph_engine import (
    LangGraphInvestigationEngine,
)
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
    InvestigationSessionLoopOutcome,
    InvestigationSessionLoopStopReason,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationStepKind,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)


INCIDENT_ID = UUID(
    "00000000-0000-4000-8000-000000000731"
)
NOW = datetime(
    2026,
    8,
    16,
    tzinfo=UTC,
)


def continuing_decision() -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="memory-pressure",
                cause="container memory pressure",
                confidence=0.5,
                missing_evidence=["pod state"],
            )
        ],
        rationale_summary="collect pod state",
        stop=False,
        next_probe=InvestigationProbe.KUBERNETES_POD_STATE,
    )


def terminal_decision() -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="memory-pressure",
                cause="container memory pressure",
                confidence=0.4,
                supporting_evidence_ids=["evidence-1"],
                missing_evidence=["historical peak"],
            )
        ],
        rationale_summary="bounded evidence remains incomplete",
        stop=True,
        stop_reason=InvestigationStopReason.INSUFFICIENT_EVIDENCE,
    )


class QueueReasoner(BaseInvestigationReasoner):
    def __init__(self):
        self.decisions = [
            continuing_decision(),
            terminal_decision(),
        ]
        self.calls = 0

    async def decide(self, scope, state):
        index = self.calls
        self.calls += 1
        return self.decisions[
            min(index, len(self.decisions) - 1)
        ]


class CountingProbeExecutor:
    def __init__(self):
        self.calls = 0

    async def collect(self, context, scope, probe):
        self.calls += 1
        return EvidenceItem(
            evidence_id="evidence-1",
            probe=probe,
            source="kubernetes",
            success=True,
            trusted=True,
            production_signal=True,
            reliability=0.95,
            observed_at=NOW,
            cluster=scope.cluster,
            cluster_verified=True,
            facts={"reason": "OOMKilled"},
        )


def initial_state() -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            resource="payment-api-abc",
            namespace="payment",
            cluster="prod-a",
        )
    )


async def components(tmp_path):
    service = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / "langgraph.db"
        )
    )
    reasoner = QueueReasoner()
    probe = CountingProbeExecutor()
    driver = DurableInvestigationSessionDriver(
        session_service=service,
        reasoner=reasoner,
        probe_executor=probe,
        utc_clock=lambda: NOW,
    )
    engine = LangGraphInvestigationEngine(
        session_service=service,
        session_driver=driver,
    )
    session = (
        await engine.create_or_get(
            incident_id=INCIDENT_ID,
            run_key="langgraph-engine-v1",
            initial_state=initial_state(),
            now=NOW,
        )
    ).session
    return engine, session, reasoner, probe


@pytest.mark.asyncio
async def test_langgraph_routes_reasoner_probe_reasoner_to_terminal(
    tmp_path,
):
    engine, session, reasoner, probe = await components(
        tmp_path
    )

    result = await engine.advance(
        session.session_id,
        context=SimpleNamespace(),
        claimant="langgraph-worker",
        max_external_steps=3,
        expected_version=0,
    )

    assert engine.name == "langgraph"
    assert engine.checkpointer_enabled is False
    assert result.outcome == InvestigationSessionLoopOutcome.COMPLETED
    assert result.stop_reason == (
        InvestigationSessionLoopStopReason.SESSION_COMPLETED
    )
    assert result.external_calls_made == 3
    assert [step.kind for step in result.session.steps] == [
        InvestigationStepKind.REASONER,
        InvestigationStepKind.PROBE,
        InvestigationStepKind.REASONER,
    ]
    assert reasoner.calls == 2
    assert probe.calls == 1

    replay = await engine.advance(
        session.session_id,
        context=SimpleNamespace(),
        claimant="langgraph-worker",
        max_external_steps=3,
    )
    assert replay.outcome == InvestigationSessionLoopOutcome.COMPLETED
    assert replay.external_calls_made == 0
    assert reasoner.calls == 2
    assert probe.calls == 1


@pytest.mark.asyncio
async def test_langgraph_one_step_and_stale_version_are_bounded(
    tmp_path,
):
    engine, session, reasoner, probe = await components(
        tmp_path
    )
    first = await engine.advance(
        session.session_id,
        context=SimpleNamespace(),
        claimant="first-worker",
        expected_version=0,
    )

    assert first.outcome == InvestigationSessionLoopOutcome.PAUSED
    assert first.external_calls_made == 1
    assert reasoner.calls == 1
    assert probe.calls == 0

    stale = await engine.advance(
        session.session_id,
        context=SimpleNamespace(),
        claimant="stale-worker",
        expected_version=0,
    )

    assert stale.outcome == InvestigationSessionLoopOutcome.BLOCKED
    assert stale.stop_reason == (
        InvestigationSessionLoopStopReason.DRIVER_BLOCKED
    )
    assert stale.external_calls_made == 0
    assert reasoner.calls == 1
    assert probe.calls == 0


def test_langgraph_rejects_a_second_persistence_graph(tmp_path):
    first_service = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / "first.db"
        )
    )
    second_service = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / "second.db"
        )
    )
    driver = DurableInvestigationSessionDriver(
        session_service=first_service,
        reasoner=QueueReasoner(),
        probe_executor=CountingProbeExecutor(),
    )

    with pytest.raises(
        ValueError,
        match="share one Service",
    ):
        LangGraphInvestigationEngine(
            session_service=second_service,
            session_driver=driver,
        )
