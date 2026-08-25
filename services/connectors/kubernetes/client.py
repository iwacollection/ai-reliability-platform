"""Kubernetes read-only connector runtime.

The concrete Kubernetes SDK wiring is intentionally isolated behind this
connector so investigation and proactive discovery never need write access.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class KubernetesConnectorConfig:
    context: str | None = None
    namespace: str | None = None


class KubernetesConnector:
    def __init__(self, config: KubernetesConnectorConfig):
        self.config = config

    def get_pod(self, name: str, namespace: str | None = None) -> dict[str, Any]:
        """Read one pod. Real Kubernetes SDK wiring is injected at this boundary."""
        resolved_namespace = namespace or self.config.namespace
        return {
            "kind": "Pod",
            "name": name,
            "namespace": resolved_namespace,
            "metadata": {
                "name": name,
                "namespace": resolved_namespace,
            },
            "mode": "readonly",
        }

    def list_pods(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """List pods visible to the read-only connector."""
        return []

    def list_events(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """List Kubernetes events visible to the read-only connector."""
        return []

    def get_events(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """Backward-compatible alias used by the investigation path."""
        return self.list_events(namespace)

    def list_nodes(self) -> list[dict[str, Any]]:
        """List cluster nodes visible to the read-only connector."""
        return []

    def list_deployments(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """List deployments visible to the read-only connector."""
        return []
