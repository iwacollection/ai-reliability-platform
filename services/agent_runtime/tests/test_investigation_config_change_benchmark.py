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
    CHANGE_SCENARIOS,
    CONFIG_CHANGE_SCENARIOS,
    CONFIG_CHANGE_SCENARIO_KEYS,
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
                cause="candidate cause",
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


def abstain() -> InvestigationDecision:
    return InvestigationDecision(
        hypotheses=[
            hypothesis(
                cause=(
                    "configuration history remains unresolved"
                ),
                supporting=[],
                missing=[
                    "historical configuration value/change evidence",
                    "runtime mechanism evidence",
                ],
                confidence=0.3,
            )
        ],
        rationale_summary=(
            "current ConfigMap metadata cannot establish historical change"
        ),
        stop=True,
        stop_reason=(
            InvestigationStopReason.INSUFFICIENT_EVIDENCE
        ),
        next_probe=None,
        conclusion=None,
    )


def test_historical_suites_remain_unchanged():
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

    assert len(
        CHANGE_ROLLOUT_EVENT_SCENARIOS
    ) == 3


def test_config_suite_is_separate_and_addressable():
    assert len(
        CONFIG_CHANGE_SCENARIOS
    ) == 4

    assert len(
        CONFIG_CHANGE_SCENARIO_KEYS
    ) == 4

    assert {
        item.key
        for item in CONFIG_CHANGE_SCENARIOS
    } == {
        "config_ref_change_log_rca",
        "config_change_dependency_red_herring",
        "same_name_configmap_history_unknown",
        "configmap_metadata_rbac_degraded_rca",
    }

    for key in (
        CONFIG_CHANGE_SCENARIO_KEYS
    ):
        assert (
            scenario_by_key(
                key
            ).key
            == key
        )


@pytest.mark.asyncio
async def test_config_probe_is_exposed_only_to_config_scenarios():
    baseline_executor = BenchmarkProbeExecutor(
        SCENARIOS[
            0
        ],
        observed_at=NOW,
    )

    assert (
        InvestigationProbe.KUBERNETES_CONFIG_CHANGE
        not in baseline_executor.available_probes(
            None
        )
    )

    change_executor = BenchmarkProbeExecutor(
        CHANGE_SCENARIOS[
            0
        ],
        observed_at=NOW,
    )

    assert (
        InvestigationProbe.KUBERNETES_CONFIG_CHANGE
        not in change_executor.available_probes(
            None
        )
    )

    config_executor = BenchmarkProbeExecutor(
        CONFIG_CHANGE_SCENARIOS[
            0
        ],
        observed_at=NOW,
    )

    assert (
        InvestigationProbe.KUBERNETES_CONFIG_CHANGE
        in config_executor.available_probes(
            None
        )
    )


@pytest.mark.asyncio
async def test_config_synthetic_evidence_respects_domain_contract():
    for scenario in (
        CONFIG_CHANGE_SCENARIOS
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
                .KUBERNETES_CONFIG_CHANGE
            ),
        )

        assert evidence.source == (
            "kubernetes_change"
        )

        assert evidence.trusted is True

        assert len(
            evidence.facts
        ) <= 32

        assert evidence.facts[
            "secret_content_queried"
        ] is False

        assert evidence.facts[
            "configmap_content_exposed"
        ] is False


def test_same_name_configmap_scenario_has_no_historical_change_proof():
    scenario = scenario_by_key(
        "same_name_configmap_history_unknown"
    )

    facts = scenario.evidence_by_probe[
        InvestigationProbe.KUBERNETES_CONFIG_CHANGE
    ]

    assert facts[
        "configmap_refs_changed"
    ] is False

    assert facts[
        "config_annotation_changed"
    ] is None

    assert facts[
        "current_configmap_metadata_status"
    ] == "complete"

    assert (
        "rv=14001"
        in facts[
            "current_configmap_metadata_summary"
        ]
    )


def test_configmap_rbac_scenario_retains_template_delta():
    scenario = scenario_by_key(
        "configmap_metadata_rbac_degraded_rca"
    )

    facts = scenario.evidence_by_probe[
        InvestigationProbe.KUBERNETES_CONFIG_CHANGE
    ]

    assert facts[
        "configmap_refs_changed"
    ] is True

    assert facts[
        "config_annotation_changed"
    ] is True

    assert facts[
        "current_configmap_metadata_status"
    ] == "unavailable"

    assert (
        "authorization_denied"
        in facts[
            "current_configmap_metadata_error"
        ]
    )


@pytest.mark.asyncio
async def test_config_change_plus_logs_scores_grounded_config_rca():
    scenario = scenario_by_key(
        "config_ref_change_log_rca"
    )

    config_id = benchmark_evidence_id(
        scenario.key,
        (
            InvestigationProbe
            .KUBERNETES_CONFIG_CHANGE
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
                    .KUBERNETES_CONFIG_CHANGE
                ),
            ),
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS
                ),
                supporting=[
                    config_id
                ],
            ),
            sufficient(
                cause=(
                    "configuration rollout removed required FEATURE_MODE"
                ),
                evidence_ids=[
                    config_id,
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
async def test_config_red_herring_scores_dependency_rca():
    scenario = scenario_by_key(
        "config_change_dependency_red_herring"
    )

    config_id = benchmark_evidence_id(
        scenario.key,
        (
            InvestigationProbe
            .KUBERNETES_CONFIG_CHANGE
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
                    .KUBERNETES_CONFIG_CHANGE
                ),
            ),
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS
                ),
                supporting=[
                    config_id
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
async def test_same_name_current_resource_version_ends_in_safe_abstention():
    scenario = scenario_by_key(
        "same_name_configmap_history_unknown"
    )

    reasoner = ScriptedReasoner(
        [
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_CONFIG_CHANGE
                ),
            ),
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS
                ),
            ),
            abstain(),
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

    assert (
        result.root_cause_or_abstention_correct
        is True
    )


@pytest.mark.asyncio
async def test_metadata_rbac_loss_does_not_block_template_delta_plus_logs_rca():
    scenario = scenario_by_key(
        "configmap_metadata_rbac_degraded_rca"
    )

    config_id = benchmark_evidence_id(
        scenario.key,
        (
            InvestigationProbe
            .KUBERNETES_CONFIG_CHANGE
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
                    .KUBERNETES_CONFIG_CHANGE
                ),
            ),
            continue_with(
                (
                    InvestigationProbe
                    .KUBERNETES_PREVIOUS_CONTAINER_LOGS
                ),
                supporting=[
                    config_id
                ],
            ),
            sufficient(
                cause=(
                    "configuration rollout introduced invalid FEATURE_MODE"
                ),
                evidence_ids=[
                    config_id,
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
