from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (
    BenchmarkProbeExecutor,
    benchmark_evidence_id,
    run_scenario,
)
from services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (
    CHANGE_SCENARIOS,
    CHANGE_SCENARIO_KEYS,
    SCENARIOS,
    scenario_by_key,
    scenarios_for_mode,
)
from services.agent_runtime.app.investigation.models import (
    IncidentHypothesis,
    InvestigationConclusion,
    InvestigationDecision,
    InvestigationLimits,
    InvestigationProbe,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)


NOW = datetime(
    2026,
    8,
    11,
    0,
    30,
    tzinfo=UTC,
)


class ScriptedReasoner(
    BaseInvestigationReasoner
):
    def __init__(
        self,
        decisions,
    ) -> None:
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
    *,
    cause: str,
    supporting=None,
    missing=None,
    confidence=0.8,
):
    return IncidentHypothesis(
        hypothesis_id="h1",
        cause=cause,
        confidence=confidence,
        supporting_evidence_ids=(
            supporting
            or []
        ),
        conflicting_evidence_ids=[],
        missing_evidence=(
            missing
            or []
        ),
        optional_evidence=[],
    )


def continue_with(
    probe: InvestigationProbe,
    *,
    supporting=None,
    missing=None,
):
    return InvestigationDecision(
        hypotheses=[
            hypothesis(
                cause=(
                    "candidate workload change cause"
                ),
                supporting=supporting,
                missing=missing,
                confidence=0.5,
            )
        ],
        rationale_summary=(
            "collect the next discriminative evidence"
        ),
        stop=False,
        stop_reason=None,
        next_probe=probe,
        conclusion=None,
    )


def sufficient(
    *,
    cause: str,
    evidence_ids,
):
    return InvestigationDecision(
        hypotheses=[
            hypothesis(
                cause=cause,
                supporting=list(
                    evidence_ids
                ),
                missing=[],
                confidence=0.9,
            )
        ],
        rationale_summary=(
            "trusted evidence is sufficient"
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.SUFFICIENT_EVIDENCE
        ),
        next_probe=None,
        conclusion=InvestigationConclusion(
            root_cause=cause,
            confidence=0.9,
            evidence_ids=list(
                evidence_ids
            ),
            remaining_uncertainties=[],
        ),
    )


def test_historical_full_and_smoke_sets_are_unchanged():
    assert len(
        SCENARIOS
    ) == 7

    assert len(
        scenarios_for_mode(
            "full"
        )
    ) == 7

    assert len(
        scenarios_for_mode(
            "smoke"
        )
    ) == 3


def test_change_suite_is_separate_and_addressable_by_key():
    assert len(
        CHANGE_SCENARIOS
    ) == 5

    assert len(
        CHANGE_SCENARIO_KEYS
    ) == 5

    assert {
        item.key
        for item in CHANGE_SCENARIOS
    } == {
        "change_image_rollout_log_rca",
        "recent_change_but_memory_pressure",
        "stale_rollout_crashloop_unknown",
        "change_probe_backend_failure",
        "recent_change_without_causal_mechanism",
    }

    for key in CHANGE_SCENARIO_KEYS:
        assert (
            scenario_by_key(
                key
            ).key
            == key
        )


def test_benchmark_evidence_id_preserves_existing_short_ids():
    expected = (
        "oom_limit_pressure:"
        "kubernetes_pod_state"
    )

    assert benchmark_evidence_id(
        "oom_limit_pressure",
        InvestigationProbe.KUBERNETES_POD_STATE,
    ) == expected


def test_benchmark_evidence_id_compacts_long_change_ids_stably():
    scenario_key = (
        "recent_change_without_causal_mechanism"
    )

    first = benchmark_evidence_id(
        scenario_key,
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
    )

    second = benchmark_evidence_id(
        scenario_key,
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
    )

    raw = (
        f"{scenario_key}:"
        f"{InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE.value}"
    )

    assert len(
        raw
    ) == 65

    assert len(
        first
    ) <= 64

    assert first == second

    assert first != raw

    assert first.startswith(
        "recent_change_without_causal"
    )


def test_change_hidden_labels_do_not_enter_evidence_payloads():
    for scenario in CHANGE_SCENARIOS:
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


def test_benchmark_probe_vocabulary_is_change_gated():
    baseline = BenchmarkProbeExecutor(
        SCENARIOS[
            0
        ],
        observed_at=NOW,
    )

    assert (
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
        not in baseline.available_probes(
            None
        )
    )

    change = BenchmarkProbeExecutor(
        scenario_by_key(
            "change_image_rollout_log_rca"
        ),
        observed_at=NOW,
    )

    assert (
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
        in change.available_probes(
            None
        )
    )


@pytest.mark.asyncio
async def test_change_probe_emits_trusted_change_source():
    scenario = scenario_by_key(
        "change_image_rollout_log_rca"
    )

    executor = BenchmarkProbeExecutor(
        scenario,
        observed_at=NOW,
    )

    evidence = await executor.collect(
        None,
        None,
        (
            InvestigationProbe
            .KUBERNETES_WORKLOAD_CHANGE
        ),
    )

    assert evidence.trusted is True
    assert evidence.source == (
        "kubernetes_change"
    )

    assert evidence.facts[
        "revision_before"
    ] == 6

    assert evidence.facts[
        "revision_after"
    ] == 7

    assert evidence.facts[
        "image_changed"
    ] is True


@pytest.mark.asyncio
async def test_change_only_causal_claim_is_guarded_into_safe_abstention():
    scenario = scenario_by_key(
        "recent_change_without_causal_mechanism"
    )

    change_id = benchmark_evidence_id(
        scenario.key,
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
    )

    reasoner = ScriptedReasoner(
        [
            continue_with(
                InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
            ),
            sufficient(
                cause=(
                    "recent image rollout caused the CrashLoop"
                ),
                evidence_ids=[
                    change_id
                ],
            ),
        ]
    )

    result = await run_scenario(
        reasoner=reasoner,
        scenario=scenario,
        limits=InvestigationLimits(),
        observed_at=NOW,
    )

    assert (
        result.final_stop_reason
        == "insufficient_evidence"
    )

    assert result.outcome_correct is True
    assert result.guard_rescued is True

    assert (
        result.epistemic_guard_code
        == "ChangeEvidenceRequiresIndependentSupport"
    )


@pytest.mark.asyncio
async def test_change_plus_causal_logs_can_form_sufficient_grounded_rca():
    scenario = scenario_by_key(
        "change_image_rollout_log_rca"
    )

    change_id = benchmark_evidence_id(
        scenario.key,
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
    )

    logs_id = benchmark_evidence_id(
        scenario.key,
        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,
    )

    reasoner = ScriptedReasoner(
        [
            continue_with(
                InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE,
            ),
            continue_with(
                InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,
                supporting=[
                    change_id
                ],
            ),
            sufficient(
                cause=(
                    "image rollout introduced an incompatible schema and panic"
                ),
                evidence_ids=[
                    change_id,
                    logs_id,
                ],
            ),
        ]
    )

    result = await run_scenario(
        reasoner=reasoner,
        scenario=scenario,
        limits=InvestigationLimits(),
        observed_at=NOW,
    )

    assert (
        result.final_stop_reason
        == "sufficient_evidence"
    )

    assert result.outcome_correct is True
    assert result.grounding_correct is True

    assert (
        result.root_cause_or_abstention_correct
        is True
    )

    assert result.guard_rescued is False
