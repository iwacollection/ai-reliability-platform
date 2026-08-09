from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

import services.agent_runtime.app.runtime.runtime as runtime_module

from common.config.settings import (
    AuthenticationConfig,
)

from services.agent_runtime.app.investigation.coordinator import (
    EvidenceDrivenInvestigationCoordinator,
)
from services.agent_runtime.app.investigation.models import (
    EvidenceItem,
    IncidentHypothesis,
    InvestigationDecision,
    InvestigationLimits,
    InvestigationProbe,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.investigation.settings import (
    InvestigationSettings,
)
from services.agent_runtime.app.security.factory import (
    create_authentication_service,
)
from services.agent_runtime.app.tools.manager import (
    ToolManager,
)
from services.agent_runtime.app.tools.registry import (
    ToolRegistry,
)
from services.agent_runtime.app.verification.collector import (
    VerificationEvaluation,
    VerificationEvidenceCollector,
    VerificationProbe,
)
from services.agent_runtime.app.verification.models import (
    VerificationSource,
)


NOW = datetime(
    2026,
    8,
    11,
    5,
    30,
    tzinfo=UTC,
)

CLUSTER = "prod-us-03"


class StopAfterOneProbeReasoner(
    BaseInvestigationReasoner
):
    def __init__(
        self,
    ) -> None:
        self.calls = 0

    async def decide(
        self,
        scope,
        state,
    ) -> InvestigationDecision:
        self.calls += 1

        hypothesis = IncidentHypothesis(
            hypothesis_id="strict-cluster-policy",
            cause="collect one bounded evidence item",
            confidence=0.1,
            supporting_evidence_ids=[],
            conflicting_evidence_ids=[],
            missing_evidence=[
                "verified production evidence"
            ],
            optional_evidence=[],
        )

        if self.calls == 1:
            return InvestigationDecision(
                hypotheses=[
                    hypothesis
                ],
                rationale_summary=(
                    "collect one Kubernetes read"
                ),
                stop=False,
                next_probe=(
                    InvestigationProbe
                    .KUBERNETES_POD_STATE
                ),
            )

        return InvestigationDecision(
            hypotheses=[
                hypothesis
            ],
            rationale_summary=(
                "stop after evidence admission policy"
            ),
            stop=True,
            stop_reason=(
                InvestigationStopReason
                .INSUFFICIENT_EVIDENCE
            ),
            next_probe=None,
            conclusion=None,
        )


class OneEvidenceExecutor:
    def __init__(
        self,
        evidence: EvidenceItem,
    ) -> None:
        self.evidence = evidence

    async def collect(
        self,
        context,
        scope,
        probe,
    ) -> EvidenceItem:
        return self.evidence


def context():
    return SimpleNamespace(
        event=SimpleNamespace(
            header=SimpleNamespace(
                occurred_at=NOW,
            ),
            signal=SimpleNamespace(
                name="PodRestartHigh",
                message="restart rate elevated",
            ),
            resources=[
                SimpleNamespace(
                    name="device-gateway-xyz789",
                    namespace="fleet-edge",
                    cluster=CLUSTER,
                )
            ],
        ),
        metadata={},
    )


def identityless_evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="identityless-production-evidence",
        probe=(
            InvestigationProbe
            .KUBERNETES_POD_STATE
        ),
        source="kubernetes",
        success=True,
        trusted=True,
        production_signal=True,
        reliability=1.0,
        observed_at=NOW,
        cluster=None,
        cluster_verified=False,
        facts={
            "ready": True,
        },
    )


def verified_evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="verified-production-evidence",
        probe=(
            InvestigationProbe
            .KUBERNETES_POD_STATE
        ),
        source="kubernetes",
        success=True,
        trusted=True,
        production_signal=True,
        reliability=1.0,
        observed_at=NOW,
        cluster=CLUSTER,
        cluster_verified=True,
        facts={
            "ready": True,
        },
    )


@pytest.mark.asyncio
async def test_default_investigation_policy_preserves_identityless_legacy_compatibility():
    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=(
                StopAfterOneProbeReasoner()
            ),
            probe_executor=(
                OneEvidenceExecutor(
                    identityless_evidence()
                )
            ),
            limits=InvestigationLimits(
                max_iterations=3,
                max_tool_calls=2,
                timeout_seconds=10,
            ),
            utc_clock=lambda: NOW,
        )
    )

    result = await coordinator.investigate(
        context()
    )

    admitted = result.evidence[
        0
    ]

    assert admitted.success is True
    assert admitted.trusted is True

    assert (
        admitted.cluster_verified
        is False
    )

    assert admitted.facts == {
        "ready": True,
    }


@pytest.mark.asyncio
async def test_strict_investigation_policy_strips_identityless_production_evidence():
    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=(
                StopAfterOneProbeReasoner()
            ),
            probe_executor=(
                OneEvidenceExecutor(
                    identityless_evidence()
                )
            ),
            limits=InvestigationLimits(
                max_iterations=3,
                max_tool_calls=2,
                timeout_seconds=10,
            ),
            utc_clock=lambda: NOW,
            require_cluster_verified_evidence=True,
        )
    )

    result = await coordinator.investigate(
        context()
    )

    rejected = result.evidence[
        0
    ]

    assert rejected.success is False
    assert rejected.trusted is False

    assert rejected.error_code == (
        "ClusterVerificationRequired"
    )

    assert rejected.cluster is None
    assert rejected.facts == {}


@pytest.mark.asyncio
async def test_strict_investigation_policy_accepts_matching_verified_evidence():
    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=(
                StopAfterOneProbeReasoner()
            ),
            probe_executor=(
                OneEvidenceExecutor(
                    verified_evidence()
                )
            ),
            limits=InvestigationLimits(
                max_iterations=3,
                max_tool_calls=2,
                timeout_seconds=10,
            ),
            utc_clock=lambda: NOW,
            require_cluster_verified_evidence=True,
        )
    )

    result = await coordinator.investigate(
        context()
    )

    admitted = result.evidence[
        0
    ]

    assert admitted.success is True
    assert admitted.trusted is True

    assert (
        admitted.cluster_verified
        is True
    )

    assert admitted.cluster == (
        CLUSTER
    )


class RecordingTools:
    def __init__(
        self,
        result: dict[str, Any],
    ) -> None:
        self.result = result
        self.calls = []

    async def call(
        self,
        name,
        context=None,
        **kwargs,
    ):
        self.calls.append(
            {
                "name": name,
                "kwargs": kwargs,
            }
        )

        return self.result


def verification_result(
    *,
    include_cluster: bool,
) -> dict[str, Any]:
    result = {
        "success": True,
        "source": "prometheus",
        "mode": "read_only",
        "production_signal": True,
        "observed_at": NOW.isoformat(),
        "data": {
            "resultType": "vector",
            "result": [],
        },
    }

    if include_cluster:
        result[
            "cluster"
        ] = CLUSTER

    return result


def verification_probe(
    *,
    required: bool,
    evaluator,
) -> VerificationProbe:
    return VerificationProbe(
        name="restart_metric",
        source=VerificationSource.METRIC,
        tool="prometheus",
        provider="prometheus",
        arguments={
            "query": (
                'up{cluster="prod-us-03"}'
            ),
            "cluster": CLUSTER,
        },
        evaluator=evaluator,
        required=required,
    )


@pytest.mark.asyncio
async def test_strict_verification_rejects_required_identityless_evidence_before_evaluator():
    evaluator_calls = []

    def evaluator(
        evidence,
    ):
        evaluator_calls.append(
            evidence
        )

        return VerificationEvaluation(
            passed=True
        )

    collector = VerificationEvidenceCollector(
        tools=RecordingTools(
            verification_result(
                include_cluster=False
            )
        ),
        clock=lambda: NOW,
        require_cluster_verified_evidence=True,
    )

    check = await collector.collect_one(
        verification_probe(
            required=True,
            evaluator=evaluator,
        )
    )

    assert check.passed is None

    assert (
        "cluster verification is required"
        in check.metadata[
            "rejection_reasons"
        ]
    )

    assert evaluator_calls == []


@pytest.mark.asyncio
async def test_strict_verification_accepts_required_cluster_verified_evidence():
    def evaluator(
        evidence,
    ):
        return VerificationEvaluation(
            passed=True,
            message="verified cluster evidence",
        )

    collector = VerificationEvidenceCollector(
        tools=RecordingTools(
            verification_result(
                include_cluster=True
            )
        ),
        clock=lambda: NOW,
        require_cluster_verified_evidence=True,
    )

    check = await collector.collect_one(
        verification_probe(
            required=True,
            evaluator=evaluator,
        )
    )

    assert check.passed is True

    assert (
        check.metadata[
            "cluster_verified"
        ]
        is True
    )

    assert (
        check.metadata[
            "evidence_cluster"
        ]
        == CLUSTER
    )


@pytest.mark.asyncio
async def test_optional_verification_probe_keeps_identityless_compatibility_in_strict_mode():
    def evaluator(
        evidence,
    ):
        return VerificationEvaluation(
            passed=True,
            message="optional legacy evidence",
        )

    collector = VerificationEvidenceCollector(
        tools=RecordingTools(
            verification_result(
                include_cluster=False
            )
        ),
        clock=lambda: NOW,
        require_cluster_verified_evidence=True,
    )

    check = await collector.collect_one(
        verification_probe(
            required=False,
            evaluator=evaluator,
        )
    )

    assert check.passed is True

    assert (
        check.metadata[
            "cluster_verified"
        ]
        is False
    )


def _runtime_with_registry_presence(
    monkeypatch,
    tmp_path,
    *,
    kubernetes_registry,
    prometheus_registry,
):
    monkeypatch.chdir(
        tmp_path
    )

    fake_coordinator = SimpleNamespace(
        require_cluster_verified_evidence=False
    )

    monkeypatch.setattr(
        runtime_module,
        "create_investigation_coordinator",
        lambda **_: fake_coordinator,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_cluster_registry",
        lambda: kubernetes_registry,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_prometheus_cluster_registry",
        lambda: prometheus_registry,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_tool_manager",
        lambda **_: ToolManager(
            ToolRegistry()
        ),
    )

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_preflight_resolver",
        lambda: None,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_kubernetes_production_executor",
        lambda **_: None,
    )

    monkeypatch.setattr(
        runtime_module,
        "create_production_pilot_live_readiness_probe",
        lambda: None,
    )

    runtime = runtime_module.AgentRuntime(
        authentication_service=(
            create_authentication_service(
                AuthenticationConfig()
            )
        ),
        investigation_settings=(
            InvestigationSettings()
        ),
    )

    return (
        runtime,
        fake_coordinator,
    )


def test_runtime_default_keeps_cluster_verified_policy_disabled(
    monkeypatch,
    tmp_path,
):
    runtime, coordinator = (
        _runtime_with_registry_presence(
            monkeypatch,
            tmp_path,
            kubernetes_registry=None,
            prometheus_registry=None,
        )
    )

    assert (
        runtime.cluster_verified_evidence_required
        is False
    )

    assert (
        coordinator.require_cluster_verified_evidence
        is False
    )

    assert (
        runtime.verification_collector
        .require_cluster_verified_evidence
        is False
    )


@pytest.mark.parametrize(
    (
        "kubernetes_registry",
        "prometheus_registry",
    ),
    [
        (
            object(),
            None,
        ),
        (
            None,
            object(),
        ),
        (
            object(),
            object(),
        ),
    ],
)
def test_runtime_automatically_enables_strict_policy_when_read_registry_exists(
    monkeypatch,
    tmp_path,
    kubernetes_registry,
    prometheus_registry,
):
    runtime, coordinator = (
        _runtime_with_registry_presence(
            monkeypatch,
            tmp_path,
            kubernetes_registry=(
                kubernetes_registry
            ),
            prometheus_registry=(
                prometheus_registry
            ),
        )
    )

    assert (
        runtime.cluster_verified_evidence_required
        is True
    )

    assert (
        coordinator.require_cluster_verified_evidence
        is True
    )

    assert (
        runtime.verification_collector
        .require_cluster_verified_evidence
        is True
    )


def test_strict_policy_components_reject_non_boolean_configuration():
    with pytest.raises(
        TypeError,
        match="Investigation cluster-verified evidence policy",
    ):
        EvidenceDrivenInvestigationCoordinator(
            reasoner=(
                StopAfterOneProbeReasoner()
            ),
            probe_executor=(
                OneEvidenceExecutor(
                    identityless_evidence()
                )
            ),
            require_cluster_verified_evidence=1,
        )

    with pytest.raises(
        TypeError,
        match="Verification cluster-verified evidence policy",
    ):
        VerificationEvidenceCollector(
            tools=ToolManager(
                ToolRegistry()
            ),
            require_cluster_verified_evidence=1,
        )
