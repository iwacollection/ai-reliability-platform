from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (
    BenchmarkProbeExecutor,
    benchmark_evidence_id,
    run_scenario,
)
from services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (
    CHANGE_ROLLOUT_EVENT_SCENARIOS,
    CHANGE_ROLLOUT_EVENT_SCENARIO_KEYS,
    CHANGE_SCENARIOS,
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
):
    return InvestigationDecision(
        hypotheses=[
            hypothesis(
                cause=(
                    "candidate cause"
                ),
                supporting=(
                    supporting
                    or []
                ),
                missing=[
                    "one discriminative evidence source"
                ],
                confidence=0.5,
            )
        ],
        rationale_summary=(
            "collect discriminative evidence"
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
            "trusted independent evidence supports the conclusion"
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


def test_historical_benchmark_sets_remain_unchanged():
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

    assert len(
        CHANGE_SCENARIOS
    ) == 5


def test_rollout_event_suite_is_separate_and_addressable():
    assert len(
        CHANGE_ROLLOUT_EVENT_SCENARIOS
    ) == 3

    assert len(
        CHANGE_ROLLOUT_EVENT_SCENARIO_KEYS
    ) == 3

    assert {
        item.key
        for item
        in CHANGE_ROLLOUT_EVENT_SCENARIOS
    } == {
        "rollout_failure_events_log_rca",
        "normal_rollout_events_dependency_rca",
        "event_rbac_unavailable_core_change_rca",
    }

    for key in (
        CHANGE_ROLLOUT_EVENT_SCENARIO_KEYS
    ):
        assert (
            scenario_by_key(
                key
            ).key
            == key
        )


@pytest.mark.asyncio
async def test_rollout_event_change_facts_respect_production_contract():
    for scenario in (
        CHANGE_ROLLOUT_EVENT_SCENARIOS
    ):
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

        assert evidence.source == (
            "kubernetes_change"
        )

        assert evidence.trusted is True

        assert len(
            evidence.facts
        ) <= 32

        assert (
            "rollout_condition_summary"
            in evidence.facts
        )

        assert (
            "events_status"
            in evidence.facts
        )


def test_failed_rollout_scenario_contains_discriminative_warning_signals():
    scenario = scenario_by_key(
        "rollout_failure_events_log_rca"
    )

    facts = scenario.evidence_by_probe[
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
    ]

    assert facts[
        "rollout_failure_signal"
    ] is True

    assert (
        "ProgressDeadlineExceeded"
        in facts[
            "rollout_failure_reason"
        ]
    )

    assert facts[
        "recent_warning_count"
    ] == 2

    assert (
        "FailedCreate"
        in facts[
            "recent_event_reasons"
        ]
    )


def test_normal_event_scenario_explicitly_marks_rollout_healthy():
    scenario = scenario_by_key(
        "normal_rollout_events_dependency_rca"
    )

    facts = scenario.evidence_by_probe[
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
    ]

    assert facts[
        "rollout_failure_signal"
    ] is False

    assert facts[
        "rollout_complete"
    ] is True

    assert facts[
        "recent_warning_count"
    ] == 0

    assert (
        "ScalingReplicaSet"
        in facts[
            "recent_event_reasons"
        ]
    )


def test_event_rbac_scenario_retains_core_change_facts():
    scenario = scenario_by_key(
        "event_rbac_unavailable_core_change_rca"
    )

    facts = scenario.evidence_by_probe[
        InvestigationProbe.KUBERNETES_WORKLOAD_CHANGE
    ]

    assert facts[
        "revision_before"
    ] == 41

    assert facts[
        "revision_after"
    ] == 42

    assert facts[
        "events_status"
    ] == "unavailable"

    assert facts[
        "events_error_code"
    ] == "authorization_denied"

    assert facts[
        "rollout_failure_signal"
    ] is True


@pytest.mark.asyncio
async def test_failed_rollout_plus_logs_scores_as_grounded_change_rca():
    scenario = scenario_by_key(
        "rollout_failure_events_log_rca"
    )

    change_id = benchmark_evidence_id(
        scenario.key,
        (
            InvestigationProbe
            .KUBERNETES_WORKLOAD_CHANGE
        ),
    )

    logs_id = benchmark_evidence_id(
        scenario.key,
        (
            InvestigationProbe
            .KUBERNETES_PREVIOUS_CONTAINER_LOGS
        ),
    )

    reasoner = ScriptedReasoner(
        [
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_WORKLOAD_CHANGE
                ),
            ),
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS
                ),
                supporting=[
                    change_id
                ],
            ),
            sufficient(
                cause=(
                    "image rollout introduced an incompatible schema panic"
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

    assert result.outcome_correct is True
    assert result.grounding_correct is True

    assert (
        result.root_cause_or_abstention_correct
        is True
    )


@pytest.mark.asyncio
async def test_healthy_rollout_plus_dependency_logs_scores_dependency_rca():
    scenario = scenario_by_key(
        "normal_rollout_events_dependency_rca"
    )

    change_id = benchmark_evidence_id(
        scenario.key,
        (
            InvestigationProbe
            .KUBERNETES_WORKLOAD_CHANGE
        ),
    )

    logs_id = benchmark_evidence_id(
        scenario.key,
        (
            InvestigationProbe
            .KUBERNETES_PREVIOUS_CONTAINER_LOGS
        ),
    )

    reasoner = ScriptedReasoner(
        [
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_WORKLOAD_CHANGE
                ),
            ),
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS
                ),
                supporting=[
                    change_id
                ],
            ),
            sufficient(
                cause=(
                    "orders-db dependency is unavailable and connection is refused"
                ),
                evidence_ids=[
                    logs_id
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

    assert result.outcome_correct is True

    assert (
        result.root_cause_or_abstention_correct
        is True
    )

    assert result.guard_rescued is False


@pytest.mark.asyncio
async def test_event_rbac_loss_does_not_block_change_plus_logs_rca():
    scenario = scenario_by_key(
        "event_rbac_unavailable_core_change_rca"
    )

    change_id = benchmark_evidence_id(
        scenario.key,
        (
            InvestigationProbe
            .KUBERNETES_WORKLOAD_CHANGE
        ),
    )

    logs_id = benchmark_evidence_id(
        scenario.key,
        (
            InvestigationProbe
            .KUBERNETES_PREVIOUS_CONTAINER_LOGS
        ),
    )

    reasoner = ScriptedReasoner(
        [
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_WORKLOAD_CHANGE
                ),
            ),
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS
                ),
                supporting=[
                    change_id
                ],
            ),
            sufficient(
                cause=(
                    "image rollout requires a missing configuration key and panics"
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

    assert result.outcome_correct is True
    assert result.grounding_correct is True

    assert (
        result.root_cause_or_abstention_correct
        is True
    )
