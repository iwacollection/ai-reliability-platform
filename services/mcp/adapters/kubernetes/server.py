from .tools import KubernetesMCPTools


class KubernetesMCPServerAdapter:
    """MCP server adapter exposing Kubernetes investigation tools."""

    def __init__(self, tools: KubernetesMCPTools | None = None):
        self.tools = tools or KubernetesMCPTools()

    def list_tools(self):
        return [
            "get_pods",
            "get_events",
            "describe_pod",
            "get_logs",
            "get_nodes",
            "get_deployments",
        ]

    def call(self, request):
        return self.tools.execute(request)
