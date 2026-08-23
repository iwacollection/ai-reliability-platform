from dataclasses import dataclass, field
from typing import Any


@dataclass
class KubernetesEvidence:
    evidence_type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)


class KubernetesEvidenceCollector:
    """Convert Kubernetes MCP responses into investigation evidence."""

    def collect(self, tool_name: str, response: dict[str, Any]) -> KubernetesEvidence:
        return KubernetesEvidence(
            evidence_type=self._map_type(tool_name),
            source="kubernetes-mcp",
            payload=response,
        )

    def _map_type(self, tool_name: str) -> str:
        mapping = {
            "get_pods": "kubernetes.pod_state",
            "get_events": "kubernetes.events",
            "describe_pod": "kubernetes.pod_detail",
            "get_logs": "kubernetes.logs",
            "get_nodes": "kubernetes.node_health",
            "get_deployments": "kubernetes.deployment_state",
        }
        return mapping.get(tool_name, "kubernetes.unknown")
