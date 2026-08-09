from __future__ import annotations

from datetime import UTC, datetime

from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (
    BenchmarkScenario,
    score_scenario,
)
from services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (
    scenario_by_key,
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
    10,
    45,
    tzinfo=UTC,
)


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message="payment-api restart count is increasing",
        resource="payment-api",
        namespace="payment",
        cluster="benchmark-lab",
    )


def evidence(
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
            "observed": True,
        },
    )


def hypothesis(
    *,
    cause: str,
    confidence: float,
    supporting=None,
    conflicting=None,
    missing=None,
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
    )


def sufficient(
    *,
    hypothesis_value: IncidentHypothesis,
    evidence_ids,
    root_cause: str,
    confidence: float,
) -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            hypothesis_value
        ],
        rationale_summary="candidate root cause",
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


def test_guard_rejects_supported_hypothesis_with_unresolved_missing_evidence():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            evidence(
                "restart",
                InvestigationProbe.PROMETHEUS_RESTART_COUNT,
            ),
            evidence(
                "pod",
                InvestigationProbe.KUBERNETES_POD_STATE,
            ),
        ],
    )

    decision = sufficient(
        hypothesis_value=hypothesis(
            cause=(
                "CrashLoopBackOff due to application panic or misconfiguration"
            ),
            confidence=0.7,
            supporting=[
                "restart",
                "pod",
            ],
            missing=[
                "previous container logs or termination message"
            ],
        ),
        evidence_ids=[
            "restart",
            "pod",
        ],
        root_cause=(
            "CrashLoopBackOff due to application panic or misconfiguration"
        ),
        confidence=0.7,
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


def test_guard_preserves_complete_positive_oom_conclusion():
    state = InvestigationState(
        scope=scope(),
        evidence=[
            evidence(
                "pod",
                InvestigationProbe.KUBERNETES_POD_STATE,
            ),
            evidence(
                "working",
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
            ),
            evidence(
                "limit",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
            ),
        ],
    )

    decision = sufficient(
        hypothesis_value=hypothesis(
            cause="OOMKilled due to memory limit exceeded",
            confidence=0.9,
            supporting=[
                "pod",
                "working",
                "limit",
            ],
            missing=[],
        ),
        evidence_ids=[
            "pod",
            "working",
            "limit",
        ],
        root_cause="OOMKilled due to memory limit exceeded",
        confidence=0.9,
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


def test_prompt_distinguishes_symptom_from_specific_root_cause():
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
        "symptom or failure-mode observation"
        in prompt
    )

    assert (
        "does not by itself establish the specific underlying cause"
        in prompt
    )

    assert (
        "Do not clear missing_evidence merely because all allowed probes have been attempted"
        in prompt
    )

    assert (
        "confirms a symptom/failure mode without establishing its cause"
        in prompt
    )


def test_missing_capability_awareness_does_not_use_guessed_cause_text():
    scenario = BenchmarkScenario(
        key="awareness-negative",
        title="awareness-negative",
        alert_name="PodRestartHigh",
        alert_message="restart",
        evidence_by_probe={},
        hidden_expected_stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
        hidden_missing_capability_keywords=[
            "log",
        ],
    )

    decision = InvestigationDecision(
        hypotheses=[
            hypothesis(
                cause=(
                    "CrashLoopBackOff due to application panic or misconfiguration"
                ),
                confidence=0.4,
                missing=[
                    "memory limit"
                ],
            )
        ],
        rationale_summary=(
            "Application panic remains possible"
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
    )

    state = InvestigationState(
        status=InvestigationStatus.CONCLUDED,
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
        score.missing_capability_awareness
        is False
    )


def test_missing_capability_awareness_requires_explicit_missing_logs():
    scenario = BenchmarkScenario(
        key="awareness-positive",
        title="awareness-positive",
        alert_name="PodRestartHigh",
        alert_message="restart",
        evidence_by_probe={},
        hidden_expected_stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
        hidden_missing_capability_keywords=[
            "log",
        ],
    )

    decision = InvestigationDecision(
        hypotheses=[
            hypothesis(
                cause=(
                    "CrashLoopBackOff has an unresolved underlying cause"
                ),
                confidence=0.4,
                missing=[
                    "previous container logs"
                ],
            )
        ],
        rationale_summary=(
            "Current bounded probes cannot identify the application failure."
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
    )

    state = InvestigationState(
        status=InvestigationStatus.CONCLUDED,
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
        score.missing_capability_awareness
        is True
    )


def test_crashloop_hidden_labels_no_longer_accept_application_word():
    scenario = scenario_by_key(
        "crashloop_not_memory"
    )

    normalized = {
        item.lower()
        for item
        in scenario.hidden_missing_capability_keywords
    }

    assert "application" not in normalized
    assert "应用" not in normalized
    assert "log" in normalized
