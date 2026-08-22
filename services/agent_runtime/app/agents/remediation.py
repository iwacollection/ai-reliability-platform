"""Remediation Agent foundation.

Converts RCA output into a controlled remediation plan.
Execution remains protected by approval and risk policy layers.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class RemediationPlan:
    incident_id: str
    actions: List[str] = field(default_factory=list)
    requires_approval: bool = True


class RemediationAgent:
    def create_plan(self, incident_id: str, root_cause: str) -> RemediationPlan:
        actions = []

        if "OOM" in root_cause or "memory" in root_cause.lower():
            actions.append("increase_memory_or_restart_workload")

        return RemediationPlan(
            incident_id=incident_id,
            actions=actions,
            requires_approval=True,
        )
