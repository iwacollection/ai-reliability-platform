
class MCPEvidenceAdapter:
    def collect(self, request):
        return {
            "source": request.get("tool"),
            "evidence": {}
        }
