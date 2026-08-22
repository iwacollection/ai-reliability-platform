
class AdaptivePlanner:
    def __init__(self, strategy_registry):
        self.strategy_registry = strategy_registry

    def create_plan(self, incident):
        return {
            "incident": incident,
            "strategies": self.strategy_registry.get_for_incident(incident),
            "steps": [
                "collect_context",
                "collect_evidence",
                "analyze"
            ]
        }
