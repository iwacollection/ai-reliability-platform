
class LokiAdapter:
    def collect(self, query):
        return {
            "tool": "loki",
            "query": query,
            "evidence": {}
        }
