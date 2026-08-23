"""
Kubernetes Evidence Adapter

Transform Kubernetes runtime objects into Evidence objects consumed by
Investigation Runtime.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class KubernetesEvidence:
    source: str
    kind: str
    resource: str
    signal: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class KubernetesEvidenceAdapter:
    """Read-only adapter for Kubernetes investigation evidence."""

    def pod_to_evidence(self, pod: dict[str, Any]) -> KubernetesEvidence:
        metadata = pod.get("metadata", {})
        spec = pod.get("spec", {})
        status = pod.get("status", {})

        containers = []
        for container in status.get("containerStatuses", []):
            containers.append(
                {
                    "name": container.get("name"),
                    "restartCount": container.get("restartCount", 0),
                    "state": container.get("state", {}),
                    "lastTerminationReason": (
                        container.get("lastState", {})
                        .get("terminated", {})
                        .get("reason")
                    ),
                }
            )

        return KubernetesEvidence(
            source="kubernetes",
            kind="pod",
            resource=metadata.get("name", "unknown"),
            signal={
                "phase": status.get("phase"),
                "containers": containers,
            },
            metadata={
                "namespace": metadata.get("namespace"),
                "labels": metadata.get("labels", {}),
                "ownerReferences": metadata.get("ownerReferences", []),
                "nodeName": spec.get("nodeName"),
            },
        )

    def event_to_evidence(self, event: dict[str, Any]) -> KubernetesEvidence:
        return KubernetesEvidence(
            source="kubernetes",
            kind="event",
            resource=event.get("involvedObject", {}).get("name", "unknown"),
            signal={
                "reason": event.get("reason"),
                "message": event.get("message"),
                "type": event.get("type"),
            },
        )

    def node_to_evidence(self, node: dict[str, Any]) -> KubernetesEvidence:
        metadata = node.get("metadata", {})
        status = node.get("status", {})
        return KubernetesEvidence(
            source="kubernetes",
            kind="node",
            resource=metadata.get("name", "unknown"),
            signal={
                "conditions": status.get("conditions", [])
            },
        )

    def deployment_to_evidence(self, deployment: dict[str, Any]) -> KubernetesEvidence:
        metadata = deployment.get("metadata", {})
        status = deployment.get("status", {})
        spec = deployment.get("spec", {})

        return KubernetesEvidence(
            source="kubernetes",
            kind="deployment",
            resource=metadata.get("name", "unknown"),
            signal={
                "replicas": status.get("replicas", 0),
                "availableReplicas": status.get("availableReplicas", 0),
                "readyReplicas": status.get("readyReplicas", 0),
                "rollout": status,
            },
            metadata={
                "desiredReplicas": spec.get("replicas", 0),
            },
        )
