from .models import KubernetesToolRequest, KubernetesToolResponse


class KubernetesMCPTools:
    """Kubernetes MCP tool adapter.

    The first implementation keeps execution abstract so it can bind to
    AKS Kubernetes API client without coupling the Agent Runtime.
    """

    def __init__(self, client=None):
        self.client = client

    def execute(self, request: KubernetesToolRequest) -> KubernetesToolResponse:
        if request.tool == "get_pods":
            return self.get_pods(request)
        if request.tool == "get_events":
            return self.get_events(request)
        if request.tool == "describe_pod":
            return self.describe_pod(request)
        if request.tool == "get_logs":
            return self.get_logs(request)
        if request.tool == "get_nodes":
            return self.get_nodes(request)
        if request.tool == "get_deployments":
            return self.get_deployments(request)

        return KubernetesToolResponse(
            success=False,
            error=f"unsupported kubernetes tool: {request.tool}",
        )

    def _call(self, operation: str, request: KubernetesToolRequest):
        if self.client is None:
            return {
                "mode": "adapter",
                "operation": operation,
                "namespace": request.namespace,
            }
        return getattr(self.client, operation)(request)

    def get_pods(self, request):
        return KubernetesToolResponse(True, self._call("get_pods", request))

    def get_events(self, request):
        return KubernetesToolResponse(True, self._call("get_events", request))

    def describe_pod(self, request):
        return KubernetesToolResponse(True, self._call("describe_pod", request))

    def get_logs(self, request):
        return KubernetesToolResponse(True, self._call("get_logs", request))

    def get_nodes(self, request):
        return KubernetesToolResponse(True, self._call("get_nodes", request))

    def get_deployments(self, request):
        return KubernetesToolResponse(True, self._call("get_deployments", request))
