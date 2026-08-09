from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from services.agent_runtime.app.action.models import (
    ActionPlan,
    ActionRisk,
    ActionType,
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
    InvestigationScope,
    InvestigationStopReason,
)
from services.agent_runtime.app.investigation.probes import (
    InvestigationProbeResponseError,
    ReadOnlyInvestigationProbeExecutor,
)
from services.agent_runtime.app.investigation.reasoner import (
    BaseInvestigationReasoner,
)
from services.agent_runtime.app.verification.collector import (
    VerificationEvaluation,
    VerificationEvidenceCollector,
    VerificationProbe,
)
from services.agent_runtime.app.verification.models import (
    VerificationSource,
)
from services.agent_runtime.app.verification.profiles import (
    VerificationProfileFactory,
)


NOW = datetime(
    2026,
    8,
    11,
    5,
    0,
    tzinfo=UTC,
)

INCIDENT_CLUSTER = "prod-us-03"
WRONG_CLUSTER = "prod-sg-17"


def scope() -> InvestigationScope:
    return InvestigationScope(
        alert_name="PodRestartHigh",
        alert_message="device gateway restart rate is elevated",
        event_occurred_at=NOW,
        resource="device-gateway-xyz789",
        namespace="fleet-edge",
        cluster=INCIDENT_CLUSTER,
    )


def kubernetes_result(
    *,
    cluster: str | None = INCIDENT_CLUSTER,
) -> dict[str, Any]:
    result = {
        "success": True,
        "source": "kubernetes",
        "mode": "read_only",
        "production_signal": True,
        "observed_at": NOW.isoformat(),
        "data": {
            "phase": "Running",
            "ready": True,
            "scheduled": True,
            "oom_killed": False,
            "containers": [],
        },
    }

    if cluster is not None:
        result[
            "cluster"
        ] = cluster

    return result


def prometheus_result(
    *,
    cluster: str | None = INCIDENT_CLUSTER,
) -> dict[str, Any]:
    result = {
        "success": True,
        "source": "prometheus",
        "mode": "read_only",
        "production_signal": True,
        "observed_at": NOW.isoformat(),
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {},
                    "value": [
                        NOW.timestamp(),
                        "7",
                    ],
                }
            ],
        },
    }

    if cluster is not None:
        result[
            "cluster"
        ] = cluster

    return result


def test_matching_kubernetes_and_prometheus_evidence_are_cluster_verified():
    executor = (
        ReadOnlyInvestigationProbeExecutor()
    )

    kubernetes = (
        executor._normalize_kubernetes(
            scope=scope(),
            probe=(
                InvestigationProbe
                .KUBERNETES_POD_STATE
            ),
            result=kubernetes_result(),
        )
    )

    prometheus = (
        executor._normalize_prometheus(
            scope=scope(),
            probe=(
                InvestigationProbe
                .PROMETHEUS_RESTART_COUNT
            ),
            result=prometheus_result(),
        )
    )

    assert (
        kubernetes.cluster
        == INCIDENT_CLUSTER
    )

    assert (
        prometheus.cluster
        == INCIDENT_CLUSTER
    )

    assert (
        kubernetes.cluster_verified
        is True
    )

    assert (
        prometheus.cluster_verified
        is True
    )

    assert (
        kubernetes.cluster
        == prometheus.cluster
        == scope().cluster
    )


@pytest.mark.parametrize(
    ("normalizer_name", "probe", "result"),
    [
        (
            "_normalize_kubernetes",
            InvestigationProbe.KUBERNETES_POD_STATE,
            kubernetes_result(
                cluster=WRONG_CLUSTER
            ),
        ),
        (
            "_normalize_prometheus",
            InvestigationProbe.PROMETHEUS_RESTART_COUNT,
            prometheus_result(
                cluster=WRONG_CLUSTER
            ),
        ),
    ],
)
def test_explicit_tool_cluster_mismatch_is_rejected_before_evidence(
    normalizer_name,
    probe,
    result,
):
    executor = (
        ReadOnlyInvestigationProbeExecutor()
    )

    normalizer = getattr(
        executor,
        normalizer_name,
    )

    with pytest.raises(
        InvestigationProbeResponseError,
        match=(
            "cluster does not match trusted scope"
        ),
    ):
        normalizer(
            scope=scope(),
            probe=probe,
            result=result,
        )


def test_identityless_legacy_prometheus_evidence_remains_compatible_but_unverified():
    evidence = (
        ReadOnlyInvestigationProbeExecutor()
        ._normalize_prometheus(
            scope=scope(),
            probe=(
                InvestigationProbe
                .PROMETHEUS_RESTART_COUNT
            ),
            result=prometheus_result(
                cluster=None
            ),
        )
    )

    assert evidence.success is True
    assert evidence.trusted is True
    assert evidence.cluster is None
    assert (
        evidence.cluster_verified
        is False
    )


def test_cluster_verified_model_requires_trusted_cluster_identity():
    with pytest.raises(
        ValueError,
        match=(
            "cluster-verified evidence requires a cluster identity"
        ),
    ):
        EvidenceItem(
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
            cluster_verified=True,
            facts={},
        )


class TwoStepReasoner(
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
            hypothesis_id="cluster-contract",
            cause="collect bounded evidence",
            confidence=0.1,
            supporting_evidence_ids=[],
            conflicting_evidence_ids=[],
            missing_evidence=[
                "root cause evidence"
            ],
            optional_evidence=[],
        )

        if self.calls == 1:
            return InvestigationDecision(
                hypotheses=[
                    hypothesis
                ],
                rationale_summary=(
                    "collect Kubernetes evidence"
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
                "stop after cluster integrity check"
            ),
            stop=True,
            stop_reason=(
                InvestigationStopReason
                .INSUFFICIENT_EVIDENCE
            ),
            next_probe=None,
            conclusion=None,
        )


class ForgedMismatchProbeExecutor:
    async def collect(
        self,
        context,
        scope,
        probe,
    ) -> EvidenceItem:
        return EvidenceItem(
            evidence_id="forged-cluster-evidence",
            probe=probe,
            source="forged-read-tool",
            success=True,
            trusted=True,
            production_signal=True,
            reliability=1.0,
            observed_at=NOW,
            cluster=WRONG_CLUSTER,
            cluster_verified=False,
            facts={
                "ready": True,
            },
        )


@pytest.mark.asyncio
async def test_coordinator_replaces_custom_executor_mismatch_before_reasoner_reuse():
    event = SimpleNamespace(
        header=SimpleNamespace(
            occurred_at=NOW,
        ),
        signal=SimpleNamespace(
            name="PodRestartHigh",
            message="restart rate elevated",
        ),
        resources=[
            SimpleNamespace(
                name=(
                    "device-gateway-xyz789"
                ),
                namespace="fleet-edge",
                cluster=INCIDENT_CLUSTER,
            )
        ],
    )

    context = SimpleNamespace(
        event=event,
        metadata={},
    )

    coordinator = (
        EvidenceDrivenInvestigationCoordinator(
            reasoner=TwoStepReasoner(),
            probe_executor=(
                ForgedMismatchProbeExecutor()
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
        context
    )

    assert len(
        result.evidence
    ) == 1

    rejected = result.evidence[
        0
    ]

    assert rejected.success is False
    assert rejected.trusted is False

    assert rejected.error_code == (
        "ClusterEvidenceMismatch"
    )

    assert rejected.cluster is None
    assert rejected.facts == {}


def build_plan() -> ActionPlan:
    return ActionPlan(
        type=(
            ActionType
            .INCREASE_MEMORY_LIMIT
        ),
        target=(
            "device-gateway-xyz789"
        ),
        risk=ActionRisk.MEDIUM,
        metadata={},
    )


def test_verification_profile_routes_cluster_to_kubernetes_and_prometheus_tools():
    profile = (
        VerificationProfileFactory()
        .create(
            build_plan(),
            namespace="fleet-edge",
            cluster=INCIDENT_CLUSTER,
        )
    )

    assert profile.cluster == (
        INCIDENT_CLUSTER
    )

    for probe in profile.probes:
        assert (
            probe.arguments.get(
                "cluster"
            )
            == INCIDENT_CLUSTER
        )


class OneResponseTools:
    def __init__(
        self,
        response,
    ) -> None:
        self.response = response
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

        return self.response


def metric_probe(
    evaluator,
) -> VerificationProbe:
    return VerificationProbe(
        name="cluster_metric",
        source=VerificationSource.METRIC,
        tool="prometheus",
        provider="prometheus",
        arguments={
            "query": (
                'up{cluster="prod-us-03"}'
            ),
            "cluster": INCIDENT_CLUSTER,
        },
        evaluator=evaluator,
        required=True,
    )


@pytest.mark.asyncio
async def test_verification_collector_rejects_explicit_cluster_mismatch_before_evaluator():
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

    tools = OneResponseTools(
        prometheus_result(
            cluster=WRONG_CLUSTER
        )
    )

    collector = (
        VerificationEvidenceCollector(
            tools=tools,
            clock=lambda: NOW,
        )
    )

    check = await collector.collect_one(
        metric_probe(
            evaluator
        )
    )

    assert check.passed is None

    assert (
        check.metadata[
            "trusted"
        ]
        is False
    )

    assert (
        "cluster does not match expected scope"
        in check.metadata[
            "rejection_reasons"
        ]
    )

    assert evaluator_calls == []


@pytest.mark.asyncio
async def test_verification_collector_records_matching_cluster_as_verified():
    def evaluator(
        evidence,
    ):
        return VerificationEvaluation(
            passed=True,
            observed_value=7.0,
            expected_value=7.0,
            message="cluster matched",
        )

    collector = (
        VerificationEvidenceCollector(
            tools=OneResponseTools(
                prometheus_result()
            ),
            clock=lambda: NOW,
        )
    )

    check = await collector.collect_one(
        metric_probe(
            evaluator
        )
    )

    assert check.passed is True

    assert (
        check.metadata[
            "evidence_cluster"
        ]
        == INCIDENT_CLUSTER
    )

    assert (
        check.metadata[
            "cluster_verified"
        ]
        is True
    )


@pytest.mark.asyncio
async def test_verification_legacy_identityless_result_is_visible_as_unverified():
    def evaluator(
        evidence,
    ):
        return VerificationEvaluation(
            passed=True,
            message="legacy compatible",
        )

    collector = (
        VerificationEvidenceCollector(
            tools=OneResponseTools(
                prometheus_result(
                    cluster=None
                )
            ),
            clock=lambda: NOW,
        )
    )

    check = await collector.collect_one(
        metric_probe(
            evaluator
        )
    )

    assert check.passed is True

    assert (
        check.metadata[
            "evidence_cluster"
        ]
        is None
    )

    assert (
        check.metadata[
            "cluster_verified"
        ]
        is False
    )
