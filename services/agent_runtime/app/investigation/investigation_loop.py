from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class InvestigationContext:
    incident_id: str
    hypothesis: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)


class InvestigationLoop:
    """Controlled observe -> collect -> update investigation loop."""

    def __init__(self, planner: Callable[[InvestigationContext], list[dict[str, Any]]]):
        self.planner = planner

    def run(self, context: InvestigationContext, executor: Callable[[dict[str, Any]], Any]):
        steps = self.planner(context)

        results = []
        for step in steps:
            result = executor(step)
            results.append(result)
            context.completed_steps.append(step.get("action", "unknown"))

        return results
