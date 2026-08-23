from typing import Any


class KubernetesAPIClient:
    """Runtime Kubernetes API binding for MCP adapter.

    Keeps Kubernetes SDK usage isolated from Agent Runtime.
    A real AKS client can be injected through this boundary.
    """

    def __init__(self, api_client: Any = None):
        self.api_client = api_client

    def get_pods(self, request):
        if self.api_client is None:
            return {"status": "mock", "resource": "pods"}
        return self.api_client.list_pods(request.namespace)

    def get_events(self, request):
        if self.api_client is None:
            return {"status": "mock", "resource": "events"}
        return self.api_client.list_events(request.namespace)

    def describe_pod(self, request):
        if self.api_client is None:
            return {"status": "mock", "resource": request.resource}
        return self.api_client.describe_pod(request.namespace, request.resource)

    def get_logs(self, request):
        if self.api_client is None:
            return {"status": "mock", "resource": "logs"}
        return self.api_client.get_logs(request.namespace, request.resource)

    def get_nodes(self, request):
        if self.api_client is None:
            return {"status": "mock", "resource": "nodes"}
        return self.api_client.list_nodes()

    def get_deployments(self, request):
        if self.api_client is None:
            return {"status": "mock", "resource": "deployments"}
        return self.api_client.list_deployments(request.namespace)
