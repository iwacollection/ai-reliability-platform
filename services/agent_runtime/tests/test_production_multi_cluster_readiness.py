from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.agent_runtime.app.investigation.models import (
    default_investigation_probes,
)
from services.agent_runtime.app.investigation.multi_cluster_readiness import (
    ProductionMultiClusterReadinessError,
    ProductionMultiClusterReadinessGate,
)
from services.agent_runtime.app.tools.factory import (
    create_tool_manager,
)
from services.agent_runtime.app.tools.kubernetes.router import (
    KubernetesClusterRegistry,
)
from services.agent_runtime.app.tools.kubernetes.tool import (
    KubernetesTool,
)
from services.agent_runtime.app.tools.prometheus.router import (
    PrometheusClusterRegistry,
)
from services.agent_runtime.app.tools.prometheus.tool import (
    PrometheusTool,
)


CLUSTER = "prod-us-03"
SECOND_CLUSTER = "prod-sg-17"


def kubernetes_tool(
    cluster=CLUSTER,
    *,
    fallback=False,
):
    return KubernetesTool(
        api_url=(
            f"https://{cluster}.kubernetes.test"
        ),
        cluster_name=cluster,
        bearer_token=(
            f"{cluster}-read-token-1234567890"
        ),
        verify_tls=True,
        allow_dry_run_fallback=fallback,
    )


def prometheus_tool(
    endpoint="central",
):
    return PrometheusTool(
        base_url=(
            f"https://{endpoint}.prometheus.test"
        ),
        bearer_token="",
        verify_tls=True,
        allow_mock_fallback=False,
    )


def complete_gate(
    *,
    clusters=(CLUSTER,),
):
    kubernetes_registry = (
        KubernetesClusterRegistry(
            [
                kubernetes_tool(
                    cluster
                )
                for cluster in clusters
            ]
        )
    )

    central = prometheus_tool()

    prometheus_registry = (
        PrometheusClusterRegistry(
            {
                cluster: central
                for cluster in clusters
            }
        )
    )

    manager = create_tool_manager(
        kubernetes_cluster_registry=(
            kubernetes_registry
        ),
        prometheus_cluster_registry=(
            prometheus_registry
        ),
    )

    return (
        ProductionMultiClusterReadinessGate(
            kubernetes_cluster_registry=(
                kubernetes_registry
            ),
            prometheus_cluster_registry=(
                prometheus_registry
            ),
            tools=manager,
            strict_evidence_required=True,
        ),
        kubernetes_registry,
        prometheus_registry,
    )


def event(
    *clusters,
):
    return SimpleNamespace(
        resources=[
            SimpleNamespace(
                cluster=cluster
            )
            for cluster in clusters
        ]
    )


def test_complete_cluster_coverage_is_ready_without_network():
    gate, _, _ = complete_gate()

    report = gate.evaluate(
        CLUSTER
    )

    assert report.ready is True

    assert (
        report.kubernetes_route_ready
        is True
    )

    assert (
        report.kubernetes_change_route_ready
        is True
    )

    assert (
        report.prometheus_route_ready
        is True
    )

    assert set(
        report.covered_investigation_probes
    ) == {
        probe.value
        for probe
        in default_investigation_probes()
    }

    assert (
        report.covered_verification_tools
        == (
            "kubernetes",
            "prometheus",
        )
    )

    assert report.issues == ()


def test_complete_central_prometheus_binding_can_cover_multiple_clusters():
    gate, _, prometheus_registry = (
        complete_gate(
            clusters=(
                CLUSTER,
                SECOND_CLUSTER,
            )
        )
    )

    first = prometheus_registry.resolve(
        CLUSTER
    )[
        1
    ]

    second = prometheus_registry.resolve(
        SECOND_CLUSTER
    )[
        1
    ]

    assert first is second

    coverage = gate.evaluate_all()

    assert coverage.ready is True

    assert {
        item.cluster
        for item in coverage.clusters
    } == {
        CLUSTER,
        SECOND_CLUSTER,
    }


def test_partial_registry_coverage_is_not_ready():
    kubernetes_registry = (
        KubernetesClusterRegistry(
            [
                kubernetes_tool()
            ]
        )
    )

    manager = create_tool_manager(
        kubernetes_cluster_registry=(
            kubernetes_registry
        )
    )

    gate = ProductionMultiClusterReadinessGate(
        kubernetes_cluster_registry=(
            kubernetes_registry
        ),
        prometheus_cluster_registry=None,
        tools=manager,
        strict_evidence_required=True,
    )

    report = gate.evaluate(
        CLUSTER
    )

    assert report.ready is False

    assert (
        "prometheus_registry_missing"
        in report.issues
    )

    assert (
        "required_investigation_probe_coverage_incomplete"
        in report.issues
    )

    assert (
        "required_verification_tool_coverage_incomplete"
        in report.issues
    )


def test_registry_cluster_sets_must_cover_the_same_incident_clusters():
    kubernetes_registry = (
        KubernetesClusterRegistry(
            [
                kubernetes_tool(
                    CLUSTER
                ),
                kubernetes_tool(
                    SECOND_CLUSTER
                ),
            ]
        )
    )

    prometheus_registry = (
        PrometheusClusterRegistry(
            {
                CLUSTER: prometheus_tool(),
            }
        )
    )

    manager = create_tool_manager(
        kubernetes_cluster_registry=(
            kubernetes_registry
        ),
        prometheus_cluster_registry=(
            prometheus_registry
        ),
    )

    gate = ProductionMultiClusterReadinessGate(
        kubernetes_cluster_registry=(
            kubernetes_registry
        ),
        prometheus_cluster_registry=(
            prometheus_registry
        ),
        tools=manager,
        strict_evidence_required=True,
    )

    coverage = gate.evaluate_all()

    assert coverage.ready is False

    assert (
        "cluster_registry_coverage_sets_differ"
        in coverage.issues
    )

    second = [
        item
        for item in coverage.clusters
        if item.cluster
        == SECOND_CLUSTER
    ][
        0
    ]

    assert second.ready is False

    assert (
        "prometheus_cluster_binding_missing"
        in second.issues
    )


def test_runtime_tool_manager_must_use_the_exact_registry_routers():
    gate, kubernetes_registry, prometheus_registry = (
        complete_gate()
    )

    legacy_manager = create_tool_manager()

    broken = ProductionMultiClusterReadinessGate(
        kubernetes_cluster_registry=(
            kubernetes_registry
        ),
        prometheus_cluster_registry=(
            prometheus_registry
        ),
        tools=legacy_manager,
        strict_evidence_required=True,
    )

    report = broken.evaluate(
        CLUSTER
    )

    assert report.ready is False

    assert (
        "kubernetes_runtime_router_mismatch"
        in report.issues
    )

    assert (
        "kubernetes_change_runtime_router_mismatch"
        in report.issues
    )

    assert (
        "prometheus_runtime_router_mismatch"
        in report.issues
    )


def test_kubernetes_dry_run_fallback_blocks_production_readiness():
    kubernetes_registry = (
        KubernetesClusterRegistry(
            [
                kubernetes_tool(
                    fallback=True
                )
            ]
        )
    )

    prometheus_registry = (
        PrometheusClusterRegistry(
            {
                CLUSTER: prometheus_tool(),
            }
        )
    )

    manager = create_tool_manager(
        kubernetes_cluster_registry=(
            kubernetes_registry
        ),
        prometheus_cluster_registry=(
            prometheus_registry
        ),
    )

    gate = ProductionMultiClusterReadinessGate(
        kubernetes_cluster_registry=(
            kubernetes_registry
        ),
        prometheus_cluster_registry=(
            prometheus_registry
        ),
        tools=manager,
        strict_evidence_required=True,
    )

    report = gate.evaluate(
        CLUSTER
    )

    assert report.ready is False

    assert (
        "kubernetes_dry_run_fallback_must_be_disabled"
        in report.issues
    )


def test_strict_evidence_policy_is_required_for_ready_state():
    gate, kubernetes_registry, prometheus_registry = (
        complete_gate()
    )

    not_strict = ProductionMultiClusterReadinessGate(
        kubernetes_cluster_registry=(
            kubernetes_registry
        ),
        prometheus_cluster_registry=(
            prometheus_registry
        ),
        tools=gate.tools,
        strict_evidence_required=False,
    )

    report = not_strict.evaluate(
        CLUSTER
    )

    assert report.ready is False

    assert (
        "cluster_verified_evidence_policy_inactive"
        in report.issues
    )


@pytest.mark.parametrize(
    "clusters",
    [
        (),
        (
            CLUSTER,
            SECOND_CLUSTER,
        ),
    ],
)
def test_event_scope_requires_one_exact_cluster(
    clusters,
):
    gate, _, _ = complete_gate(
        clusters=(
            CLUSTER,
            SECOND_CLUSTER,
        )
    )

    report = gate.evaluate_event(
        event(
            *clusters
        )
    )

    assert report.ready is False

    if not clusters:
        assert any(
            item
            in report.issues
            for item in (
                "incident_resource_missing",
                "incident_cluster_missing",
            )
        )
    else:
        assert (
            "incident_cluster_ambiguous"
            in report.issues
        )


def test_assert_event_ready_raises_sanitized_error_only():
    kubernetes_registry = (
        KubernetesClusterRegistry(
            [
                kubernetes_tool()
            ]
        )
    )

    manager = create_tool_manager(
        kubernetes_cluster_registry=(
            kubernetes_registry
        )
    )

    gate = ProductionMultiClusterReadinessGate(
        kubernetes_cluster_registry=(
            kubernetes_registry
        ),
        prometheus_cluster_registry=None,
        tools=manager,
        strict_evidence_required=True,
    )

    with pytest.raises(
        ProductionMultiClusterReadinessError,
        match="read coverage is not ready",
    ) as captured:
        gate.assert_event_ready(
            event(
                CLUSTER
            )
        )

    text = str(
        captured.value
    )

    assert "https://" not in text
    assert "token" not in text.lower()


def test_readiness_snapshot_contains_no_endpoint_or_credential_values():
    gate, _, _ = complete_gate()

    snapshot = gate.evaluate(
        CLUSTER
    ).snapshot()

    text = str(
        snapshot
    )

    assert (
        "kubernetes.test"
        not in text
    )

    assert (
        "prometheus.test"
        not in text
    )

    assert (
        "read-token"
        not in text
    )

    assert snapshot[
        "read_only"
    ] is True

    assert snapshot[
        "decision_influence"
    ] is False
