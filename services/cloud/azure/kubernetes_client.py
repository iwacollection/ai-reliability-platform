from typing import Any


class AKSKubernetesClient:
    """Real AKS Kubernetes client boundary.

    Azure authentication and Kubernetes SDK are isolated here so MCP
    adapters do not directly manage cloud credentials.
    """

    def __init__(self, kube_client: Any = None):
        self.kube_client = kube_client

    def list_pods(self, namespace: str):
        if self.kube_client is None:
            return {"status": "not_connected", "resource": "pods", "namespace": namespace}
        return self.kube_client.list_namespaced_pod(namespace)

    def list_events(self, namespace: str):
        if self.kube_client is None:
            return {"status": "not_connected", "resource": "events", "namespace": namespace}
        return self.kube_client.list_namespaced_event(namespace)

    def list_nodes(self):
        if self.kube_client is None:
            return {"status": "not_connected", "resource": "nodes"}
        return self.kube_client.list_node()

    def list_deployments(self, namespace: str):
        if self.kube_client is None:
            return {"status": "not_connected", "resource": "deployments", "namespace": namespace}
        return self.kube_client.list_namespaced_deployment(namespace)
