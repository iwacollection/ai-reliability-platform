
class KubernetesAdapter:
    def collect(self, request):
        return {
            "tool": "kubernetes",
            "action": request.get("action"),
            "evidence": {}
        }
