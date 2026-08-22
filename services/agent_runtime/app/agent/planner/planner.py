
class InvestigationPlan:
    def __init__(self, incident_id, steps=None):
        self.incident_id = incident_id
        self.steps = steps or []


class AgentPlanner:
    def create_plan(self, incident):
        return InvestigationPlan(
            incident_id=getattr(incident, "id", "unknown"),
            steps=[
                "collect_context",
                "collect_evidence",
                "analyze_root_cause"
            ]
        )
