from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (
    BenchmarkScenario,
    TracingReasoner,
    build_report,
    run_scenario,
)
from services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (
    SCENARIOS,
    scenarios_for_mode,
)
from services.agent_runtime.app.investigation.models import (
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationDecision,
    InvestigationLimits,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStatus,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)


NOW = datetime(
    2026,
    8,
    10,
    8,
    45,
    tzinfo=UTC,
)


class ScriptedReasoner(
    BaseInvestigationReasoner
):
    def __init__(
        self,
        decisions,
    ):
        self.decisions = list(
            decisions
        )

    async def decide(
        self,
        scope,
        state,
    ):
        return self.decisions.pop(
            0
        )


def hypothesis(
    confidence,
    *,
    supporting=None,
    missing=None,
):
    return IncidentHypothesis(
        hypothesis_id="memory",
        cause=(
            "Container memory limit pressure"
        ),
        confidence=confidence,
        supporting_evidence_ids=(
            supporting
            or []
        ),
        missing_evidence=(
            missing
            or []
        ),
    )


def test_smoke_mode_contains_three_scenarios():
    scenarios = scenarios_for_mode(
        "smoke"
    )

    assert len(
        scenarios
    ) == 3

    assert {
        item.key
        for item in scenarios
    } == {
        "oom_limit_pressure",
        "crashloop_not_memory",
        "conflicting_oom_signal",
    }


def test_hidden_labels_are_not_in_evidence_payloads():
    for scenario in SCENARIOS:
        serialized = str(
            scenario.evidence_by_probe
        ).lower()

        assert (
            "hidden_expected"
            not in serialized
        )

        assert (
            "hidden_root_cause"
            not in serialized
        )


def test_tracing_reasoner_is_transparent():
    decision = InvestigationDecision(
        hypotheses=[
            hypothesis(
                0.3
            )
        ],
        rationale_summary=(
            "inspect pod"
        ),
        next_probe=(
            InvestigationProbe.KUBERNETES_POD_STATE
        ),
    )

    delegate = ScriptedReasoner(
        [
            decision
        ]
    )

    tracing = TracingReasoner(
        delegate
    )

    scope = InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message="Pod restarted",
        resource="payment-api",
        namespace="payment",
        cluster="benchmark-lab",
    )

    state = InvestigationState(
        scope=scope
    )

    result = asyncio.run(
        tracing.decide(
            scope,
            state,
        )
    )

    assert result == decision
    assert tracing.decisions == [
        decision
    ]


def test_clear_oom_scenario_scores_high_with_correct_reasoning():
    scenario = next(
        item
        for item in SCENARIOS
        if item.key
        == "oom_limit_pressure"
    )

    pod_id = (
        f"{scenario.key}:"
        f"{InvestigationProbe.KUBERNETES_POD_STATE.value}"
    )

    limit_id = (
        f"{scenario.key}:"
        f"{InvestigationProbe.PROMETHEUS_MEMORY_LIMIT.value}"
    )

    reasoner = ScriptedReasoner(
        [
            InvestigationDecision(
                hypotheses=[
                    hypothesis(
                        0.4,
                        missing=[
                            "pod state"
                        ],
                    )
                ],
                rationale_summary=(
                    "inspect pod"
                ),
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            ),
            InvestigationDecision(
                hypotheses=[
                    hypothesis(
                        0.75,
                        supporting=[
                            pod_id
                        ],
                        missing=[
                            "memory limit"
                        ],
                    )
                ],
                rationale_summary=(
                    "oom found; inspect limit"
                ),
                next_probe=(
                    InvestigationProbe.PROMETHEUS_MEMORY_LIMIT
                ),
            ),
            InvestigationDecision(
                hypotheses=[
                    hypothesis(
                        0.95,
                        supporting=[
                            pod_id,
                            limit_id,
                        ],
                    )
                ],
                rationale_summary=(
                    "evidence sufficient"
                ),
                stop=True,
                stop_reason=(
                    InvestigationStopReason.SUFFICIENT_EVIDENCE
                ),
                conclusion=(
                    InvestigationConclusion(
                        root_cause=(
                            "Container memory limit pressure caused OOM"
                        ),
                        confidence=0.95,
                        evidence_ids=[
                            pod_id,
                            limit_id,
                        ],
                    )
                ),
            ),
        ]
    )

    score = asyncio.run(
        run_scenario(
            reasoner=reasoner,
            scenario=scenario,
            limits=InvestigationLimits(
                max_iterations=5,
                max_tool_calls=4,
                timeout_seconds=10,
            ),
            observed_at=NOW,
        )
    )

    assert score.score >= 90
    assert score.outcome_correct is True
    assert score.grounding_correct is True


def test_abstention_scenario_penalizes_fabricated_rca():
    scenario = next(
        item
        for item in SCENARIOS
        if item.key
        == "crashloop_not_memory"
    )

    pod_id = (
        f"{scenario.key}:"
        f"{InvestigationProbe.KUBERNETES_POD_STATE.value}"
    )

    reasoner = ScriptedReasoner(
        [
            InvestigationDecision(
                hypotheses=[
                    hypothesis(
                        0.95
                    )
                ],
                rationale_summary=(
                    "guess root cause"
                ),
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            ),
            InvestigationDecision(
                hypotheses=[
                    hypothesis(
                        0.95,
                        supporting=[
                            pod_id
                        ],
                    )
                ],
                rationale_summary=(
                    "incorrectly stop"
                ),
                stop=True,
                stop_reason=(
                    InvestigationStopReason.SUFFICIENT_EVIDENCE
                ),
                conclusion=(
                    InvestigationConclusion(
                        root_cause=(
                            "Memory limit caused restart"
                        ),
                        confidence=0.95,
                        evidence_ids=[
                            pod_id
                        ],
                    )
                ),
            ),
        ]
    )

    score = asyncio.run(
        run_scenario(
            reasoner=reasoner,
            scenario=scenario,
            limits=InvestigationLimits(
                max_iterations=5,
                max_tool_calls=4,
                timeout_seconds=10,
            ),
            observed_at=NOW,
        )
    )

    assert score.outcome_correct is False
    assert (
        score.root_cause_or_abstention_correct
        is False
    )
    assert score.score < 70


def test_build_report_aggregates_scenarios():
    scenario = BenchmarkScenario(
        key="unit",
        title="unit",
        alert_name="Unit",
        alert_message="Unit",
        evidence_by_probe={},
        hidden_expected_stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
    )

    reasoner = ScriptedReasoner(
        [
            InvestigationDecision(
                hypotheses=[
                    hypothesis(
                        0.1
                    )
                ],
                rationale_summary=(
                    "insufficient"
                ),
                stop=True,
                stop_reason=(
                    InvestigationStopReason.INSUFFICIENT_EVIDENCE
                ),
            )
        ]
    )

    score = asyncio.run(
        run_scenario(
            reasoner=reasoner,
            scenario=scenario,
            limits=InvestigationLimits(
                max_iterations=3,
                max_tool_calls=2,
                timeout_seconds=10,
            ),
            observed_at=NOW,
        )
    )

    report = build_report(
        provider="unit",
        mode="unit",
        scenarios=[
            score
        ],
    )

    assert report.scenario_count == 1
    assert report.outcome_accuracy == 100.0
    assert report.overall_score >= 90


def test_failed_reasoner_is_not_counted_as_abstention():
    scenario = BenchmarkScenario(
        key="failed-abstention",
        title="failed-abstention",
        alert_name="PodRestartHigh",
        alert_message="restart",
        evidence_by_probe={},
        hidden_expected_stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
    )

    state = InvestigationState(
        status=InvestigationStatus.FAILED,
        scope=InvestigationScope(
            alert_name="PodRestartHigh",
            alert_message="restart",
            resource="payment-api",
            namespace="payment",
            cluster="benchmark-lab",
        ),
        stop_reason=InvestigationStopReason.REASONER_ERROR,
        failure_code="InvestigationReasonerError",
    )

    from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (
        score_scenario,
    )

    score = score_scenario(
        scenario=scenario,
        state=state,
        decisions=[],
    )

    assert score.outcome_correct is False
    assert score.grounding_correct is False
    assert score.root_cause_or_abstention_correct is False
    assert score.failure_code == "InvestigationReasonerError"

    report = build_report(
        provider="unit",
        mode="unit",
        scenarios=[score],
    )

    assert report.abstention_accuracy == 0.0
    assert report.outcome_accuracy == 0.0
