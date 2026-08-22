
class InvestigationLoop:
    def execute(self, plan):
        return {
            "incident_id": plan.incident_id,
            "steps": plan.steps,
            "status": "READY_FOR_EVIDENCE"
        }
