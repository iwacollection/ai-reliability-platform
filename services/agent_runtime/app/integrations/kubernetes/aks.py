"""AKS/Kubernetes integration boundary."""

from dataclasses import dataclass


@dataclass
class KubernetesContext:
    cluster_name: str
    namespace: str | None = None


class AKSConnector:
    """Production Kubernetes investigation adapter boundary."""

    def __init__(self, context: KubernetesContext):
        self.context = context

    def list_workloads(self) -> list[dict]:
        return []

    def get_events(self) -> list[dict]:
        return []

    def describe_workload(self, name: str) -> dict:
        return {
            "cluster": self.context.cluster_name,
            "workload": name,
            "status": "adapter_ready",
        }
