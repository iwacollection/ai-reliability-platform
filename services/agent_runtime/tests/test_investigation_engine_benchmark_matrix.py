from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from services.agent_runtime.app.investigation.engine import (
    BaseInvestigationEngine,
    CustomInvestigationEngine,
)
from services.agent_runtime.app.investigation.engine_benchmark import (
    InvestigationEngineBenchmarkCase,
    InvestigationEngineBenchmarkResult,
    InvestigationEngineBenchmarkRunner,
)
from services.agent_runtime.app.investigation.engine_benchmark_matrix import (
    InvestigationEngineBenchmarkArmObservation,
    InvestigationEngineBenchmarkMatrixEvaluator,
    InvestigationEngineBenchmarkScenario,
)
from services.agent_runtime.app.investigation.langgraph_engine import (
    LangGraphInvestigationEngine,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationDecision,
    InvestigationLimits,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
    InvestigationReasonerError,
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
    InvestigationSessionRecord,
    InvestigationSessionStatus,
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)

INCIDENT_ID = UUID(
    "00000000-0000-4000-8000-000000000817"
)
NOW = datetime(
    2026,
    8,
    16,
    20,
    0,
    tzinfo=UTC,
)
SECRET = "matrix-credential-must-not-be-exported"


class MatrixReasoner(BaseInvestigationReasoner):
    def __init__(
        self,
        scenario: InvestigationEngineBenchmarkScenario,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self.scenario = scenario
        self.delay_seconds = delay_seconds
        self.calls = 0

    async def decide(self, scope, state):
        self.calls += 1
        if self.delay_seconds:
            await asyncio.sleep(
                self.delay_seconds
            )

        if self.scenario == (
            InvestigationEngineBenchmarkScenario.REASONER_FAILURE
        ):
            raise InvestigationReasonerError(
                f"sanitized failure {SECRET}"
            )
        if self.scenario == (
            InvestigationEngineBenchmarkScenario.REASONER_TIMEOUT
        ):
            raise TimeoutError(
                SECRET
            )

        if not state.evidence:
            return InvestigationDecision(
                hypotheses=[
                    IncidentHypothesis(
                        hypothesis_id="kernel-oom",
                        cause="kernel OOM termination",
                        confidence=0.55,
                        missing_evidence=[
                            "last termination reason"
                        ],
                    )
                ],
                rationale_summary=(
                    "collect the bounded Kubernetes Pod state"
                ),
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            )

        evidence = state.evidence[0]
        if self.scenario == (
            InvestigationEngineBenchmarkScenario.SUFFICIENT_EVIDENCE
        ):
            return InvestigationDecision(
                hypotheses=[
                    IncidentHypothesis(
                        hypothesis_id="kernel-oom",
                        cause="kernel OOM termination",
                        confidence=0.82,
                        supporting_evidence_ids=[
                            evidence.evidence_id
                        ],
                    )
                ],
                rationale_summary=(
                    "trusted Pod state confirms the termination mechanism"
                ),
                stop=True,
                stop_reason=(
                    InvestigationStopReason.SUFFICIENT_EVIDENCE
                ),
                conclusion=InvestigationConclusion(
                    root_cause="kernel OOM termination",
                    confidence=0.80,
                    evidence_ids=[
                        evidence.evidence_id
                    ],
                ),
            )

        return InvestigationDecision(
            hypotheses=[
                IncidentHypothesis(
                    hypothesis_id="kernel-oom",
                    cause="kernel OOM termination",
                    confidence=(
                        0.20
                        if not evidence.success
                        else 0.72
                    ),
                    supporting_evidence_ids=(
                        [evidence.evidence_id]
                        if evidence.success
                        else []
                    ),
                    missing_evidence=[
                        "bounded memory-pressure metrics"
                    ],
                )
            ],
            rationale_summary=(
                "the bounded evidence cannot prove a sufficient RCA"
            ),
            stop=True,
            stop_reason=(
                InvestigationStopReason.INSUFFICIENT_EVIDENCE
            ),
        )


class MatrixProbe:
    def __init__(
        self,
        scenario: InvestigationEngineBenchmarkScenario,
        *,
        backend: str,
    ) -> None:
        self.scenario = scenario
        self.backend = backend
        self.calls = 0

    async def collect(self, context, scope, probe):
        self.calls += 1
        assert context.authorization == SECRET
        if self.scenario == (
            InvestigationEngineBenchmarkScenario.PROBE_FAILURE
        ):
            raise RuntimeError(
                SECRET
            )

        return EvidenceItem(
            evidence_id=f"{self.backend}-matrix-evidence",
            probe=probe,
            source="kubernetes",
            success=True,
            trusted=True,
            production_signal=True,
            reliability=0.95,
            observed_at=(
                NOW
                if self.backend == "custom"
                else NOW + timedelta(milliseconds=11)
            ),
            cluster=scope.cluster,
            cluster_verified=True,
            facts={
                "last_termination_reason": "OOMKilled",
                "restart_count": 15,
            },
        )


def initial_state(
    scenario: InvestigationEngineBenchmarkScenario,
) -> InvestigationState:
    limits = (
        InvestigationLimits(
            max_iterations=1,
            max_tool_calls=1,
            timeout_seconds=30.0,
        )
        if scenario
        == InvestigationEngineBenchmarkScenario.BUDGET_EXHAUSTION
        else InvestigationLimits()
    )
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            alert_message="Container restarted after OOMKilled",
            event_occurred_at=NOW,
            resource="payment-api-abc",
            namespace="payment",
            cluster="prod-a",
        ),
        limits=limits,
    )


def engine_components(
    tmp_path,
    *,
    scenario: InvestigationEngineBenchmarkScenario,
    backend: str,
    db_name: str | None = None,
    reasoner: MatrixReasoner | None = None,
):
    service = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path / (
                db_name
                or f"{scenario.value}-{backend}.db"
            )
        )
    )
    active_reasoner = reasoner or MatrixReasoner(
        scenario
    )
    probe = MatrixProbe(
        scenario,
        backend=backend,
    )
    driver = DurableInvestigationSessionDriver(
        session_service=service,
        reasoner=active_reasoner,
        probe_executor=probe,
        require_cluster_verified_evidence=True,
        utc_clock=lambda: NOW,
    )
    if backend == "custom":
        engine: BaseInvestigationEngine = CustomInvestigationEngine(
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
    return engine, service, active_reasoner, probe


def benchmark_case(
    tmp_path,
    *,
    scenario: InvestigationEngineBenchmarkScenario,
    backend: str,
):
    engine, service, reasoner, probe = engine_components(
        tmp_path,
        scenario=scenario,
        backend=backend,
    )
    return (
        InvestigationEngineBenchmarkCase(
            engine=engine,
            incident_id=INCIDENT_ID,
            run_key=f"matrix-{scenario.value}",
            initial_state=initial_state(
                scenario
            ),
            context=SimpleNamespace(
                authorization=SECRET
            ),
            claimant=f"{backend}-matrix-worker",
            created_at=NOW,
        ),
        service,
        reasoner,
        probe,
    )


def standard_observation(
    result: InvestigationEngineBenchmarkResult,
    *,
    reasoner_calls: int,
    probe_calls: int,
    failed_probe_observed: bool = False,
) -> InvestigationEngineBenchmarkArmObservation:
    return InvestigationEngineBenchmarkArmObservation(
        backend=result.backend,
        session_status=result.session_status,
        investigation_status=result.investigation_status,
        investigation_stop_reason=(
            result.investigation_stop_reason
        ),
        outcome=result.outcome,
        loop_stop_reason=result.stop_reason,
        durable_steps=result.durable_steps,
        reasoner_calls=reasoner_calls,
        probe_calls=probe_calls,
        external_calls_made=(
            result.external_calls_made
        ),
        replay_external_calls_made=(
            result.replay_external_calls_made
        ),
        recovery_required=(
            result.stop_reason
            == InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
        ),
        failed_probe_observed=failed_probe_observed,
        semantic_digest=result.semantic_digest,
        protocol_digest=result.protocol_digest,
        elapsed_ms=result.elapsed_ms,
    )


async def standard_scenario(
    tmp_path,
    scenario: InvestigationEngineBenchmarkScenario,
):
    custom = benchmark_case(
        tmp_path,
        scenario=scenario,
        backend="custom",
    )
    langgraph = benchmark_case(
        tmp_path,
        scenario=scenario,
        backend="langgraph",
    )
    report = await InvestigationEngineBenchmarkRunner().compare(
        custom=custom[0],
        langgraph=langgraph[0],
    )

    failed_probe = (
        scenario
        == InvestigationEngineBenchmarkScenario.PROBE_FAILURE
    )
    observations = (
        standard_observation(
            report.custom,
            reasoner_calls=custom[2].calls,
            probe_calls=custom[3].calls,
            failed_probe_observed=failed_probe,
        ),
        standard_observation(
            report.langgraph,
            reasoner_calls=langgraph[2].calls,
            probe_calls=langgraph[3].calls,
            failed_probe_observed=failed_probe,
        ),
    )
    return observations


def session_observation(
    *,
    backend: str,
    session: InvestigationSessionRecord,
    outcome: InvestigationSessionLoopOutcome,
    stop_reason: InvestigationSessionLoopStopReason,
    reasoner_calls: int,
    probe_calls: int,
    external_calls: int,
    replay_calls: int,
    concurrent_call_grants: int = 0,
    concurrent_replay_blocked: bool = False,
    restart_performed: bool = False,
) -> InvestigationEngineBenchmarkArmObservation:
    return InvestigationEngineBenchmarkArmObservation(
        backend=backend,
        session_status=session.status,
        investigation_status=session.state.status,
        investigation_stop_reason=session.state.stop_reason,
        outcome=outcome,
        loop_stop_reason=stop_reason,
        durable_steps=len(session.steps),
        reasoner_calls=reasoner_calls,
        probe_calls=probe_calls,
        external_calls_made=external_calls,
        replay_external_calls_made=replay_calls,
        recovery_required=(
            stop_reason
            == InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
        ),
        concurrent_call_grants=concurrent_call_grants,
        concurrent_replay_blocked=(
            concurrent_replay_blocked
        ),
        restart_performed=restart_performed,
        semantic_digest=(
            InvestigationEngineBenchmarkRunner.semantic_digest(
                session
            )
        ),
        protocol_digest=(
            InvestigationEngineBenchmarkRunner.protocol_digest(
                session
            )
        ),
        elapsed_ms=0.0,
    )


async def concurrent_scenario(
    tmp_path,
    *,
    backend: str,
):
    scenario = InvestigationEngineBenchmarkScenario.CONCURRENT_CLAIM
    reasoner = MatrixReasoner(
        scenario,
        delay_seconds=0.03,
    )
    first, service, _, probe = engine_components(
        tmp_path,
        scenario=scenario,
        backend=backend,
        db_name=f"concurrent-{backend}.db",
        reasoner=reasoner,
    )
    second, _, _, _ = engine_components(
        tmp_path,
        scenario=scenario,
        backend=backend,
        db_name=f"concurrent-{backend}.db",
        reasoner=reasoner,
    )
    created = await first.create_or_get(
        incident_id=INCIDENT_ID,
        run_key=f"matrix-concurrent-{backend}",
        initial_state=initial_state(
            scenario
        ),
        now=NOW,
    )
    results = await asyncio.gather(
        first.advance(
            created.session.session_id,
            context=SimpleNamespace(
                authorization=SECRET
            ),
            claimant=f"{backend}-concurrent-worker",
            expected_version=0,
        ),
        second.advance(
            created.session.session_id,
            context=SimpleNamespace(
                authorization=SECRET
            ),
            claimant=f"{backend}-concurrent-worker",
            expected_version=0,
        ),
    )
    latest = await service.require(
        created.session.session_id
    )
    replay = await first.advance(
        latest.session_id,
        context=SimpleNamespace(
            authorization=SECRET
        ),
        claimant=f"{backend}-concurrent-worker",
        expected_version=0,
    )
    external_calls = sum(
        result.external_calls_made
        for result in results
    )
    blocked = any(
        result.outcome
        == InvestigationSessionLoopOutcome.BLOCKED
        for result in results
    )
    assert probe.calls == 0
    return session_observation(
        backend=backend,
        session=latest,
        outcome=InvestigationSessionLoopOutcome.PAUSED,
        stop_reason=InvestigationSessionLoopStopReason.STEP_LIMIT,
        reasoner_calls=reasoner.calls,
        probe_calls=probe.calls,
        external_calls=external_calls,
        replay_calls=replay.external_calls_made,
        concurrent_call_grants=external_calls,
        concurrent_replay_blocked=blocked,
    )


async def restart_scenario(
    tmp_path,
    *,
    backend: str,
):
    scenario = InvestigationEngineBenchmarkScenario.RESTART_RECOVERY
    db_name = f"restart-{backend}.db"
    first, service, first_reasoner, _ = engine_components(
        tmp_path,
        scenario=scenario,
        backend=backend,
        db_name=db_name,
    )
    created = await first.create_or_get(
        incident_id=INCIDENT_ID,
        run_key=f"matrix-restart-{backend}",
        initial_state=initial_state(
            scenario
        ),
        created_by="matrix-benchmark",
        now=NOW,
    )
    first_result = await first.advance(
        created.session.session_id,
        context=SimpleNamespace(
            authorization=SECRET
        ),
        claimant=f"{backend}-restart-worker",
        expected_version=0,
    )
    assert first_result.external_calls_made == 1

    restarted, restarted_service, restarted_reasoner, restarted_probe = (
        engine_components(
            tmp_path,
            scenario=scenario,
            backend=backend,
            db_name=db_name,
        )
    )
    external_calls = first_result.external_calls_made
    current = await restarted_service.require(
        created.session.session_id
    )
    final_result = None
    for _ in range(2):
        final_result = await restarted.advance(
            current.session_id,
            context=SimpleNamespace(
                authorization=SECRET
            ),
            claimant=f"{backend}-restart-worker",
            expected_version=current.version,
        )
        external_calls += final_result.external_calls_made
        current = final_result.session

    assert final_result is not None
    replay = await restarted.advance(
        current.session_id,
        context=SimpleNamespace(
            authorization=SECRET
        ),
        claimant=f"{backend}-restart-worker",
    )
    assert service is not restarted_service
    return session_observation(
        backend=backend,
        session=current,
        outcome=final_result.outcome,
        stop_reason=final_result.stop_reason,
        reasoner_calls=(
            first_reasoner.calls
            + restarted_reasoner.calls
        ),
        probe_calls=restarted_probe.calls,
        external_calls=external_calls,
        replay_calls=replay.external_calls_made,
        restart_performed=True,
    )


async def matrix_observations(tmp_path):
    observations = {}
    standard = (
        InvestigationEngineBenchmarkScenario.SUFFICIENT_EVIDENCE,
        InvestigationEngineBenchmarkScenario.INSUFFICIENT_EVIDENCE,
        InvestigationEngineBenchmarkScenario.PROBE_FAILURE,
        InvestigationEngineBenchmarkScenario.REASONER_FAILURE,
        InvestigationEngineBenchmarkScenario.REASONER_TIMEOUT,
        InvestigationEngineBenchmarkScenario.BUDGET_EXHAUSTION,
    )
    for scenario in standard:
        observations[scenario] = await standard_scenario(
            tmp_path,
            scenario,
        )

    observations[
        InvestigationEngineBenchmarkScenario.CONCURRENT_CLAIM
    ] = (
        await concurrent_scenario(
            tmp_path,
            backend="custom",
        ),
        await concurrent_scenario(
            tmp_path,
            backend="langgraph",
        ),
    )
    observations[
        InvestigationEngineBenchmarkScenario.RESTART_RECOVERY
    ] = (
        await restart_scenario(
            tmp_path,
            backend="custom",
        ),
        await restart_scenario(
            tmp_path,
            backend="langgraph",
        ),
    )
    return observations


@pytest.mark.asyncio
async def test_complete_eight_scenario_matrix_passes(tmp_path):
    report = InvestigationEngineBenchmarkMatrixEvaluator().evaluate(
        await matrix_observations(tmp_path)
    )

    assert report.passed is True
    assert report.scenario_count == 8
    assert report.passed_count == 8
    assert report.all_semantically_equivalent is True
    assert report.all_protocol_equivalent is True
    assert report.all_call_budgets_equivalent is True
    assert report.all_replay_safe is True
    assert report.sensitive_output_absent is True

    serialized = report.model_dump_json()
    assert SECRET not in serialized
    assert "matrix-worker" not in serialized
    assert "matrix-evidence" not in serialized
    assert ".db" not in serialized
    print(
        "CONTROLLED_INVESTIGATION_ENGINE_BENCHMARK_MATRIX="
        + json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@pytest.mark.asyncio
async def test_matrix_fails_closed_on_semantic_divergence(tmp_path):
    observations = await matrix_observations(tmp_path)
    scenario = (
        InvestigationEngineBenchmarkScenario.SUFFICIENT_EVIDENCE
    )
    custom, langgraph = observations[scenario]
    observations[scenario] = (
        custom,
        langgraph.model_copy(
            update={
                "semantic_digest": "f" * 64
            }
        ),
    )

    report = InvestigationEngineBenchmarkMatrixEvaluator().evaluate(
        observations
    )

    assert report.passed is False
    assert report.passed_count == 7
    assert report.all_semantically_equivalent is False


def test_matrix_rejects_missing_scenario():
    with pytest.raises(
        ValueError,
        match="incomplete",
    ):
        InvestigationEngineBenchmarkMatrixEvaluator().evaluate(
            {}
        )


def test_matrix_rejects_duplicate_backend():
    placeholder = InvestigationEngineBenchmarkArmObservation(
        backend="custom",
        session_status=InvestigationSessionStatus.PAUSED,
        investigation_status="running",
        investigation_stop_reason=None,
        outcome=InvestigationSessionLoopOutcome.PAUSED,
        loop_stop_reason=InvestigationSessionLoopStopReason.STEP_LIMIT,
        durable_steps=1,
        reasoner_calls=1,
        probe_calls=0,
        external_calls_made=1,
        replay_external_calls_made=0,
        semantic_digest="a" * 64,
        protocol_digest="b" * 64,
        elapsed_ms=0.0,
    )
    observations = {
        scenario: (
            placeholder,
            placeholder,
        )
        for scenario in InvestigationEngineBenchmarkScenario
    }

    with pytest.raises(
        ValueError,
        match="backends",
    ):
        InvestigationEngineBenchmarkMatrixEvaluator().evaluate(
            observations
        )
