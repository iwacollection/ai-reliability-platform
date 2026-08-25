from typing import Any

from services.agent_runtime.app.discovery.models import DiscoveryObservation
from services.agent_runtime.app.discovery.source import DiscoverySource
from services.connectors.kubernetes.client import KubernetesConnector
from services.connectors.kubernetes.evidence_adapter import KubernetesEvidenceAdapter


class KubernetesDiscoverySource(DiscoverySource):
    """Collect read-only Kubernetes state for proactive discovery."""

    def __init__(self, connector: KubernetesConnector):
        self.connector = connector
        self.adapter = KubernetesEvidenceAdapter()

    @property
    def name(self) -> str:
        return "kubernetes"

    async def collect(self) -> list[DiscoveryObservation]:
        observations: list[DiscoveryObservation] = []
        namespace = self.connector.config.namespace

        for pod in self.connector.list_pods(namespace):
            observations.append(self._pod_observation(pod))

        for event in self.connector.list_events(namespace):
            observations.append(self._event_observation(event))

        for node in self.connector.list_nodes():
            observations.append(self._node_observation(node))

        for deployment in self.connector.list_deployments(namespace):
            observations.append(self._deployment_observation(deployment))

        return observations

    def _resource(self, raw: dict[str, Any], kind: str) -> dict[str, Any]:
        metadata = raw.get("metadata", {})
        return {
            "kind": kind,
            "name": metadata.get("name", "unknown"),
            "namespace": metadata.get("namespace"),
            "cluster": self.connector.config.context,
            "labels": metadata.get("labels", {}),
        }

    def _pod_observation(self, pod: dict[str, Any]) -> DiscoveryObservation:
        evidence = self.adapter.pod_to_evidence(pod)
        containers = evidence.signal.get("containers", [])
        restart_count = max((int(item.get("restartCount") or 0) for item in containers), default=0)
        waiting_reason = None
        last_termination_reason = None

        for item in containers:
            state = item.get("state") or {}
            reason = (state.get("waiting") or {}).get("reason")
            if reason:
                waiting_reason = reason
            if item.get("lastTerminationReason"):
                last_termination_reason = item["lastTerminationReason"]

        return DiscoveryObservation(
            source="kubernetes",
            kind="Pod",
            resource=self._resource(pod, "Pod"),
            signal={
                "phase": evidence.signal.get("phase"),
                "restart_count": restart_count,
                "waiting_reason": waiting_reason,
                "last_termination_reason": last_termination_reason,
                "containers": containers,
            },
        )

    def _event_observation(self, event: dict[str, Any]) -> DiscoveryObservation:
        evidence = self.adapter.event_to_evidence(event)
        involved = event.get("involvedObject", {})
        metadata = event.get("metadata", {})
        return DiscoveryObservation(
            source="kubernetes",
            kind="Event",
            resource={
                "kind": involved.get("kind", "Custom"),
                "name": involved.get("name", evidence.resource),
                "namespace": involved.get("namespace") or metadata.get("namespace"),
                "cluster": self.connector.config.context,
            },
            signal=evidence.signal,
        )

    def _node_observation(self, node: dict[str, Any]) -> DiscoveryObservation:
        evidence = self.adapter.node_to_evidence(node)
        conditions = evidence.signal.get("conditions", [])
        active_conditions = {
            str(condition.get("type")): str(condition.get("status")).lower() == "true"
            for condition in conditions
        }
        return DiscoveryObservation(
            source="kubernetes",
            kind="Node",
            resource=self._resource(node, "Node"),
            signal={**active_conditions, "conditions": conditions},
        )

    def _deployment_observation(self, deployment: dict[str, Any]) -> DiscoveryObservation:
        evidence = self.adapter.deployment_to_evidence(deployment)
        return DiscoveryObservation(
            source="kubernetes",
            kind="Deployment",
            resource=self._resource(deployment, "Deployment"),
            signal={
                "desired_replicas": evidence.metadata.get("desiredReplicas", 0),
                "available_replicas": evidence.signal.get("availableReplicas", 0),
                "ready_replicas": evidence.signal.get("readyReplicas", 0),
                "rollout": evidence.signal.get("rollout", {}),
            },
        )
