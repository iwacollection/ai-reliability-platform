from services.connectors.kubernetes import KubernetesEvidenceAdapter


class KubernetesEvidenceCollector:
    """Bridge Kubernetes connector output into evidence pipeline."""

    def __init__(self):
        self.adapter = KubernetesEvidenceAdapter()

    def collect_pod(self, pod):
        return self.adapter.pod_to_evidence(pod)

    def collect_event(self, event):
        return self.adapter.event_to_evidence(event)

    def collect_node(self, node):
        return self.adapter.node_to_evidence(node)

    def collect_deployment(self, deployment):
        return self.adapter.deployment_to_evidence(deployment)
