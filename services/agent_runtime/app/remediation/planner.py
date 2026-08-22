from dataclasses import dataclass


@dataclass
class RemediationPlan:
    incident_id: str
    action: str
    risk_level: str
    requires_approval: bool = True


class RemediationPlanner:
    def build_plan(self, incident_id: str, root_cause: str) -> RemediationPlan:
        if "OOM" in root_cause or "memory" in root_cause.lower():
            return RemediationPlan(
                incident_id=incident_id,
                action="restart_unhealthy_pods",
                risk_level="medium",
                requires_approval=True,
            )

        return RemediationPlan(
            incident_id=incident_id,
            action="manual_investigation",
            risk_level="high",
            requires_approval=True,
        )
