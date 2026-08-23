from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ActionEvent:
    incident_id: str
    action: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


ACTION_STARTED = "action_started"
ACTION_EXECUTING = "action_executing"
ACTION_COMPLETED = "action_completed"
ACTION_FAILED = "action_failed"
