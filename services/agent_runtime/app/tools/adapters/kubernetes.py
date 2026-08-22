"""Kubernetes investigation adapter.

Read-only foundation for Investigation Agent tools.
Production adapters can bind this interface to kubernetes client/MCP servers.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class KubernetesEvidence:
    kind: str
    resource: str
    payload: dict[str, Any]


class KubernetesToolAdapter:
    name = "kubernetes"
    permission = "readonly"

    def get_pod_status(self, namespace: str, pod: str) -> KubernetesEvidence:
        return KubernetesEvidence(
            kind="pod_status",
            resource=f"{namespace}/{pod}",
            payload={"status": "mock", "namespace": namespace, "pod": pod},
        )

    def get_events(self, namespace: str, resource: str) -> KubernetesEvidence:
        return KubernetesEvidence(
            kind="kubernetes_event",
            resource=resource,
            payload={"events": []},
        )
