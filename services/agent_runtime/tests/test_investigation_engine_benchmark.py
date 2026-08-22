from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from services.agent_runtime.app.investigation.engine import (
    CustomInvestigationEngine,
)
from services.agent_runtime.app.investigation.engine_benchmark import (
    InvestigationEngineBenchmarkCase,
    InvestigationEngineBenchmarkRunner,
)
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
    DurableInvestigationSessionLoop,
    InvestigationSessionLoopOutcome,
)
from services.agent_runtime.app.investigation.session_models import (
    InvestigationSessionStatus,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)

INCIDENT_ID = UUID(
    "00000000-0000-4000-8000-000000000816"
)
NOW = datetime(
    2026,
    8,
    16,
    12,
    0,
    tzinfo=UTC,
)
RUN_KEY = "oomkilled-engine-comparison-v1"


class DeterministicOOMReasoner(BaseInvestigationReasoner):
    def __init__(
        self,
        *,
        cause: str = "container exceeded its memory limit",
    ) -> None:
        self.cause = cause
        self.calls = 0

    async def decide(self, scope, state):
        self.calls += 1
        if not state.evidence:
            return InvestigationDecision(
                hypotheses=[
                    IncidentHypothesis(
                        hypothesis_id="oom-memory-pressure",
                        cause=self.cause,
                        confidence=0.55,
                        missing_evidence=[
                            "last container termination reason"
                        ],
                    )
                ],
                rationale_summary=(
                    "collect the bounded Kubernetes Pod state"
                ),
                stop=False,
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            )

        evidence_id = state.evidence[0].evidence_id
        return InvestigationDecision(
            hypotheses=[
                IncidentHypothesis(
                    hypothesis_id="oom-memory-pressure",
                    cause=self.cause,
                    confidence=0.74,
                    supporting_evidence_ids=[
                        evidence_id
                    ],
                    missing_evidence=[
                        "memory working set near the configured limit"
                    ],
                )
            ],
            rationale_summary=(
                "OOMKilled is confirmed but memory pressure metrics "
                "are still required for a sufficient RCA"
            ),
            stop=True,
            stop_reason=(
                InvestigationStopReason.INSUFFICIENT_EVIDENCE
            ),
        )


class DeterministicPodStateProbe:
    def __init__(
        self,
        *,
        evidence_id: str,
        observed_at: datetime,
    ) -> None:
        self.evidence_id = evidence_id
        self.observed_at = observed_at
        self.calls = 0

    async def collect(self, context, scope, probe):
        self.calls += 1
        assert context.authorization == "must-not-be-exported"
        return EvidenceItem(
            evidence_id=self.evidence_id,
            probe=probe,
            source="kubernetes",
            success=True,
            trusted=True,
            production_signal=True,
            reliability=0.95,
            observed_at=self.observed_at,
            cluster=scope.cluster,
            cluster_verified=True,
            facts={
                "last_termination_reason": "OOMKilled",
                "restart_count": 15,
            },
        )


def initial_state(
    *,
    resource: str = "payment-api-abc",
) -> InvestigationState:
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            alert_message="Container restarted after OOMKilled",
            event_occurred_at=NOW,
            resource=resource,
            namespace="payment",
            cluster="prod-a",
        )
    )


def benchmark_case(
    tmp_path,
    *,
    backend: str,
    cause: str = "container exceeded its memory limit",
):
    service = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / f"{backend}.db"
        )
    )
    reasoner = DeterministicOOMReasoner(
        cause=cause
    )
    probe = DeterministicPodStateProbe(
        evidence_id=f"{backend}-evidence-id",
        observed_at=(
            NOW
            if backend == "custom"
            else NOW + timedelta(milliseconds=17)
        ),
    )
    driver = DurableInvestigationSessionDriver(
        session_service=service,
        reasoner=reasoner,
        probe_executor=probe,
        require_cluster_verified_evidence=True,
        utc_clock=lambda: NOW,
    )

    if backend == "custom":
        engine = CustomInvestigationEngine(
            session_service=service,
            session_loop=DurableInvestigationSessionLoop(
                session_service=service,
                session_driver=driver,
            ),
        )
    else:
        engine = LangGraphInvestigationEngine(
            session_service=service,
            session_driver=driver,
        )

    case = InvestigationEngineBenchmarkCase(
        engine=engine,
        incident_id=INCIDENT_ID,
        run_key=RUN_KEY,
        initial_state=initial_state(),
        context=SimpleNamespace(
            authorization="must-not-be-exported"
        ),
        claimant=f"{backend}-benchmark-worker",
        created_at=NOW,
    )
    return case, reasoner, probe


@pytest.mark.asyncio
async def test_oomkilled_custom_and_langgraph_replay_are_equivalent(
    tmp_path,
):
    custom, custom_reasoner, custom_probe = benchmark_case(
        tmp_path,
        backend="custom",
    )
    langgraph, graph_reasoner, graph_probe = benchmark_case(
        tmp_path,
        backend="langgraph",
    )

    report = await InvestigationEngineBenchmarkRunner().compare(
        custom=custom,
        langgraph=langgraph,
    )

    assert report.passed is True
    assert report.semantic_equivalent is True
    assert report.protocol_equivalent is True
    assert report.external_call_budget_equal is True
    assert report.replay_safe is True

    for result in (
        report.custom,
        report.langgraph,
    ):
        assert result.outcome == (
            InvestigationSessionLoopOutcome.COMPLETED
        )
        assert result.session_status == (
            InvestigationSessionStatus.COMPLETED
        )
        assert result.investigation_stop_reason == (
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        )
        assert result.conclusion_available is False
        assert result.durable_steps == 3
        assert result.reasoner_steps == 2
        assert result.probe_steps == 1
        assert result.external_calls_made == 3
        assert result.replay_external_calls_made == 0
        assert result.invocation_count == 3
        assert result.version == 6
        assert result.replay_safe is True
        assert result.elapsed_ms >= 0.0

    assert custom_reasoner.calls == graph_reasoner.calls == 2
    assert custom_probe.calls == graph_probe.calls == 1

    serialized = report.model_dump_json()
    assert "must-not-be-exported" not in serialized
    assert "benchmark-worker" not in serialized
    assert "evidence-id" not in serialized
    assert ".db" not in serialized

    print(
        "CONTROLLED_OOMKILLED_ENGINE_BENCHMARK="
        + serialized
    )


@pytest.mark.asyncio
async def test_benchmark_detects_semantic_divergence(
    tmp_path,
):
    custom, _, _ = benchmark_case(
        tmp_path,
        backend="custom",
    )
    langgraph, _, _ = benchmark_case(
        tmp_path,
        backend="langgraph",
        cause="a different unsupported cause",
    )

    report = await InvestigationEngineBenchmarkRunner().compare(
        custom=custom,
        langgraph=langgraph,
    )

    assert report.semantic_equivalent is False
    assert report.protocol_equivalent is True
    assert report.passed is False


@pytest.mark.asyncio
async def test_benchmark_rejects_mismatched_or_shared_inputs(
    tmp_path,
):
    custom, custom_reasoner, custom_probe = benchmark_case(
        tmp_path,
        backend="custom",
    )
    langgraph, graph_reasoner, graph_probe = benchmark_case(
        tmp_path,
        backend="langgraph",
    )
    mismatched = InvestigationEngineBenchmarkCase(
        engine=langgraph.engine,
        incident_id=langgraph.incident_id,
        run_key=langgraph.run_key,
        initial_state=initial_state(
            resource="another-pod"
        ),
        context=langgraph.context,
        claimant=langgraph.claimant,
        created_at=NOW,
    )

    with pytest.raises(
        ValueError,
        match="immutable inputs differ",
    ):
        await InvestigationEngineBenchmarkRunner().compare(
            custom=custom,
            langgraph=mismatched,
        )

    shared_service_case = InvestigationEngineBenchmarkCase(
        engine=LangGraphInvestigationEngine(
            session_service=custom.engine.session_service,
            session_driver=(
                custom.engine.session_loop.session_driver
            ),
        ),
        incident_id=langgraph.incident_id,
        run_key=langgraph.run_key,
        initial_state=langgraph.initial_state,
        context=langgraph.context,
        claimant=langgraph.claimant,
        created_at=NOW,
    )
    with pytest.raises(
        ValueError,
        match="Stores must be isolated",
    ):
        await InvestigationEngineBenchmarkRunner().compare(
            custom=custom,
            langgraph=shared_service_case,
        )

    assert custom_reasoner.calls == graph_reasoner.calls == 0
    assert custom_probe.calls == graph_probe.calls == 0


@pytest.mark.parametrize(
    "value",
    (
        0,
        33,
        True,
        "3",
    ),
)
def test_benchmark_rejects_invalid_invocation_budget(value):
    with pytest.raises(
        ValueError,
        match="invocation limit is invalid",
    ):
        InvestigationEngineBenchmarkRunner(
            max_invocations=value,
        )
