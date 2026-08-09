from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.agent_runtime.app.investigation.live_readiness import (
    ProductionReadinessLiveProbe,
    ProductionReadinessLiveProbeError,
)
from services.agent_runtime.app.investigation.multi_cluster_readiness import (
    ProductionMultiClusterReadinessGate,
)
from services.agent_runtime.app.tools.factory import create_tool_manager
from services.agent_runtime.app.tools.kubernetes.router import (
    KubernetesClusterRegistry,
)
from services.agent_runtime.app.tools.kubernetes.tool import KubernetesTool
from services.agent_runtime.app.tools.prometheus.router import (
    PrometheusClusterRegistry,
)
from services.agent_runtime.app.tools.prometheus.tool import PrometheusTool


CLUSTER = "prod-us-03"


class RecordingKubernetesTool(KubernetesTool):
    def __init__(self, *, response_cluster=CLUSTER, delay=0.0, fail=False):
        super().__init__(
            api_url="https://prod-us-03.kubernetes.test",
            cluster_name=CLUSTER,
            bearer_token="prod-us-read-token-1234567890",
            verify_tls=True,
            allow_dry_run_fallback=False,
        )
        self.response_cluster = response_cluster
        self.delay = delay
        self.fail = fail
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("https://secret.example/token-value")
        return {
            "success": True,
            "source": "kubernetes",
            "mode": "read_only",
            "production_signal": True,
            "cluster": self.response_cluster,
            "data": {"phase": "Running"},
        }


class RecordingPrometheusTool(PrometheusTool):
    def __init__(self, *, response_cluster=CLUSTER, delay=0.0, fail=False):
        super().__init__(
            base_url="https://central.prometheus.test",
            bearer_token="",
            verify_tls=True,
            allow_mock_fallback=False,
        )
        self.response_cluster = response_cluster
        self.delay = delay
        self.fail = fail
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("https://secret.prometheus/token-value")
        return {
            "success": True,
            "source": "prometheus",
            "mode": "read_only",
            "production_signal": True,
            "cluster": self.response_cluster,
            "data": {"resultType": "vector", "result": []},
        }


def build_probe(*, kubernetes=None, prometheus=None, timeout_seconds=1.0):
    kubernetes = kubernetes or RecordingKubernetesTool()
    prometheus = prometheus or RecordingPrometheusTool()

    kubernetes_registry = KubernetesClusterRegistry([kubernetes])
    prometheus_registry = PrometheusClusterRegistry({CLUSTER: prometheus})

    tools = create_tool_manager(
        kubernetes_cluster_registry=kubernetes_registry,
        prometheus_cluster_registry=prometheus_registry,
    )

    gate = ProductionMultiClusterReadinessGate(
        kubernetes_cluster_registry=kubernetes_registry,
        prometheus_cluster_registry=prometheus_registry,
        tools=tools,
        strict_evidence_required=True,
    )

    return (
        ProductionReadinessLiveProbe(
            readiness_gate=gate,
            tools=tools,
            timeout_seconds=timeout_seconds,
        ),
        kubernetes,
        prometheus,
    )


def event(*, cluster=CLUSTER, namespace="fleet-edge", name="device-gateway-xyz789"):
    return SimpleNamespace(
        resources=[
            SimpleNamespace(
                cluster=cluster,
                namespace=namespace,
                name=name,
            )
        ]
    )


@pytest.mark.asyncio
async def test_live_probe_requires_exact_acknowledgement_before_any_tool_call():
    probe, kubernetes, prometheus = build_probe()
    with pytest.raises(
        ProductionReadinessLiveProbeError,
        match="acknowledgement",
    ):
        await probe.probe_event(
            event(),
            acknowledgement="WRONG",
            reason="operator preflight",
        )
    assert kubernetes.calls == []
    assert prometheus.calls == []


@pytest.mark.asyncio
async def test_live_probe_requires_non_empty_reason_before_any_tool_call():
    probe, kubernetes, prometheus = build_probe()
    with pytest.raises(
        ProductionReadinessLiveProbeError,
        match="reason",
    ):
        await probe.probe_event(
            event(),
            acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,
            reason="",
        )
    assert kubernetes.calls == []
    assert prometheus.calls == []


@pytest.mark.asyncio
async def test_live_probe_runs_exact_bounded_reads_for_ready_incident_scope():
    probe, kubernetes, prometheus = build_probe()

    report = await probe.probe_event(
        event(),
        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,
        reason="pre-production connectivity proof",
    )

    assert report.ready is True
    assert report.kubernetes_probe_ready is True
    assert report.prometheus_probe_ready is True

    assert kubernetes.calls == [
        {
            "cluster": CLUSTER,
            "action": "get",
            "resource": "pod",
            "target": "device-gateway-xyz789",
            "namespace": "fleet-edge",
        }
    ]
    assert len(prometheus.calls) == 1
    assert prometheus.calls[0]["query"] == (
        'count(up{cluster="prod-us-03"})'
    )


@pytest.mark.asyncio
async def test_live_probe_rejects_cluster_mismatch_without_leaking_payload():
    probe, _, _ = build_probe(
        prometheus=RecordingPrometheusTool(
            response_cluster="prod-sg-17"
        )
    )

    report = await probe.probe_event(
        event(),
        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,
        reason="cluster proof",
    )

    assert report.ready is False
    assert report.kubernetes_probe_ready is True
    assert report.prometheus_probe_ready is False
    assert report.issues == ("prometheus_live_probe_failed",)

    text = str(report.snapshot())
    assert "prometheus.test" not in text
    assert "token-value" not in text


@pytest.mark.asyncio
async def test_live_probe_sanitizes_backend_exception_details():
    probe, _, _ = build_probe(
        kubernetes=RecordingKubernetesTool(fail=True)
    )

    report = await probe.probe_event(
        event(),
        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,
        reason="sanitized error proof",
    )

    assert report.ready is False
    assert report.issues == ("kubernetes_live_probe_failed",)

    text = str(report.snapshot())
    assert "https://" not in text
    assert "token-value" not in text


@pytest.mark.asyncio
async def test_live_probe_timeout_is_bounded_and_sanitized():
    probe, _, _ = build_probe(
        prometheus=RecordingPrometheusTool(delay=0.05),
        timeout_seconds=0.01,
    )

    report = await probe.probe_event(
        event(),
        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,
        reason="bounded timeout proof",
    )

    assert report.ready is False
    assert report.issues == ("prometheus_live_probe_failed",)


@pytest.mark.asyncio
async def test_static_readiness_failure_prevents_live_calls():
    probe, kubernetes, prometheus = build_probe()

    report = await probe.probe_event(
        event(cluster="prod-sg-17"),
        acknowledgement=ProductionReadinessLiveProbe.ACKNOWLEDGEMENT,
        reason="unknown cluster proof",
    )

    assert report.ready is False
    assert report.issues == ("static_readiness_not_ready",)
    assert kubernetes.calls == []
    assert prometheus.calls == []


def test_live_probe_source_contains_no_write_authority():
    from pathlib import Path
    import services.agent_runtime.app.investigation.live_readiness as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    forbidden = [
        "ActionRuntime",
        "ApprovalService",
        "KubernetesProductionExecutor",
        ".post(",
        ".patch(",
        ".put(",
        ".delete(",
    ]
    assert [item for item in forbidden if item in source] == []
