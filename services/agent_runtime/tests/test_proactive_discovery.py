import asyncio

from common.domain.event.enums import EventSource, ResourceKind
from services.agent_runtime.app.discovery.detector import DiscoveryDetector
from services.agent_runtime.app.discovery.event_bridge import finding_to_standard_event
from services.agent_runtime.app.discovery.kubernetes_source import KubernetesDiscoverySource
from services.agent_runtime.app.discovery.models import DiscoveryObservation
from services.agent_runtime.app.discovery.runtime import ProactiveDiscoveryRuntime
from services.agent_runtime.app.discovery.source import StaticDiscoverySource
from services.connectors.kubernetes.client import KubernetesConnectorConfig


def _observation(kind: str, signal: dict, *, name: str = "workload") -> DiscoveryObservation:
    return DiscoveryObservation(
        source="kubernetes",
        kind=kind,
        resource={
            "kind": "Pod" if kind.lower() in {"pod", "event"} else kind,
            "name": name,
            "namespace": "default",
            "cluster": "test-cluster",
        },
        signal=signal,
    )


def test_detector_finds_crashloop_and_oom() -> None:
    detector = DiscoveryDetector()
    observation = _observation(
        "Pod",
        {
            "waiting_reason": "CrashLoopBackOff",
            "restart_count": 8,
            "last_termination_reason": "OOMKilled",
        },
        name="api-0",
    )

    findings = detector.evaluate(observation)

    assert {finding.rule_id for finding in findings} == {
        "k8s.pod.crashloop",
        "k8s.pod.oomkilled",
    }
    assert all(finding.score >= 0.9 for finding in findings)


def test_detector_finds_image_pull_node_pressure_and_replica_deficit() -> None:
    detector = DiscoveryDetector()

    image_findings = detector.evaluate(
        _observation("Event", {"reason": "ImagePullBackOff"}, name="worker-0")
    )
    node_findings = detector.evaluate(
        _observation("Node", {"MemoryPressure": True}, name="node-a")
    )
    deployment_findings = detector.evaluate(
        _observation(
            "Deployment",
            {"desired_replicas": 3, "available_replicas": 1},
            name="checkout",
        )
    )

    assert image_findings[0].rule_id == "k8s.image.pull_failure"
    assert node_findings[0].rule_id == "k8s.node.pressure"
    assert deployment_findings[0].rule_id == "k8s.deployment.replica_deficit"


def test_runtime_promotes_only_findings_above_threshold() -> None:
    observations = [
        _observation(
            "Deployment",
            {"desired_replicas": 3, "available_replicas": 2},
            name="checkout",
        ),
        _observation(
            "Pod",
            {"last_termination_reason": "OOMKilled"},
            name="api-0",
        ),
    ]
    promoted_rule_ids: list[str] = []

    runtime = ProactiveDiscoveryRuntime(
        StaticDiscoverySource(observations),
        min_score=0.9,
        sink=lambda finding: promoted_rule_ids.append(finding.rule_id),
    )

    batch = asyncio.run(runtime.scan())

    assert batch.scanned == 2
    assert len(batch.findings) == 2
    assert [finding.rule_id for finding in batch.promoted] == ["k8s.pod.oomkilled"]
    assert promoted_rule_ids == ["k8s.pod.oomkilled"]
    assert batch.promoted[0].should_investigate is True


def test_promoted_finding_converts_to_standard_event() -> None:
    finding = DiscoveryDetector().evaluate(
        _observation(
            "Pod",
            {
                "waiting_reason": "CrashLoopBackOff",
                "restart_count": 7,
            },
            name="payments-0",
        )
    )[0]

    event = finding_to_standard_event(finding)

    assert event.header.source == EventSource.KUBERNETES
    assert event.signal.name == "k8s.pod.crashloop"
    assert event.signal.labels["discovery"] == "proactive"
    assert event.resources[0].kind == ResourceKind.POD
    assert event.resources[0].name == "payments-0"


def test_kubernetes_source_normalizes_readonly_connector_state() -> None:
    class FakeConnector:
        config = KubernetesConnectorConfig(context="dev-cluster", namespace="default")

        def list_pods(self, namespace=None):
            return [
                {
                    "metadata": {"name": "api-0", "namespace": "default"},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {
                                "name": "api",
                                "restartCount": 6,
                                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                "lastState": {"terminated": {"reason": "Error"}},
                            }
                        ],
                    },
                }
            ]

        def list_events(self, namespace=None):
            return []

        def list_nodes(self):
            return []

        def list_deployments(self, namespace=None):
            return []

    source = KubernetesDiscoverySource(FakeConnector())
    observations = asyncio.run(source.collect())
    findings = DiscoveryDetector().evaluate(observations[0])

    assert observations[0].resource["cluster"] == "dev-cluster"
    assert observations[0].signal["restart_count"] == 6
    assert findings[0].rule_id == "k8s.pod.crashloop"
