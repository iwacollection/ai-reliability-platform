from __future__ import annotations

from datetime import UTC, datetime

from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (
    BenchmarkScenario,
    _BenchmarkMonotonicClock,
    score_scenario,
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
    LLMInvestigationReasoner,
)


NOW = datetime(
    2026,
    8,
    10,
    13,
    30,
    tzinfo=UTC,
)


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodOOMKilled",
        alert_message="payment-api restarted",
        resource="payment-api",
        namespace="payment",
        cluster="benchmark-lab",
    )


def trusted(
    evidence_id: str,
    probe: InvestigationProbe,
    facts=None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        probe=probe,
        source=(
            "kubernetes"
            if probe.value.startswith(
                "kubernetes_"
            )
            else "prometheus"
        ),
        success=True,
        trusted=True,
        production_signal=True,
        reliability=1.0,
        observed_at=NOW,
        facts=(
            facts
            or {
                "observed": True,
            }
        ),
    )


def hypothesis(
    *,
    cause: str,
    confidence: float,
    supporting=None,
    conflicting=None,
    missing=None,
    optional=None,
) -> IncidentHypothesis:
    return IncidentHypothesis(
        hypothesis_id="h1",
        cause=cause,
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
        optional_evidence=(
            optional
            or []
        ),
    )


def test_hypothesis_supports_blocking_and_optional_evidence():
    value = hypothesis(
        cause="application panic due to invalid configuration",
        confidence=0.9,
        supporting=[
            "log-1"
        ],
        missing=[],
        optional=[
            "restart count for frequency corroboration"
        ],
    )

    assert value.missing_evidence == []
    assert value.optional_evidence == [
        "restart count for frequency corroboration"
    ]


def test_optional_evidence_does_not_block_supported_conclusion():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            trusted(
                "log-1",
                InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,
            )
        ],
    )

    decision = InvestigationDecision(
        hypotheses=[
            hypothesis(
                cause=(
                    "application panic due to invalid configuration"
                ),
                confidence=0.9,
                supporting=[
                    "log-1"
                ],
                missing=[],
                optional=[
                    "prometheus restart count"
                ],
            )
        ],
        rationale_summary=(
            "panic log directly establishes the startup failure"
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
        ),
        conclusion=InvestigationConclusion(
            root_cause=(
                "application panic due to invalid configuration"
            ),
            confidence=0.9,
            evidence_ids=[
                "log-1"
            ],
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
    assert result.code is None


def test_blocking_missing_evidence_still_blocks_conclusion():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            trusted(
                "log-1",
                InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,
            )
        ],
    )

    decision = InvestigationDecision(
        hypotheses=[
            hypothesis(
                cause=(
                    "application panic due to invalid configuration"
                ),
                confidence=0.9,
                supporting=[
                    "log-1"
                ],
                missing=[
                    "required configuration source"
                ],
                optional=[
                    "restart count"
                ],
            )
        ],
        rationale_summary=(
            "root cause still requires blocking evidence"
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
        ),
        conclusion=InvestigationConclusion(
            root_cause=(
                "application panic due to invalid configuration"
            ),
            confidence=0.9,
            evidence_ids=[
                "log-1"
            ],
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
        == "SupportedHypothesisStillMissingEvidence"
    )


def test_reasoner_prompt_contains_optional_evidence_contract():
    value = scope()

    prompt = (
        LLMInvestigationReasoner
        ._build_prompt(
            scope=value,
            state=InvestigationState(
                scope=value
            ),
        )
    )

    assert (
        "Use hypothesis.missing_evidence only for evidence that is REQUIRED"
        in prompt
    )

    assert (
        "hypothesis.optional_evidence"
        in prompt
    )

    assert (
        "optional_evidence may remain non-empty"
        in prompt
    )


def test_reasoner_prompt_contains_point_sample_temporal_causality_contract():
    value = scope()

    prompt = (
        LLMInvestigationReasoner
        ._build_prompt(
            scope=value,
            state=InvestigationState(
                scope=value
            ),
        )
    )

    assert (
        "OOMKilled proves that an OOM termination occurred"
        in prompt
    )

    assert (
        "does not by itself prove that a configured container memory limit was exceeded"
        in prompt
    )

    assert (
        "point-in-time or sampled metric cannot establish an unobserved transient peak"
        in prompt
    )

    assert (
        "sample is not positive support for the claim that the limit was exceeded"
        in prompt
    )


def test_benchmark_logical_clock_does_not_follow_wall_clock():
    clock = _BenchmarkMonotonicClock(
        step_seconds=0.001
    )

    values = [
        clock()
        for _ in range(
            6
        )
    ]

    assert values == [
        0.0,
        0.001,
        0.002,
        0.003,
        0.004,
        0.005,
    ]


def test_benchmark_trace_schema_accepts_optional_evidence_state():
    scenario = BenchmarkScenario(
        key="optional-trace",
        title="optional-trace",
        alert_name="PodRestartHigh",
        alert_message="restart",
        evidence_by_probe={},
        hidden_expected_stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
    )

    decision = InvestigationDecision(
        hypotheses=[
            hypothesis(
                cause="unresolved startup failure",
                confidence=0.4,
                missing=[
                    "previous container logs"
                ],
                optional=[
                    "restart count for frequency only"
                ],
            )
        ],
        rationale_summary=(
            "causal evidence remains unavailable"
        ),
        stop=True,
        stop_reason=(
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
    )

    score = score_scenario(
        scenario=scenario,
        state=state,
        decisions=[
            decision
        ],
    )

    assert (
        score.decision_trace[
            0
        ][
            "hypotheses"
        ][
            0
        ][
            "optional_evidence"
        ]
        == [
            "restart count for frequency only"
        ]
    )
