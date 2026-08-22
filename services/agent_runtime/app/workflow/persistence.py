from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class WorkflowState:
    workflow_id: str
    status: str
    context: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowStore:
    def __init__(self):
        self.states: dict[str, WorkflowState] = {}

    def save(self, state: WorkflowState):
        self.states[state.workflow_id] = state

    def get(self, workflow_id: str):
        return self.states.get(workflow_id)
