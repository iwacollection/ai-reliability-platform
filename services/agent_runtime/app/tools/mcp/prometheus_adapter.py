
class PrometheusAdapter:
    def collect(self, query):
        return {
            "tool": "prometheus",
            "query": query,
            "evidence": {}
        }
