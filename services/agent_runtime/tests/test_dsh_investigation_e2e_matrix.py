from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from services.agent_runtime.app.investigation.dsh_investigation_reasoner import (
    DshInvestigationReasoner,
    DshInvestigationReasonerConfig,
    DshInvestigationReasonerTimeoutError,
)
from services.agent_runtime.app.investigation.dsh_runtime_adapter import (
    DshRunResult,
)
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
    InvestigationLimits,
    InvestigationScope,
    InvestigationState,
)
from services.agent_runtime.app.investigation.reasoner import (
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
)
from services.agent_runtime.app.investigation.session_service import (
    InvestigationSessionService,
)
from services.agent_runtime.app.investigation.session_store import (
    InvestigationSessionStore,
)


NOW = datetime(
    2026,
    8,
    20,
    2,
    0,
    tzinfo=UTC,
)
INCIDENT_ID = UUID(
    "7f294139-b76a-4d6c-8a33-361a01a8c777"
)
SECRET = "dsh-matrix-credential-must-not-be-exported"


@dataclass
class DshScenarioController:
    scenario: InvestigationEngineBenchmarkScenario
    delay_seconds: float = 0.0
    calls: int = 0
    initialize_calls: int = 0
    closed_runtimes: int = 0


class ScriptedDshRuntime:
    """
    Deterministic DSH protocol fixture.

    The durable stack uses the real DshInvestigationReasoner contract. Only the
    final Harness process/model transport is replaced so all eight scenarios are
    deterministic, fast and free of external credentials.
    """

    def __init__(
        self,
        controller: DshScenarioController,
    ) -> None:
        self.controller = controller

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        tb,
    ):
        self.controller.closed_runtimes += 1

    async def initialize(
        self,
        *,
        cwd: str,
        provider: str,
        model: str,
        max_tokens: int | None = None,
    ) -> dict:
        self.controller.initialize_calls += 1
        return {
            "serverInfo": {
                "name": "deepseek-harness-sdk-runtime",
                "version": "matrix-fixture",
            }
        }

    async def run_turn(
        self,
        input_text: str,
        *,
        session_id: str,
    ) -> DshRunResult:
        self.controller.calls += 1

        if self.controller.delay_seconds:
            await asyncio.sleep(
                self.controller.delay_seconds
            )

        scenario = self.controller.scenario
        if (
            scenario
            == InvestigationEngineBenchmarkScenario.REASONER_FAILURE
        ):
            raise RuntimeError(
                SECRET
            )
        if (
            scenario
            == InvestigationEngineBenchmarkScenario.REASONER_TIMEOUT
        ):
            raise TimeoutError(
                SECRET
            )

        try:
            payload = json.loads(
                input_text.rsplit(
                    "INPUT_JSON:\n",
                    1,
                )[1]
            )
        except Exception as error:
            raise AssertionError(
                "DSH contract prompt did not contain parseable INPUT_JSON"
            ) from error

        if (
            payload.get("contract_version")
            != "ai-reliability-dsh-reasoner-v1"
        ):
            raise AssertionError(
                "DSH contract version changed unexpectedly"
            )

        state = payload["state"]
        evidence = state.get(
            "evidence",
            []
        )

        if not evidence:
            decision = {
                "hypotheses": [
                    {
                        "hypothesis_id": "kernel-oom",
                        "cause": "kernel OOM termination",
                        "confidence": 0.55,
                        "supporting_evidence_ids": [],
                        "conflicting_evidence_ids": [],
                        "missing_evidence": [
                            "last termination reason"
                        ],
                        "optional_evidence": [],
                    }
                ],
                "rationale_summary": (
                    "collect the bounded Kubernetes Pod state"
                ),
                "stop": False,
                "stop_reason": None,
                "next_probe": "kubernetes_pod_state",
                "conclusion": None,
            }
        else:
            first = evidence[0]
            evidence_id = first[
                "evidence_id"
            ]

            if (
                scenario
                == InvestigationEngineBenchmarkScenario.SUFFICIENT_EVIDENCE
            ):
                decision = {
                    "hypotheses": [
                        {
                            "hypothesis_id": "kernel-oom",
                            "cause": "kernel OOM termination",
                            "confidence": 0.82,
                            "supporting_evidence_ids": [
                                evidence_id
                            ],
                            "conflicting_evidence_ids": [],
                            "missing_evidence": [],
                            "optional_evidence": [],
                        }
                    ],
                    "rationale_summary": (
                        "trusted Pod state confirms the termination mechanism"
                    ),
                    "stop": True,
                    "stop_reason": "sufficient_evidence",
                    "next_probe": None,
                    "conclusion": {
                        "root_cause": "kernel OOM termination",
                        "confidence": 0.80,
                        "evidence_ids": [
                            evidence_id
                        ],
                    },
                }
            else:
                success = bool(
                    first.get(
                        "success"
                    )
                )
                decision = {
                    "hypotheses": [
                        {
                            "hypothesis_id": "kernel-oom",
                            "cause": "kernel OOM termination",
                            "confidence": (
                                0.72
                                if success
                                else 0.20
                            ),
                            "supporting_evidence_ids": (
                                [evidence_id]
                                if success
                                else []
                            ),
                            "conflicting_evidence_ids": [],
                            "missing_evidence": [
                                "bounded memory-pressure metrics"
                            ],
                            "optional_evidence": [],
                        }
                    ],
                    "rationale_summary": (
                        "the bounded evidence cannot prove a sufficient RCA"
                    ),
                    "stop": True,
                    "stop_reason": "insufficient_evidence",
                    "next_probe": None,
                    "conclusion": None,
                }

        return DshRunResult(
            session_id=session_id,
            final_response=json.dumps(
                decision,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
            finish_reason="completed",
            events=(),
            notifications=(),
        )


class DshMatrixProbe:
    def __init__(
        self,
        scenario: InvestigationEngineBenchmarkScenario,
        *,
        backend: str,
    ) -> None:
        self.scenario = scenario
        self.backend = backend
        self.calls = 0

    async def collect(
        self,
        context,
        scope,
        probe,
    ):
        self.calls += 1
        assert context.authorization == SECRET

        if (
            self.scenario
            == InvestigationEngineBenchmarkScenario.PROBE_FAILURE
        ):
            raise RuntimeError(
                SECRET
            )

        return EvidenceItem(
            evidence_id=(
                f"{self.backend}-dsh-matrix-evidence"
            ),
            probe=probe,
            source="kubernetes",
            success=True,
            trusted=True,
            production_signal=True,
            reliability=0.95,
            observed_at=(
                NOW
                if self.backend == "custom"
                else NOW
                + timedelta(
                    milliseconds=11
                )
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
        if (
            scenario
            == InvestigationEngineBenchmarkScenario.BUDGET_EXHAUSTION
        )
        else InvestigationLimits()
    )
    return InvestigationState(
        scope=InvestigationScope(
            alert_name="PodOOMKilled",
            alert_message=(
                "Container restarted after OOMKilled"
            ),
            event_occurred_at=NOW,
            resource="payment-api-abc",
            namespace="payment",
            cluster="prod-a",
        ),
        limits=limits,
    )


def dsh_reasoner(
    tmp_path,
    *,
    scenario: InvestigationEngineBenchmarkScenario,
    controller: DshScenarioController | None = None,
) -> tuple[
    DshInvestigationReasoner,
    DshScenarioController,
]:
    active = (
        controller
        if controller is not None
        else DshScenarioController(
            scenario=scenario
        )
    )

    reasoner = DshInvestigationReasoner(
        runtime_factory=(
            lambda: ScriptedDshRuntime(
                active
            )
        ),
        config=DshInvestigationReasonerConfig(
            cwd=str(tmp_path),
            provider="deepseek-official",
            model="deepseek-v4-flash",
            max_tokens=2048,
        ),
    )
    return reasoner, active


def engine_components(
    tmp_path,
    *,
    scenario: InvestigationEngineBenchmarkScenario,
    backend: str,
    db_name: str | None = None,
    controller: DshScenarioController | None = None,
):
    service = InvestigationSessionService(
        InvestigationSessionStore(
            tmp_path
            / (
                db_name
                or f"dsh-{scenario.value}-{backend}.db"
            )
        )
    )
    reasoner, active_controller = (
        dsh_reasoner(
            tmp_path,
            scenario=scenario,
            controller=controller,
        )
    )
    probe = DshMatrixProbe(
        scenario,
        backend=backend,
    )
    driver = DurableInvestigationSessionDriver(
        session_service=service,
        reasoner=reasoner,
        probe_executor=probe,
        require_cluster_verified_evidence=True,
        utc_clock=lambda: NOW,
    )

    if backend == "custom":
        engine: BaseInvestigationEngine = (
            CustomInvestigationEngine(
                session_service=service,
                session_loop=DurableInvestigationSessionLoop(
                    session_service=service,
                    session_driver=driver,
                ),
            )
        )
    elif backend == "langgraph":
        engine = LangGraphInvestigationEngine(
            session_service=service,
            session_driver=driver,
        )
    else:
        raise ValueError(
            f"unsupported backend: {backend}"
        )

    return (
        engine,
        service,
        active_controller,
        probe,
    )


def benchmark_case(
    tmp_path,
    *,
    scenario: InvestigationEngineBenchmarkScenario,
    backend: str,
):
    (
        engine,
        service,
        controller,
        probe,
    ) = engine_components(
        tmp_path,
        scenario=scenario,
        backend=backend,
    )

    return (
        InvestigationEngineBenchmarkCase(
            engine=engine,
            incident_id=INCIDENT_ID,
            run_key=(
                f"dsh-matrix-{scenario.value}"
            ),
            initial_state=initial_state(
                scenario
            ),
            context=SimpleNamespace(
                authorization=SECRET
            ),
            claimant=(
                f"{backend}-dsh-matrix-worker"
            ),
            created_at=NOW,
        ),
        service,
        controller,
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
        investigation_status=(
            result.investigation_status
        ),
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
        failed_probe_observed=(
            failed_probe_observed
        ),
        semantic_digest=(
            result.semantic_digest
        ),
        protocol_digest=(
            result.protocol_digest
        ),
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

    report = (
        await InvestigationEngineBenchmarkRunner()
        .compare(
            custom=custom[0],
            langgraph=langgraph[0],
        )
    )

    failed_probe = (
        scenario
        == InvestigationEngineBenchmarkScenario.PROBE_FAILURE
    )

    return (
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
        investigation_status=(
            session.state.status
        ),
        investigation_stop_reason=(
            session.state.stop_reason
        ),
        outcome=outcome,
        loop_stop_reason=stop_reason,
        durable_steps=len(
            session.steps
        ),
        reasoner_calls=reasoner_calls,
        probe_calls=probe_calls,
        external_calls_made=external_calls,
        replay_external_calls_made=(
            replay_calls
        ),
        recovery_required=(
            stop_reason
            == InvestigationSessionLoopStopReason.RECOVERY_REQUIRED
        ),
        concurrent_call_grants=(
            concurrent_call_grants
        ),
        concurrent_replay_blocked=(
            concurrent_replay_blocked
        ),
        restart_performed=(
            restart_performed
        ),
        semantic_digest=(
            InvestigationEngineBenchmarkRunner
            .semantic_digest(
                session
            )
        ),
        protocol_digest=(
            InvestigationEngineBenchmarkRunner
            .protocol_digest(
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
    scenario = (
        InvestigationEngineBenchmarkScenario.CONCURRENT_CLAIM
    )
    controller = DshScenarioController(
        scenario=scenario,
        delay_seconds=0.03,
    )

    (
        first,
        service,
        _,
        probe,
    ) = engine_components(
        tmp_path,
        scenario=scenario,
        backend=backend,
        db_name=f"dsh-concurrent-{backend}.db",
        controller=controller,
    )
    (
        second,
        _,
        _,
        _,
    ) = engine_components(
        tmp_path,
        scenario=scenario,
        backend=backend,
        db_name=f"dsh-concurrent-{backend}.db",
        controller=controller,
    )

    created = await first.create_or_get(
        incident_id=INCIDENT_ID,
        run_key=(
            f"dsh-matrix-concurrent-{backend}"
        ),
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
            claimant=(
                f"{backend}-dsh-concurrent-worker"
            ),
            expected_version=0,
        ),
        second.advance(
            created.session.session_id,
            context=SimpleNamespace(
                authorization=SECRET
            ),
            claimant=(
                f"{backend}-dsh-concurrent-worker"
            ),
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
        claimant=(
            f"{backend}-dsh-concurrent-worker"
        ),
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
    assert controller.calls == 1

    return session_observation(
        backend=backend,
        session=latest,
        outcome=(
            InvestigationSessionLoopOutcome.PAUSED
        ),
        stop_reason=(
            InvestigationSessionLoopStopReason.STEP_LIMIT
        ),
        reasoner_calls=controller.calls,
        probe_calls=probe.calls,
        external_calls=external_calls,
        replay_calls=(
            replay.external_calls_made
        ),
        concurrent_call_grants=(
            external_calls
        ),
        concurrent_replay_blocked=blocked,
    )


async def restart_scenario(
    tmp_path,
    *,
    backend: str,
):
    scenario = (
        InvestigationEngineBenchmarkScenario.RESTART_RECOVERY
    )
    db_name = (
        f"dsh-restart-{backend}.db"
    )

    (
        first,
        service,
        first_controller,
        _,
    ) = engine_components(
        tmp_path,
        scenario=scenario,
        backend=backend,
        db_name=db_name,
    )

    created = await first.create_or_get(
        incident_id=INCIDENT_ID,
        run_key=(
            f"dsh-matrix-restart-{backend}"
        ),
        initial_state=initial_state(
            scenario
        ),
        created_by="dsh-matrix-benchmark",
        now=NOW,
    )

    first_result = await first.advance(
        created.session.session_id,
        context=SimpleNamespace(
            authorization=SECRET
        ),
        claimant=(
            f"{backend}-dsh-restart-worker"
        ),
        expected_version=0,
    )
    assert (
        first_result.external_calls_made
        == 1
    )

    (
        restarted,
        restarted_service,
        restarted_controller,
        restarted_probe,
    ) = engine_components(
        tmp_path,
        scenario=scenario,
        backend=backend,
        db_name=db_name,
    )

    external_calls = (
        first_result.external_calls_made
    )
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
            claimant=(
                f"{backend}-dsh-restart-worker"
            ),
            expected_version=(
                current.version
            ),
        )
        external_calls += (
            final_result.external_calls_made
        )
        current = final_result.session

    assert final_result is not None

    replay = await restarted.advance(
        current.session_id,
        context=SimpleNamespace(
            authorization=SECRET
        ),
        claimant=(
            f"{backend}-dsh-restart-worker"
        ),
    )

    assert service is not restarted_service

    return session_observation(
        backend=backend,
        session=current,
        outcome=final_result.outcome,
        stop_reason=(
            final_result.stop_reason
        ),
        reasoner_calls=(
            first_controller.calls
            + restarted_controller.calls
        ),
        probe_calls=(
            restarted_probe.calls
        ),
        external_calls=external_calls,
        replay_calls=(
            replay.external_calls_made
        ),
        restart_performed=True,
    )


async def dsh_matrix_observations(
    tmp_path,
):
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
        observations[
            scenario
        ] = await standard_scenario(
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
async def test_dsh_complete_eight_scenario_e2e_matrix_passes(
    tmp_path,
):
    report = (
        InvestigationEngineBenchmarkMatrixEvaluator()
        .evaluate(
            await dsh_matrix_observations(
                tmp_path
            )
        )
    )

    assert report.passed is True
    assert report.scenario_count == 8
    assert report.passed_count == 8
    assert (
        report.all_semantically_equivalent
        is True
    )
    assert (
        report.all_protocol_equivalent
        is True
    )
    assert (
        report.all_call_budgets_equivalent
        is True
    )
    assert report.all_replay_safe is True
    assert (
        report.sensitive_output_absent
        is True
    )

    serialized = report.model_dump_json()
    assert SECRET not in serialized
    assert "dsh-matrix-worker" not in serialized
    assert "dsh-matrix-evidence" not in serialized
    assert ".db" not in serialized

    print(
        "DSH_INVESTIGATION_E2E_MATRIX="
        + json.dumps(
            report.model_dump(
                mode="json"
            ),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@pytest.mark.asyncio
async def test_dsh_matrix_fails_closed_on_semantic_divergence(
    tmp_path,
):
    observations = (
        await dsh_matrix_observations(
            tmp_path
        )
    )
    scenario = (
        InvestigationEngineBenchmarkScenario.SUFFICIENT_EVIDENCE
    )
    custom, langgraph = observations[
        scenario
    ]
    observations[
        scenario
    ] = (
        custom,
        langgraph.model_copy(
            update={
                "semantic_digest": "f" * 64
            }
        ),
    )

    report = (
        InvestigationEngineBenchmarkMatrixEvaluator()
        .evaluate(
            observations
        )
    )

    assert report.passed is False
    assert report.passed_count == 7
    assert (
        report.all_semantically_equivalent
        is False
    )


@pytest.mark.asyncio
async def test_dsh_reasoner_runtime_is_ephemeral_per_durable_decision(
    tmp_path,
):
    scenario = (
        InvestigationEngineBenchmarkScenario.INSUFFICIENT_EVIDENCE
    )
    (
        case,
        _,
        controller,
        probe,
    ) = benchmark_case(
        tmp_path,
        scenario=scenario,
        backend="custom",
    )

    created = await case.engine.create_or_get(
        incident_id=case.incident_id,
        run_key=case.run_key,
        initial_state=case.initial_state,
        created_by="dsh-e2e-ephemeral",
        now=case.created_at,
    )
    result = await case.engine.advance(
        created.session.session_id,
        context=case.context,
        claimant=case.claimant,
        max_external_steps=3,
        expected_version=0,
    )

    assert result.session.status.value == "completed"
    assert result.external_calls_made == 3
    assert controller.calls == 2
    assert controller.initialize_calls == 2
    assert controller.closed_runtimes == 2
    assert probe.calls == 1


@pytest.mark.asyncio
async def test_dsh_timeout_preserves_timeout_semantics_and_sanitization(
    tmp_path,
):
    scenario = (
        InvestigationEngineBenchmarkScenario.REASONER_TIMEOUT
    )
    reasoner, controller = dsh_reasoner(
        tmp_path,
        scenario=scenario,
    )
    current = initial_state(
        scenario
    )

    with pytest.raises(
        DshInvestigationReasonerTimeoutError,
        match="timed out",
    ) as captured:
        await reasoner.decide(
            current.scope,
            current,
        )

    assert isinstance(
        captured.value,
        TimeoutError,
    )
    assert not isinstance(
        captured.value,
        InvestigationReasonerError,
    )
    assert SECRET not in str(
        captured.value
    )
    assert controller.calls == 1
    assert controller.closed_runtimes == 1
