from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (
    BenchmarkScenario,
    build_report,
    score_scenario,
)
from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.epistemic_guard import (
    EpistemicConclusionGuard,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationDecision,
    InvestigationProbe,
    InvestigationScope,
    InvestigationState,
    InvestigationStatus,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
    LLMInvestigationReasoner,
)


NOW = datetime(
    2026,
    8,
    10,
    10,
    0,
    tzinfo=UTC,
)


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message="pod restarted",
        resource="payment-api",
        namespace="payment",
        cluster="benchmark-lab",
    )


def trusted(
    evidence_id: str,
    probe: InvestigationProbe,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        probe=probe,
        source=(
            "kubernetes"
            if probe
            == InvestigationProbe.KUBERNETES_POD_STATE
            else "prometheus"
        ),
        success=True,
        trusted=True,
        production_signal=True,
        reliability=1.0,
        observed_at=NOW,
        facts={
            "value_sum": 1.0,
            "oom_killed": True,
        },
    )


def hypothesis(
    *,
    confidence: float,
    supporting=None,
    conflicting=None,
    missing=None,
) -> IncidentHypothesis:
    return IncidentHypothesis(
        hypothesis_id="h1",
        cause="memory limit exhaustion",
        confidence=confidence,
        supporting_evidence_ids=(
            supporting
            or []
        ),
        conflicting_evidence_ids=(
            conflicting
            or []
        ),
        missing_evidence=(
            missing
            or []
        ),
    )


def sufficient(
    *,
    confidence: float,
    hypothesis_value: IncidentHypothesis,
    evidence_ids,
    root_cause="memory limit exhaustion",
) -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            hypothesis_value
        ],
        rationale_summary="evidence sufficient",
        stop=True,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
        ),
        conclusion=(
            InvestigationConclusion(
                root_cause=root_cause,
                confidence=confidence,
                evidence_ids=list(
                    evidence_ids
                ),
            )
        ),
    )


def test_guard_allows_positive_supported_conclusion():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            trusted(
                "e1",
                InvestigationProbe.KUBERNETES_POD_STATE,
            ),
            trusted(
                "e2",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
            ),
        ],
    )

    decision = sufficient(
        confidence=0.9,
        hypothesis_value=hypothesis(
            confidence=0.9,
            supporting=[
                "e1",
                "e2",
            ],
        ),
        evidence_ids=[
            "e1",
            "e2",
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision,
            state=state,
        )
    )

    assert result.allowed is True
    assert result.code is None


def test_guard_rejects_conclusion_built_only_from_conflicting_evidence():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            trusted(
                "e1",
                InvestigationProbe.KUBERNETES_POD_STATE,
            ),
            trusted(
                "e2",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
            ),
        ],
    )

    decision = sufficient(
        confidence=0.9,
        hypothesis_value=hypothesis(
            confidence=0.1,
            conflicting=[
                "e1",
                "e2",
            ],
        ),
        evidence_ids=[
            "e1",
            "e2",
        ],
        root_cause=(
            "unsupported alternative causal claim"
        ),
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision,
            state=state,
        )
    )

    assert result.allowed is False
    assert (
        result.code
        == "NoPositiveHypothesisSupport"
    )


def test_guard_rejects_conclusion_evidence_not_declared_as_positive_support():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            trusted(
                "e1",
                InvestigationProbe.KUBERNETES_POD_STATE,
            ),
            trusted(
                "e2",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
            ),
        ],
    )

    decision = sufficient(
        confidence=0.8,
        hypothesis_value=hypothesis(
            confidence=0.8,
            supporting=[
                "e1",
            ],
        ),
        evidence_ids=[
            "e1",
            "e2",
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision,
            state=state,
        )
    )

    assert result.allowed is False
    assert (
        result.code
        == "ConclusionEvidenceNotPositiveSupport"
    )


def test_guard_rejects_overstated_conclusion_confidence():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            trusted(
                "e1",
                InvestigationProbe.KUBERNETES_POD_STATE,
            ),
        ],
    )

    decision = sufficient(
        confidence=0.9,
        hypothesis_value=hypothesis(
            confidence=0.6,
            supporting=[
                "e1",
            ],
        ),
        evidence_ids=[
            "e1",
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision,
            state=state,
        )
    )

    assert result.allowed is False
    assert (
        result.code
        == "ConclusionConfidenceExceedsSupport"
    )


def test_non_sufficient_terminal_decision_is_untouched():
    state = InvestigationState(
        scope=scope(),
    )

    decision = InvestigationDecision(
        hypotheses=[
            hypothesis(
                confidence=0.2,
                missing=[
                    "application logs"
                ],
            )
        ],
        rationale_summary="insufficient evidence",
        stop=True,
        stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision,
            state=state,
        )
    )

    assert result.allowed is True


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
        scope_value,
        state,
    ):
        return self.decisions.pop(
            0
        )


class ProbeExecutor:
    async def collect(
        self,
        context,
        scope_value,
        probe,
    ):
        return trusted(
            "e1",
            probe,
        )


def context():
    return SimpleNamespace(
        event=SimpleNamespace(
            signal=SimpleNamespace(
                name="PodOOMKilled",
                message="pod restarted",
            ),
            resources=[
                SimpleNamespace(
                    name="payment-api",
                    namespace="payment",
                    cluster="benchmark-lab",
                )
            ],
        ),
        metadata={},
        variables={},
    )


@pytest.mark.asyncio
async def test_coordinator_downgrades_unsupported_rca_to_safe_abstention():
    reasoner = ScriptedReasoner(
        [
            InvestigationDecision(
                hypotheses=[
                    hypothesis(
                        confidence=0.5,
                        missing=[
                            "pod state"
                        ],
                    )
                ],
                rationale_summary="collect pod state",
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            ),
            sufficient(
                confidence=0.9,
                hypothesis_value=hypothesis(
                    confidence=0.1,
                    conflicting=[
                        "e1",
                    ],
                ),
                evidence_ids=[
                    "e1",
                ],
                root_cause=(
                    "unsupported alternative causal claim"
                ),
            ),
        ]
    )

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=reasoner,
            probe_executor=ProbeExecutor(),
            utc_clock=lambda: NOW,
        )
    )

    state = await coordinator.investigate(
        context()
    )

    assert state.status == (
        InvestigationStatus.CONCLUDED
    )

    assert state.stop_reason == (
        InvestigationStopReason.INSUFFICIENT_EVIDENCE
    )

    assert state.conclusion is None

    assert state.epistemic_guard_code == (
        "NoPositiveHypothesisSupport"
    )


@pytest.mark.asyncio
async def test_coordinator_preserves_valid_supported_rca():
    reasoner = ScriptedReasoner(
        [
            InvestigationDecision(
                hypotheses=[
                    hypothesis(
                        confidence=0.5,
                        missing=[
                            "pod state"
                        ],
                    )
                ],
                rationale_summary="collect pod state",
                next_probe=(
                    InvestigationProbe.KUBERNETES_POD_STATE
                ),
            ),
            sufficient(
                confidence=0.9,
                hypothesis_value=hypothesis(
                    confidence=0.9,
                    supporting=[
                        "e1",
                    ],
                ),
                evidence_ids=[
                    "e1",
                ],
            ),
        ]
    )

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=reasoner,
            probe_executor=ProbeExecutor(),
            utc_clock=lambda: NOW,
        )
    )

    state = await coordinator.investigate(
        context()
    )

    assert state.status == (
        InvestigationStatus.CONCLUDED
    )

    assert state.stop_reason == (
        InvestigationStopReason.SUFFICIENT_EVIDENCE
    )

    assert state.conclusion is not None
    assert state.epistemic_guard_code is None


def test_prompt_teaches_positive_support_and_negative_evidence_discipline():
    state = InvestigationState(
        scope=scope(),
    )

    prompt = (
        LLMInvestigationReasoner
        ._build_prompt(
            scope=scope(),
            state=state,
        )
    )

    assert (
        "Conflicting evidence can weaken a hypothesis"
        in prompt
    )

    assert (
        "Ruling out one hypothesis is not sufficient evidence"
        in prompt
    )

    assert (
        "Current-state evidence does not by itself prove"
        in prompt
    )

    assert (
        "positively establish a root cause"
        in prompt
    )


def test_benchmark_guard_rescue_is_visible_and_capped():
    scenario = BenchmarkScenario(
        key="guard-rescue",
        title="guard rescue",
        alert_name="PodOOMKilled",
        alert_message="alert",
        evidence_by_probe={},
        hidden_expected_stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
    )

    state = InvestigationState(
        status=(
            InvestigationStatus.CONCLUDED
        ),
        scope=scope(),
        stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
        epistemic_guard_code=(
            "NoPositiveHypothesisSupport"
        ),
    )

    score = score_scenario(
        scenario=scenario,
        state=state,
        decisions=[],
    )

    assert score.outcome_correct is True
    assert score.guard_rescued is True
    assert score.score == 85.0

    report = build_report(
        provider="unit",
        mode="unit",
        scenarios=[
            score
        ],
    )

    assert report.guard_rescue_count == 1
    assert report.guard_rescue_rate == 100.0
