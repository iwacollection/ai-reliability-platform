"""
Supervisor Agent

Coordinates specialist agents in the reliability runtime.
Future responsibilities:
- route incidents
- coordinate investigation/remediation agents
- enforce policy gates
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentTask:
    name: str
    payload: dict[str, Any]


class SupervisorAgent:
    def route(self, task: AgentTask) -> str:
        if task.name in {"incident", "investigation"}:
            return "investigator"
        if task.name in {"remediation", "healing"}:
            return "remediation"
        return "unknown"
