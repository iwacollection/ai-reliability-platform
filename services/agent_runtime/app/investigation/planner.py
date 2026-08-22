"""Investigation planner.

Transforms incidents into deterministic investigation steps.
"""

from dataclasses import dataclass


@dataclass
class InvestigationStep:
    tool: str
    action: str
    reason: str


class InvestigationPlanner:
    def create_plan(self, incident: dict) -> list[InvestigationStep]:
        signal = incident.get("signal", "unknown")

        steps = [
            InvestigationStep(
                tool="kubernetes",
                action="collect_resource_state",
                reason=f"inspect affected resource for {signal}",
            ),
            InvestigationStep(
                tool="prometheus",
                action="collect_metrics",
                reason="validate runtime symptoms with metrics",
            ),
        ]

        return steps
