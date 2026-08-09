from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from services.agent_runtime.app.evaluation.intelligence_benchmark.engine import (
    BenchmarkProbeExecutor,
)
from services.agent_runtime.app.evaluation.intelligence_benchmark.scenarios import (
    scenario_by_key,
)
from services.agent_runtime.app.investigation.models import (
    InvestigationProbe,
    InvestigationStopReason,
)


NOW = datetime(
    2026,
    8,
    10,
    13,
    30,
    tzinfo=UTC,
)


def test_logs_rca_scenario_is_available_with_hidden_causal_label():
    scenario = scenario_by_key(
        "crashloop_previous_log_rca"
    )

    assert (
        scenario.hidden_expected_stop_reason
        == InvestigationStopReason.SUFFICIENT_EVIDENCE
    )

    assert (
        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS
        in scenario.evidence_by_probe
    )

    assert (
        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS
        in scenario.hidden_required_probes
    )


def test_crashloop_without_logs_explicitly_models_log_unavailability():
    scenario = scenario_by_key(
        "crashloop_not_memory"
    )

    assert (
        scenario.evidence_by_probe[
            InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS
        ]
        == "unavailable"
    )


@pytest.mark.asyncio
async def test_benchmark_log_probe_is_kubernetes_evidence():
    scenario = scenario_by_key(
        "crashloop_previous_log_rca"
    )

    executor = BenchmarkProbeExecutor(
        scenario,
        observed_at=NOW,
    )

    evidence = await executor.collect(
        None,
        None,
        InvestigationProbe.KUBERNETES_PREVIOUS_CONTAINER_LOGS,
    )

    assert evidence.source == "kubernetes"
    assert evidence.trusted is True
    assert evidence.production_signal is True
    assert (
        evidence.facts["temporal_basis"]
        == "previous_container"
    )
    assert (
        "panic: invalid configuration"
        in evidence.facts["log_excerpt"]
    )
    assert "real-password" not in str(
        evidence.model_dump(
            mode="json"
        )
    )
