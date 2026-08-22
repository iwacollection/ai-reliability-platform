class EvidenceCollector:
    def collect(self, scenario, result):
        return {
            "scenario": scenario,
            "status": result.get("status"),
            "metrics": result.get("metrics", {}),
            "summary": result.get("summary", "")
        }
