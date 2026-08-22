"""AKS MCP investigation tool foundation."""


class AKSMCPTool:
    name = "aks"

    def get_pods(self, namespace: str):
        return {"namespace": namespace, "pods": []}

    def get_events(self, namespace: str):
        return {"namespace": namespace, "events": []}

    def describe_workload(self, name: str, namespace: str):
        return {"name": name, "namespace": namespace, "details": {}}
