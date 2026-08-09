from __future__ import annotations

from datetime import UTC, datetime

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
    InvestigationStopReason,
)


NOW = datetime(
    2026,
    8,
    10,
    14,
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


def evidence(
    evidence_id: str,
    probe: InvestigationProbe,
    *,
    value: float | None = None,
    oom_killed: bool | None = None,
) -> EvidenceItem:
    facts = {}

    if value is not None:
        facts["value_sum"] = value

    if oom_killed is not None:
        facts["oom_killed"] = oom_killed

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
        facts=facts,
    )


def decision(
    *,
    supporting_ids,
    conclusion_ids,
) -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            IncidentHypothesis(
                hypothesis_id="h1",
                cause=(
                    "container exceeded memory limit causing OOMKilled"
                ),
                confidence=0.9,
                supporting_evidence_ids=list(
                    supporting_ids
                ),
                conflicting_evidence_ids=[],
                missing_evidence=[],
                optional_evidence=[],
            )
        ],
        rationale_summary=(
            "model proposes a memory-limit threshold mechanism"
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
        ),
        conclusion=InvestigationConclusion(
            root_cause=(
                "container exceeded memory limit causing OOMKilled"
            ),
            confidence=0.9,
            evidence_ids=list(
                conclusion_ids
            ),
        ),
    )


def test_guard_rejects_far_below_limit_even_if_model_omits_metrics_from_support():
    current = InvestigationState(
        scope=scope(),
        evidence=[
            evidence(
                "pod",
                InvestigationProbe.KUBERNETES_POD_STATE,
                oom_killed=True,
            ),
            evidence(
                "working",
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
                value=300_000_000.0,
            ),
            evidence(
                "limit",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
                value=1_073_741_824.0,
            ),
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision(
                supporting_ids=[
                    "pod"
                ],
                conclusion_ids=[
                    "pod"
                ],
            ),
            state=current,
        )
    )

    assert result.allowed is False
    assert (
        result.code
        == "MemoryLimitEvidenceNotNearThreshold"
    )


def test_guard_rejects_near_limit_claim_when_model_omits_mechanism_evidence():
    current = InvestigationState(
        scope=scope(),
        evidence=[
            evidence(
                "pod",
                InvestigationProbe.KUBERNETES_POD_STATE,
                oom_killed=True,
            ),
            evidence(
                "working",
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
                value=530_000_000.0,
            ),
            evidence(
                "limit",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
                value=536_870_912.0,
            ),
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision(
                supporting_ids=[
                    "pod"
                ],
                conclusion_ids=[
                    "pod"
                ],
            ),
            state=current,
        )
    )

    assert result.allowed is False
    assert (
        result.code
        == "MemoryLimitEvidenceNotNearThreshold"
    )


def test_guard_allows_near_limit_claim_when_mechanism_evidence_is_cited():
    current = InvestigationState(
        scope=scope(),
        evidence=[
            evidence(
                "pod",
                InvestigationProbe.KUBERNETES_POD_STATE,
                oom_killed=True,
            ),
            evidence(
                "working",
                InvestigationProbe.PROMETHEUS_MEMORY_WORKING_SET,
                value=530_000_000.0,
            ),
            evidence(
                "limit",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
                value=536_870_912.0,
            ),
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision(
                supporting_ids=[
                    "pod",
                    "working",
                    "limit",
                ],
                conclusion_ids=[
                    "pod",
                    "working",
                    "limit",
                ],
            ),
            state=current,
        )
    )

    assert result.allowed is True
    assert result.code is None


def test_crashloop_logs_scenario_accepts_direct_log_first_path():
    scenario = scenario_by_key(
        "crashloop_previous_log_rca"
    )

    assert (
        scenario.hidden_required_probes
        == [
            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS
        ]
    )

    assert (
        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS
        in scenario.hidden_preferred_first_probes
    )


def test_backend_failure_accepts_previous_logs_as_reasonable_first_probe():
    scenario = scenario_by_key(
        "probe_backend_failure"
    )

    assert (
        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS
        in scenario.hidden_preferred_first_probes
    )


def test_memory_threshold_policy_defers_when_only_pod_state_exists():
    current = InvestigationState(
        scope=scope(),
        evidence=[
            evidence(
                "pod",
                InvestigationProbe.KUBERNETES_POD_STATE,
                oom_killed=True,
            ),
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision(
                supporting_ids=[
                    "pod"
                ],
                conclusion_ids=[
                    "pod"
                ],
            ),
            state=current,
        )
    )

    assert result.allowed is True
    assert result.code is None


def test_memory_threshold_policy_defers_when_numeric_pair_is_incomplete():
    current = InvestigationState(
        scope=scope(),
        evidence=[
            evidence(
                "pod",
                InvestigationProbe.KUBERNETES_POD_STATE,
                oom_killed=True,
            ),
            evidence(
                "limit",
                InvestigationProbe.PROMETHEUS_MEMORY_LIMIT,
                value=536_870_912.0,
            ),
        ],
    )

    result = (
        EpistemicConclusionGuard()
        .evaluate(
            decision=decision(
                supporting_ids=[
                    "pod",
                    "limit",
                ],
                conclusion_ids=[
                    "pod",
                    "limit",
                ],
            ),
            state=current,
        )
    )

    assert result.allowed is True
    assert result.code is None
