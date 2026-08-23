from services.cloud.azure.kubernetes_client import AKSKubernetesClient


class AKSKubernetesToolBinding:
    """Connect Kubernetes MCP tools with real AKS runtime client."""

    def __init__(self, client: AKSKubernetesClient):
        self.client = client

    def get_pods(self, namespace: str):
        return self.client.list_pods(namespace)

    def get_events(self, namespace: str):
        return self.client.list_events(namespace)

    def get_nodes(self):
        return self.client.list_nodes()

    def get_deployments(self, namespace: str):
        return self.client.list_deployments(namespace)
