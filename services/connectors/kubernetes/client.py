"""
Kubernetes connector runtime.

Provides read-only access for incident investigation.
No remediation actions are executed here.
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
        """Read pod metadata. Real kubernetes client wiring will be injected later."""
        return {
            "kind": "Pod",
            "name": name,
            "namespace": namespace or self.config.namespace,
            "mode": "readonly",
        }

    def get_events(self, namespace: str | None = None) -> list[dict[str, Any]]:
        return []
